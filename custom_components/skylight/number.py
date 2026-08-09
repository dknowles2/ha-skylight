"""Number platform for the Skylight integration.

Two kinds of number: the numeric settings on a physical display, every one of
which was verified writable against real hardware, and the point cost of a
reward, which Skylight also lets you change.

Redeeming a reward is the `skylight.redeem_reward` action, targeted at the
reward's number entity. It is an action rather than a button because a
redemption spends points and cannot be undone from a stray tap on a dashboard,
and because an automation may want to pick which reward to redeem.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyskylight.models import Device, Reward

from .const import DOMAIN, SERVICE_REDEEM_REWARD
from .coordinator import SkylightConfigEntry, SkylightDataUpdateCoordinator
from .entity import SkylightDeviceEntity, SkylightEntity, is_buddy


@dataclass(frozen=True, kw_only=True)
class SkylightNumberEntityDescription(NumberEntityDescription):
    """Describes a numeric setting on a physical display."""

    value_fn: Callable[[Device], int | None]
    #: Only built for a Skylight Buddy; see `is_buddy`.
    buddy_only: bool = False


NUMBER_TYPES: tuple[SkylightNumberEntityDescription, ...] = (
    SkylightNumberEntityDescription(
        key="brightness",
        translation_key="brightness",
        # The device reports 0-255, not a percentage.
        native_min_value=0,
        native_max_value=255,
        native_step=1,
        mode=NumberMode.SLIDER,
        value_fn=lambda device: device.brightness,
    ),
    SkylightNumberEntityDescription(
        key="nightlight_brightness",
        translation_key="nightlight_brightness",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        buddy_only=True,
        value_fn=lambda device: device.nightlight_brightness,
    ),
    SkylightNumberEntityDescription(
        key="sleep_sound_volume",
        translation_key="sleep_sound_volume",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        buddy_only=True,
        value_fn=lambda device: device.sleep_sound_volume,
    ),
    SkylightNumberEntityDescription(
        key="slideshow_speed",
        translation_key="slideshow_speed",
        native_min_value=1,
        native_max_value=60,
        native_step=1,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda device: device.slideshow_speed,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkylightConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Skylight numbers from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            *(
                SkylightDeviceNumber(coordinator, frame_id, device.id, description)
                for frame_id, frame_data in coordinator.data.items()
                for device in frame_data.devices
                for description in NUMBER_TYPES
                if is_buddy(device) or not description.buddy_only
            ),
            *(
                SkylightRewardNumber(coordinator, frame_id, reward)
                for frame_id, frame_data in coordinator.data.items()
                for reward in frame_data.available_rewards
            ),
        ]
    )

    entity_platform.async_get_current_platform().async_register_entity_service(
        SERVICE_REDEEM_REWARD,
        None,
        "async_redeem",
        required_features=None,
    )


class SkylightDeviceNumber(SkylightDeviceEntity, NumberEntity):
    """A numeric setting on a physical display."""

    entity_description: SkylightNumberEntityDescription

    def __init__(
        self,
        coordinator: SkylightDataUpdateCoordinator,
        frame_id: str,
        device_id: str,
        description: SkylightNumberEntityDescription,
    ) -> None:
        """Initialize the number."""
        super().__init__(coordinator, frame_id, device_id)
        self.entity_description = description
        self._attr_unique_id = f"device_{device_id}_{description.key}"

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        return self.entity_description.value_fn(self.device)

    async def async_set_native_value(self, value: float) -> None:
        """Set the value."""
        await self.async_set_device(**{self.entity_description.key: int(value)})


class SkylightRewardNumber(SkylightEntity, NumberEntity):
    """What a reward costs, and the thing `skylight.redeem_reward` targets.

    The value is the point price, which Skylight accepts changes to. Redemption
    is a separate action rather than this entity being a button: it spends
    points irreversibly, and an automation may want to choose the reward.
    """

    _attr_translation_key = "reward"
    _attr_native_min_value = 0
    _attr_native_max_value = 1000
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: SkylightDataUpdateCoordinator,
        frame_id: str,
        reward: Reward,
    ) -> None:
        """Initialize the reward."""
        super().__init__(coordinator, frame_id)
        self._category_id = reward.category_id
        self._name = reward.name or reward.id
        # Keyed on the profile and the name, not the reward id. Redeeming a
        # respawning reward mints a new resource, so an id-based key would give
        # a brand new entity after every redemption.
        slug = self._name.strip().lower().replace(" ", "_")
        self._attr_unique_id = f"{frame_id}_{self._category_id}_reward_{slug}"
        profile = self.frame_data.profiles_by_id.get(self._category_id or "")
        self._attr_translation_placeholders = {
            "profile": (profile.label if profile else None) or "",
            "reward": self._name,
        }

    @property
    def _reward(self) -> Reward | None:
        """Return the live resource behind this reward, if there is one."""
        if self._frame_id not in self.coordinator.data:
            return None
        for reward in self.frame_data.available_rewards:
            if reward.category_id == self._category_id and (reward.name or "") == self._name:
                return reward
        return None

    @property
    def available(self) -> bool:
        """Whether the reward is still offered on the frame."""
        return super().available and self._reward is not None

    @property
    def native_value(self) -> float | None:
        """Return what the reward costs."""
        reward = self._reward
        return reward.point_value if reward else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Say how close the profile is to affording this reward.

        `affordable` alone answers a yes/no question, which is the wrong shape
        for a chore chart: most of the time the interesting fact is how much
        further there is to go. `progress` and `points_needed` are the two ways
        a dashboard usually wants to say that — a bar and a sentence — and both
        are derived here so no template has to divide one entity by another.

        `progress` is capped at 100. Points keep accruing past the price of a
        reward, and a bar that overfills says nothing useful.
        """
        reward = self._reward
        if reward is None:
            return None
        points = self.frame_data.points_for(self._category_id or "")
        balance = points.current_point_balance if points else None
        cost = reward.point_value
        if balance is None or cost is None:
            # A profile with no recorded balance is not one with zero points,
            # and every derived answer is unknown rather than pessimistic.
            return {
                "balance": balance,
                "affordable": None,
                "progress": None,
                "points_needed": None,
            }
        return {
            "balance": balance,
            "affordable": balance >= cost,
            # A free reward is already earned rather than a division by zero.
            "progress": 100 if cost <= 0 else round(min(balance / cost, 1) * 100),
            "points_needed": max(cost - balance, 0),
        }

    async def async_set_native_value(self, value: float) -> None:
        """Change what the reward costs."""
        reward = self._require_reward()
        await self.async_write(
            "set_reward_failed",
            self.coordinator.client.update_reward(
                self._frame_id, reward.id, point_value=int(value)
            ),
        )

    async def async_redeem(self) -> None:
        """Redeem the reward.

        No profile is named: a reward belongs to one, and Skylight rejects a
        `category_id` here. It also enforces the balance and refuses a second
        redemption, so nothing is checked locally — a check here would race the
        balance and refuse a redemption moments after a chore was completed.
        """
        reward = self._require_reward()
        await self.async_write(
            "redeem_reward_failed",
            self.coordinator.client.redeem_reward(self._frame_id, reward.id),
        )

    def _require_reward(self) -> Reward:
        reward = self._reward
        if reward is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="reward_gone",
                translation_placeholders={"reward": self._name},
            )
        return reward
