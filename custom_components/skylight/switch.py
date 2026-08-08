"""Switch platform for the Skylight integration.

Toggles on a physical display. Every field here was verified writable against
real hardware — but writable is not the same as supported, which is why some of
them are built only for a Skylight Buddy. See `buddy_only`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyskylight.models import Device

from .coordinator import SkylightConfigEntry, SkylightDataUpdateCoordinator
from .entity import SkylightDeviceEntity, is_buddy


@dataclass(frozen=True, kw_only=True)
class SkylightSwitchEntityDescription(SwitchEntityDescription):
    """Describes a toggle on a physical display."""

    value_fn: Callable[[Device], bool | None]
    #: Only built for a Skylight Buddy; see `is_buddy`.
    buddy_only: bool = False


SWITCH_TYPES: tuple[SkylightSwitchEntityDescription, ...] = (
    SkylightSwitchEntityDescription(
        key="nightlight",
        translation_key="nightlight",
        buddy_only=True,
        value_fn=lambda device: device.nightlight,
    ),
    SkylightSwitchEntityDescription(
        key="show_caption",
        translation_key="show_caption",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda device: device.show_caption,
    ),
    SkylightSwitchEntityDescription(
        key="blur_effect",
        translation_key="blur_effect",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda device: device.blur_effect,
    ),
    SkylightSwitchEntityDescription(
        key="side_by_side",
        translation_key="side_by_side",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda device: device.side_by_side,
    ),
    SkylightSwitchEntityDescription(
        key="show_heart",
        translation_key="show_heart",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda device: device.show_heart,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkylightConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Skylight switches from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        SkylightDeviceSwitch(coordinator, frame_id, device.id, description)
        for frame_id, frame_data in coordinator.data.items()
        for device in frame_data.devices
        for description in SWITCH_TYPES
        if is_buddy(device) or not description.buddy_only
    )


class SkylightDeviceSwitch(SkylightDeviceEntity, SwitchEntity):
    """A toggle on a physical display."""

    entity_description: SkylightSwitchEntityDescription

    def __init__(
        self,
        coordinator: SkylightDataUpdateCoordinator,
        frame_id: str,
        device_id: str,
        description: SkylightSwitchEntityDescription,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, frame_id, device_id)
        self.entity_description = description
        self._attr_unique_id = f"device_{device_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return whether the setting is on."""
        return self.entity_description.value_fn(self.device)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the setting on."""
        await self.async_set_device(**{self.entity_description.key: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the setting off."""
        await self.async_set_device(**{self.entity_description.key: False})
