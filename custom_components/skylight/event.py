"""Event platform for the Skylight integration.

Rewards get redeemed at the frame far more often than from Home Assistant, and
polling alone only leaves a changed attribute behind. This turns that into an
event entity, so an automation can notify a phone or run something when a child
cashes points in.
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SkylightConfigEntry, SkylightDataUpdateCoordinator
from .entity import SkylightEntity

EVENT_REDEEMED = "redeemed"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkylightConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one reward event entity per frame."""
    coordinator = entry.runtime_data
    async_add_entities(SkylightRewardEvent(coordinator, frame_id) for frame_id in coordinator.data)


class SkylightRewardEvent(SkylightEntity, EventEntity):
    """Fires whenever a reward on this frame is redeemed.

    One entity per frame rather than per reward: rewards come and go as they are
    redeemed and respawn, and an automation wants "somebody redeemed something"
    with the details attached, not a subscription per reward.
    """

    _attr_translation_key = "reward_redeemed"

    def __init__(self, coordinator: SkylightDataUpdateCoordinator, frame_id: str) -> None:
        """Initialize the event entity."""
        super().__init__(coordinator, frame_id)
        self._attr_unique_id = f"{frame_id}_reward_redeemed"
        # Set here rather than on the class: the base declares it as an
        # instance variable, and a mutable class attribute is a ruff error.
        self._attr_event_types = [EVENT_REDEEMED]
        # Seeded from the snapshot the entity is built on, so the history in it
        # never fires. Rewards are fetched with a week's lookback, and
        # `_handle_coordinator_update` only runs on later refreshes — leaving
        # this empty would replay days of redemptions at every restart.
        self._seen = self._redeemed_now()

    def _redeemed_now(self) -> dict[str, datetime]:
        """Return {reward_id: redeemed_at} for everything currently redeemed.

        Both callers have already established that the frame is in the snapshot:
        entities are built per frame, and the update handler returns early.
        """
        return {
            reward.id: reward.redeemed_at
            for reward in self.frame_data.rewards
            if reward.redeemed_at is not None
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Fire for every redemption that appeared since the last refresh."""
        if self._frame_id not in self.coordinator.data:
            super()._handle_coordinator_update()
            return

        current = self._redeemed_now()
        for reward in self.frame_data.rewards:
            if reward.redeemed_at is None or self._seen.get(reward.id) == reward.redeemed_at:
                continue
            profile = self.frame_data.profiles_by_id.get(reward.category_id or "")
            self._trigger_event(
                EVENT_REDEEMED,
                {
                    "reward_id": reward.id,
                    "reward": reward.name,
                    "point_value": reward.point_value,
                    "profile": profile.label if profile else None,
                    "category_id": reward.category_id,
                    "redeemed_at": reward.redeemed_at.isoformat(),
                },
            )
            # Written per event so two redemptions in one poll are two state
            # changes, not one that silently swallows the first.
            self.async_write_ha_state()

        self._seen = current
        super()._handle_coordinator_update()
