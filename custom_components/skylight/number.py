"""Number platform for the Skylight integration.

Numeric display settings. Every field here was verified writable against real
hardware.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyskylight.models import Device

from .coordinator import SkylightConfigEntry, SkylightDataUpdateCoordinator
from .entity import SkylightDeviceEntity


@dataclass(frozen=True, kw_only=True)
class SkylightNumberEntityDescription(NumberEntityDescription):
    """Describes a numeric setting on a physical display."""

    value_fn: Callable[[Device], int | None]


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
        SkylightDeviceNumber(coordinator, frame_id, device.id, description)
        for frame_id, frame_data in coordinator.data.items()
        for device in frame_data.devices
        for description in NUMBER_TYPES
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
