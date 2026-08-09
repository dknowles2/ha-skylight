"""Data update coordinator for the Skylight integration."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from pyskylight import Skylight
from pyskylight.exceptions import AuthenticationError, NotAuthorizedError, SkylightError
from pyskylight.models import (
    CalendarEvent,
    Category,
    Chore,
    ChoreGroups,
    Device,
    Frame,
    Recipe,
    Reward,
    RewardPoint,
    SkylightList,
)

from .const import (
    CALENDAR_LOOKAHEAD,
    CONF_FRAMES,
    CURRENT_CHORE_BUCKETS,
    DOMAIN,
    REWARD_LOOKBACK,
    SCAN_INTERVAL,
    TOLERATED_FAILURES,
)

_LOGGER = logging.getLogger(__name__)

type SkylightConfigEntry = ConfigEntry[SkylightDataUpdateCoordinator]


def _current(groups: ChoreGroups) -> list[Chore]:
    """Return the `/chores/all` buckets that make up "now".

    `future` is left out so this covers the same span as `GET /chores`:
    overdue, due today, and undated.
    """
    return [chore for bucket in CURRENT_CHORE_BUCKETS for chore in groups.chores.get(bucket, [])]


def _unassigned_chores(groups: ChoreGroups) -> list[Chore]:
    """Return the chores the Skylight app shows under "Up for Grabs".

    These belong to nobody, and `GET /chores` never returns them whatever the
    date range — `/chores/all` is the only source.

    `Chore.unassigned` wants both the flag and an absent category: a `PUT`
    setting `up_for_grabs` alone returns 200 and changes nothing, so a chore can
    carry it while still belonging to someone.
    """
    return [chore for chore in _current(groups) if chore.unassigned]


def _merge_chores(charted: list[Chore], groups: ChoreGroups) -> list[Chore]:
    """Combine the two chore sources, because neither is complete on its own.

    `GET /chores` only returns chores belonging to a profile with
    `selected_for_chore_chart` set — a family member taken off the chart keeps
    their chores, and this endpoint stops admitting they exist. Verified on a
    test frame: a new profile's chores were invisible until the flag was set,
    then appeared immediately.

    `/chores/all` covers every profile regardless of the chart, but drops a
    chore the moment it is completed — verified the same way.

    So the charted chores come first, complete with what has been ticked off,
    and anything `/chores/all` knows about that they missed is added on. The
    gap that leaves is real and unavoidable: a chore completed today by someone
    who is not on the chore chart appears in neither source.
    """
    seen = {chore.id for chore in charted}
    return charted + [
        chore
        for chore in _current(groups)
        if chore.id not in seen and chore.category_id is not None
    ]


@dataclass
class FrameData:
    """Everything we poll for a single frame.

    Held as one object per frame so entities can be built from a single
    coordinator refresh without any further awaiting.
    """

    frame: Frame
    categories: list[Category] = field(default_factory=list)
    chores: list[Chore] = field(default_factory=list)
    unassigned_chores: list[Chore] = field(default_factory=list)
    rewards: list[Reward] = field(default_factory=list)
    reward_points: list[RewardPoint] = field(default_factory=list)
    lists: list[SkylightList] = field(default_factory=list)
    recipes: list[Recipe] = field(default_factory=list)
    calendar_events: list[CalendarEvent] = field(default_factory=list)
    devices: list[Device] = field(default_factory=list)
    hardware_model: str | None = None

    @property
    def devices_by_id(self) -> dict[str, Device]:
        """Return the frame's physical devices keyed by their resource id."""
        return {device.id: device for device in self.devices}

    @property
    def lists_by_id(self) -> dict[str, SkylightList]:
        """Return lists keyed by their resource id."""
        return {shopping_list.id: shopping_list for shopping_list in self.lists}

    @property
    def default_grocery_list(self) -> SkylightList | None:
        """Return the list Skylight puts recipe ingredients on.

        Not a choice the caller gets to make: `add_to_grocery_list` always
        targets the list carrying this flag, verified against a frame holding
        two shopping lists — the second stayed empty.
        """
        for skylight_list in self.lists:
            if skylight_list.default_grocery_list:
                return skylight_list
        return None

    def recipes_named(self, name: str) -> list[Recipe]:
        """Return every recipe whose name matches, ignoring case and padding.

        A recipe's name is its `summary`; there is no title field. Nothing stops
        a household having two of them called the same thing, so this returns
        all the matches and leaves the caller to object.
        """
        wanted = name.strip().casefold()
        return [recipe for recipe in self.recipes if (recipe.summary or "").casefold() == wanted]

    @property
    def profiles(self) -> list[Category]:
        """Return the categories that are actually family members.

        Skylight's "categories" mix people with calendar buckets: a shared
        `Family` calendar, a `Family Birthdays` feed, an `(unused)` leftover.
        Only a category linked to a family member is a person, and only a
        person can hold chores or reward points — the rest would produce
        entities that are permanently empty.
        """
        return [category for category in self.categories if category.linked_to_profile]

    @property
    def profiles_by_id(self) -> dict[str, Category]:
        """Return family profiles keyed by their resource id."""
        return {category.id: category for category in self.profiles}

    def chores_for(self, category_id: str) -> list[Chore]:
        """Return chores assigned to one family profile."""
        return [chore for chore in self.chores if chore.category_id == category_id]

    @property
    def available_rewards(self) -> list[Reward]:
        """Return the rewards that can still be redeemed.

        `respawn_on_redemption` does not reset a reward: Skylight mints a new
        resource and keeps the old one as a record of the redemption. So the
        full list is part catalogue, part history, and only the unredeemed part
        is actionable — building entities from all of it produced several
        identically named rewards, most of them already spent.

        The history is still fetched, because it is how a redemption is noticed
        at all: without it a redemption looks like a reward disappearing.
        """
        return [reward for reward in self.rewards if reward.redeemed_at is None]

    def points_for(self, category_id: str) -> RewardPoint | None:
        """Return the reward point balance for one family profile."""
        for point in self.reward_points:
            if str(point.category_id) == category_id:
                return point
        return None


class SkylightDataUpdateCoordinator(DataUpdateCoordinator[dict[str, FrameData]]):
    """Polls Skylight and hands entities a snapshot keyed by frame id."""

    config_entry: SkylightConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SkylightConfigEntry,
        client: Skylight,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        # Every frame the account owns, whether or not it is one the user chose
        # to expose: the options flow has to offer an excluded frame back, and
        # a frame filtered out here never reaches `data`.
        self.available_frames: dict[str, str] = {}
        # Static per frame, and only returned by the single-frame endpoint, so
        # it is fetched once rather than on every poll.
        self._hardware_models: dict[str, str | None] = {}
        # Consecutive failures, counted per frame and once for the account, so a
        # brief outage can be ridden out on the previous snapshot.
        self._failures: Counter[str] = Counter()

    async def _async_update_data(self) -> dict[str, FrameData]:
        """Fetch the current state of every frame on the account.

        Frames are fetched independently: an account can hold several, and one
        of them failing should not blank the others.

        A failure does not immediately blank anything. Skylight returns the
        occasional 500, and at a one-minute interval that would make every entity
        unavailable for a moment — a chore list vanishing from a dashboard
        because one request went wrong. The previous snapshot is served instead
        for up to `TOLERATED_FAILURES` consecutive polls, after which the failure
        is reported properly.

        Authentication failures are exempt: they will not fix themselves, and
        holding stale data over one would only delay the reauth prompt.
        """
        today = dt_util.now().date()
        try:
            frames = await self.client.get_frames()
        except (AuthenticationError, NotAuthorizedError) as err:
            raise ConfigEntryAuthFailed("Skylight rejected the stored credentials") from err
        except SkylightError as err:
            if (stale := self._tolerate("account", err)) is not None:
                return stale
            raise UpdateFailed(f"Error talking to Skylight: {err}") from err
        self._failures.pop("account", None)
        self.available_frames = {
            frame.id: frame.name or frame.household_name or frame.id for frame in frames
        }
        frames = self._selected(frames)

        # Sequential on purpose. Fetching frames concurrently would save a
        # little latency on a once-a-minute poll, at the cost of scheduling work
        # outside Home Assistant's task tracking. Not a trade worth making here.
        data: dict[str, FrameData] = {}
        errors: list[SkylightError] = []
        for frame in frames:
            try:
                data[frame.id] = await self._fetch_frame(frame, today)
            except (AuthenticationError, NotAuthorizedError) as err:
                # Raising this rather than UpdateFailed is what starts the
                # reauth flow instead of retrying forever with credentials that
                # no longer work.
                raise ConfigEntryAuthFailed("Skylight rejected the stored credentials") from err
            except SkylightError as err:
                _LOGGER.warning("Could not update Skylight frame %s: %s", frame.id, err)
                errors.append(err)
                # One frame failing must not blank the others, and a brief
                # failure must not blank this one either.
                if (previous := self._previous(frame.id)) is not None and self._within_tolerance(
                    frame.id
                ):
                    data[frame.id] = previous
                else:
                    self._failures.pop(frame.id, None)
            else:
                self._failures.pop(frame.id, None)

        if errors and not data:
            raise UpdateFailed(f"Error talking to Skylight: {errors[0]}")
        return data

    def _selected(self, frames: list[Frame]) -> list[Frame]:
        """Narrow the account's frames to the ones the user asked for.

        An empty or absent setting means every frame, so an account that never
        opens the options flow behaves as it always has. A frame named in the
        setting but no longer on the account simply does not match — the setting
        is not rewritten from here, since one bad poll must not discard a choice.
        """
        chosen = self.config_entry.options.get(CONF_FRAMES)
        if not chosen:
            return frames
        return [frame for frame in frames if frame.id in set(chosen)]

    def _previous(self, frame_id: str) -> FrameData | None:
        """Return the last good snapshot for a frame, if there is one."""
        return (self.data or {}).get(frame_id)

    def _within_tolerance(self, key: str) -> bool:
        """Count a failure and say whether stale data may still be served."""
        self._failures[key] += 1
        return self._failures[key] <= TOLERATED_FAILURES

    def _tolerate(self, key: str, err: SkylightError) -> dict[str, FrameData] | None:
        """Return the previous snapshot if this failure is worth riding out."""
        if not self.data or not self._within_tolerance(key):
            return None
        _LOGGER.warning(
            "Skylight poll failed (%s); serving the previous data, attempt %s of %s",
            err,
            self._failures[key],
            TOLERATED_FAILURES,
        )
        return self.data

    async def _fetch_frame(self, frame: Frame, today: date) -> FrameData:
        """Fetch the per-frame detail entities are built from."""
        # One response, two uses: the per-profile chores below and the Up for
        # Grabs list both come out of it.
        groups = await self.client.get_all_chores(frame.id)
        # include_late picks up anything overdue, which is what a chore chart
        # shows on the frame itself.
        charted = await self.client.get_chores(
            frame.id, after=today, before=today, include_late=True
        )
        return FrameData(
            frame=frame,
            categories=await self.client.get_categories(frame.id),
            chores=_merge_chores(charted, groups),
            unassigned_chores=_unassigned_chores(groups),
            rewards=await self.client.get_rewards(
                frame.id, redeemed_at_min=dt_util.utcnow() - REWARD_LOOKBACK
            ),
            reward_points=await self.client.get_reward_points(frame.id),
            recipes=await self.client.get_meal_recipes(frame.id),
            devices=await self.client.get_devices(frame.id),
            hardware_model=await self._hardware_model(frame.id),
            lists=await self._fetch_lists(frame.id),
            # Only a short window: enough for "what's on now or next", while
            # the calendar panel asks for arbitrary ranges on demand.
            calendar_events=await self.client.get_calendar_events(
                frame.id, today, today + CALENDAR_LOOKAHEAD, timezone=frame.timezone
            ),
        )

    async def _hardware_model(self, frame_id: str) -> str | None:
        """Return the frame's hardware model, fetching it the first time.

        The collection endpoint omits it; only GET /api/frames/{id} carries it.
        """
        if frame_id not in self._hardware_models:
            detail = await self.client.get_frame(frame_id)
            self._hardware_models[frame_id] = detail.hardware_model
        return self._hardware_models[frame_id]

    async def _fetch_lists(self, frame_id: str) -> list[SkylightList]:
        """Fetch every list on a frame, with its items resolved.

        The collection endpoint returns item ids but not the items, so each list
        needs its own request. Frames carry a handful of lists, so this stays
        cheap; if that ever changes, this is the place to start batching.
        """
        return [
            await self.client.get_list(frame_id, summary.id)
            for summary in await self.client.get_lists(frame_id)
        ]
