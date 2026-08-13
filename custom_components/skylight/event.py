"""Event platform for the Skylight integration.

Rewards get redeemed and chores get ticked off at the frame far more often than
from Home Assistant, and polling alone only leaves a changed attribute behind.
These turn that into event entities, so an automation can notify a phone or run
something when a child finishes a chore or cashes points in.

Skylight offers nothing to push with, so an event surfaces within one poll
interval rather than instantly.
"""

from __future__ import annotations

from abc import abstractmethod
from datetime import datetime
from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import SkylightConfigEntry, SkylightDataUpdateCoordinator
from .entity import SkylightEntity

EVENT_REDEEMED = "redeemed"
EVENT_COMPLETED = "completed"

# What a subclass reports for each thing that has happened: a key identifying
# it, a marker that changes when it happens again, and the event payload.
type Observations = dict[str, tuple[Any, dict[str, Any]]]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkylightConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the event entities for every frame."""
    coordinator = entry.runtime_data
    async_add_entities(
        entity_class(coordinator, frame_id)
        for frame_id in coordinator.data
        for entity_class in (SkylightRewardEvent, SkylightChoreEvent)
    )


class SkylightPollingEvent(SkylightEntity, EventEntity):
    """Fires when something new shows up in a coordinator refresh.

    One entity per frame rather than one per reward or chore. The things being
    watched come and go — a reward respawns, a chore falls out of today's window
    — and an automation wants "somebody did something" with the details
    attached, not a subscription per item.
    """

    _event_type: str
    _key: str

    def __init__(self, coordinator: SkylightDataUpdateCoordinator, frame_id: str) -> None:
        """Initialize the event entity."""
        super().__init__(coordinator, frame_id)
        self._attr_unique_id = f"{frame_id}_{self._key}"
        # Set here rather than on the class: the base declares it as an
        # instance variable, and a mutable class attribute is a ruff error.
        self._attr_event_types = [self._event_type]
        # Seeded from the snapshot the entity is built on, so its history never
        # fires. `_handle_coordinator_update` only runs on *later* refreshes, and
        # the first snapshot already holds today's completed chores and a week of
        # redemptions — replaying that at every restart would spray notifications
        # for things the user saw days ago.
        self._seen = {key: marker for key, (marker, _) in self._observations().items()}
        # Seeding is not enough on its own, and neither is remembering. An
        # occurrence completed before this entity existed can arrive in a later
        # snapshot without ever having been in the first — an open-ended
        # assignment with no due date lives in the late bucket and is pulled
        # into every day's chart for ever, and two were sitting on a live frame
        # finished in July when this was written. Nothing that happened before
        # this entity started watching is news, whether or not it has been seen.
        self._watching_since = dt_util.utcnow()

    def _profile_label(self, category_id: str | None) -> str | None:
        """Return a family profile's name, if the category is one."""
        profile = self.frame_data.profiles_by_id.get(category_id or "")
        return profile.label if profile else None

    @abstractmethod
    def _observations(self) -> Observations:
        """Return everything that has currently happened, keyed by id.

        Only things that *have* happened belong here. Something that reverts —
        a chore reopened, a reward respawned — drops out, so doing it again
        counts as new and fires once more.

        Callers have already established that the frame is in the snapshot:
        entities are built per frame, and the update handler returns early.
        """

    def _before_watching(self, marker: Any) -> bool:
        """Whether this happened before the entity started watching.

        The marker is when the thing happened — a redemption's `redeemed_at`, a
        chore's `completed_at` — so it answers the question directly.

        A chore finished shortly before a restart is therefore never announced.
        That is the right way round: the point is to say what is happening now,
        and a notification about something already done is worse than none.

        Only a real timestamp counts. A chore can fall back to `completed_on`,
        which is a bare date, and judging staleness from that would silence one
        ticked off at 23:50 and picked up by the poll after midnight — a real
        completion, and the sort of thing a bedtime chore does every night. So a
        date is let through, which leaves a gap: a chore whose only completion
        signal is an old date still announces itself. Every stale one observed
        on a live frame carried a timestamp, so the gap is narrower than the
        alternative's false silences.
        """
        if isinstance(marker, datetime):
            return dt_util.as_utc(marker) < self._watching_since
        return False

    @callback
    def _handle_coordinator_update(self) -> None:
        """Fire for everything that appeared since the last refresh."""
        if self._frame_id not in self.coordinator.data:
            super()._handle_coordinator_update()
            return

        observations = self._observations()
        for key, (marker, payload) in observations.items():
            if self._seen.get(key) == marker:
                continue
            if self._before_watching(marker):
                continue
            self._trigger_event(self._event_type, payload)
            # Written per event so two things happening inside one poll are two
            # state changes, not one that silently swallows the first.
            self.async_write_ha_state()

        # Updated, never rebuilt. Rebuilding forgets anything absent from this
        # snapshot, and things leave it constantly: a recurring chore is one
        # occurrence per day, so yesterday's drops out of today's window as a
        # matter of course. Forgotten, its return reads as a fresh completion —
        # which is how a "Take pills" finished last night announced itself the
        # next day.
        #
        # This grows by one small entry per thing completed, and a restart
        # clears it, so the bound is a household's chores times its uptime.
        self._seen.update({key: marker for key, (marker, _) in observations.items()})
        super()._handle_coordinator_update()


class SkylightRewardEvent(SkylightPollingEvent):
    """Fires whenever a reward on this frame is redeemed."""

    _attr_translation_key = "reward_redeemed"
    _event_type = EVENT_REDEEMED
    _key = "reward_redeemed"

    def _observations(self) -> Observations:
        """Return every reward currently redeemed, keyed by reward id."""
        return {
            reward.id: (
                reward.redeemed_at,
                {
                    "reward_id": reward.id,
                    "reward": reward.name,
                    "point_value": reward.point_value,
                    "profile": self._profile_label(reward.category_id),
                    "category_id": reward.category_id,
                    "redeemed_at": reward.redeemed_at.isoformat(),
                },
            )
            for reward in self.frame_data.rewards
            if reward.redeemed_at is not None
        }


class SkylightChoreEvent(SkylightPollingEvent):
    """Fires whenever a chore on this frame is completed.

    Both kinds count: a chore assigned to a profile, and an up-for-grabs one
    somebody claimed. `completed_category` is who gets the credit — for an
    assigned chore that is its owner, and for an up-for-grabs chore it is
    whoever claimed it, which is the more interesting of the two.
    """

    _attr_translation_key = "chore_completed"
    _event_type = EVENT_COMPLETED
    _key = "chore_completed"

    def _observations(self) -> Observations:
        """Return every chore currently complete, keyed by occurrence id."""
        observations: Observations = {}
        for chore in (*self.frame_data.chores, *self.frame_data.unassigned_chores):
            if not chore.completed:
                continue
            # A recurring chore is one resource per occurrence, so the occurrence
            # id is what distinguishes today's from yesterday's.
            credited = chore.completed_category_id or chore.category_id
            completed_at = chore.completed_at or chore.completed_on
            observations[chore.id] = (
                completed_at or True,
                {
                    "chore_id": chore.chore_id,
                    "occurrence_id": chore.id,
                    "chore": chore.summary,
                    "reward_points": chore.reward_points,
                    "profile": self._profile_label(credited),
                    "category_id": credited,
                    "up_for_grabs": bool(chore.up_for_grabs),
                    "completed_at": completed_at.isoformat() if completed_at else None,
                },
            )
        return observations
