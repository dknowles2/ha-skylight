"""Data update coordinator for the Skylight integration."""

from __future__ import annotations

import logging
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
    Device,
    Frame,
    Reward,
    RewardPoint,
    SkylightList,
)

from .const import (
    CALENDAR_LOOKAHEAD,
    CURRENT_CHORE_BUCKETS,
    DOMAIN,
    REWARD_LOOKBACK,
    SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type SkylightConfigEntry = ConfigEntry[SkylightDataUpdateCoordinator]


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
        # Static per frame, and only returned by the single-frame endpoint, so
        # it is fetched once rather than on every poll.
        self._hardware_models: dict[str, str | None] = {}

    async def _async_update_data(self) -> dict[str, FrameData]:
        """Fetch the current state of every frame on the account.

        Frames are fetched independently: an account can hold several, and one
        of them failing should not blank the others. A frame that errors is
        dropped from the snapshot, which makes its entities unavailable rather
        than leaving them showing stale numbers.
        """
        today = dt_util.now().date()
        try:
            frames = await self.client.get_frames()
        except (AuthenticationError, NotAuthorizedError) as err:
            raise ConfigEntryAuthFailed("Skylight rejected the stored credentials") from err
        except SkylightError as err:
            raise UpdateFailed(f"Error talking to Skylight: {err}") from err

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

        if errors and not data:
            raise UpdateFailed(f"Error talking to Skylight: {errors[0]}")
        return data

    async def _fetch_frame(self, frame: Frame, today: date) -> FrameData:
        """Fetch the per-frame detail entities are built from."""
        return FrameData(
            frame=frame,
            categories=await self.client.get_categories(frame.id),
            # include_late picks up anything overdue, which is what a chore
            # chart shows on the frame itself.
            chores=await self.client.get_chores(
                frame.id, after=today, before=today, include_late=True
            ),
            unassigned_chores=await self._fetch_unassigned_chores(frame.id),
            rewards=await self.client.get_rewards(
                frame.id, redeemed_at_min=dt_util.utcnow() - REWARD_LOOKBACK
            ),
            reward_points=await self.client.get_reward_points(frame.id),
            devices=await self.client.get_devices(frame.id),
            hardware_model=await self._hardware_model(frame.id),
            lists=await self._fetch_lists(frame.id),
            # Only a short window: enough for "what's on now or next", while
            # the calendar panel asks for arbitrary ranges on demand.
            calendar_events=await self.client.get_calendar_events(
                frame.id, today, today + CALENDAR_LOOKAHEAD, timezone=frame.timezone
            ),
        )

    async def _fetch_unassigned_chores(self, frame_id: str) -> list[Chore]:
        """Fetch the chores the Skylight app shows under "Up for Grabs".

        These belong to nobody, and `GET /chores` never returns them whatever
        the date range — `/chores/all` is the only source, which is why this
        costs a second request per frame.

        The buckets are taken rather than the whole response so the list covers
        the same span as the per-profile chore lists: overdue, due today, and
        undated.

        `Chore.unassigned` wants both the flag and an absent category: a `PUT`
        setting `up_for_grabs` alone returns 200 and changes nothing, so a chore
        can carry it while still belonging to someone.
        """
        groups = await self.client.get_all_chores(frame_id)
        return [
            chore
            for bucket in CURRENT_CHORE_BUCKETS
            for chore in groups.chores.get(bucket, [])
            if chore.unassigned
        ]

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
