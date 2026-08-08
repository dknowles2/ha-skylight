"""Button platform for the Skylight integration.

One button per reward. A reward belongs to exactly one family profile, so
redeeming it needs no say in who is claiming it — unlike an up-for-grabs chore.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyskylight.models import Reward

from .coordinator import SkylightConfigEntry, SkylightDataUpdateCoordinator
from .entity import SkylightEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkylightConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a redeem button for every reward."""
    coordinator = entry.runtime_data
    async_add_entities(
        SkylightRewardButton(coordinator, frame_id, reward.id)
        for frame_id, frame_data in coordinator.data.items()
        for reward in frame_data.rewards
    )


class SkylightRewardButton(SkylightEntity, ButtonEntity):
    """Redeem one reward.

    Nothing here checks whether the balance covers the cost. Skylight enforces
    both that and double redemption, and predicting it locally would race the
    balance — refusing a press moments after a chore was completed, when the
    points have already been earned. The API's refusals are clear enough to
    show as-is.
    """

    _attr_translation_key = "reward"

    def __init__(
        self,
        coordinator: SkylightDataUpdateCoordinator,
        frame_id: str,
        reward_id: str,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, frame_id)
        self._reward_id = reward_id
        self._attr_unique_id = f"{frame_id}_reward_{reward_id}"
        reward = self._reward_or_none()
        profile = self.frame_data.profiles_by_id.get(reward.category_id or "") if reward else None
        self._attr_translation_placeholders = {
            "profile": (profile.label if profile else None) or "",
            "reward": (reward.name if reward else None) or reward_id,
        }

    def _reward_or_none(self) -> Reward | None:
        if self._frame_id not in self.coordinator.data:
            return None
        for reward in self.frame_data.rewards:
            if reward.id == self._reward_id:
                return reward
        return None

    @property
    def available(self) -> bool:
        """Whether the reward is still on the frame."""
        return super().available and self._reward_or_none() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the cost and the last redemption.

        A button's own state is when Home Assistant last pressed it, which says
        nothing about redemptions made on the frame. `redeemed_at` is the real
        answer, and the cost saves a dashboard needing a second entity.
        """
        reward = self._reward_or_none()
        if reward is None:
            return None
        return {
            "point_value": reward.point_value,
            "redeemed_at": reward.redeemed_at,
            "respawn_on_redemption": reward.respawn_on_redemption,
        }

    async def async_press(self) -> None:
        """Redeem the reward.

        Skylight deducts the points itself, so there is nothing to adjust here —
        the refresh picks up the new balance.
        """
        await self.async_write(
            "redeem_reward_failed",
            self.coordinator.client.redeem_reward(self._frame_id, self._reward_id),
        )
