"""Binary sensor platform for the Skylight integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyskylight.models import Device

from .coordinator import SkylightConfigEntry, SkylightDataUpdateCoordinator
from .entity import SkylightDeviceEntity


@dataclass(frozen=True, kw_only=True)
class SkylightBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a Skylight binary sensor read from a physical device."""

    value_fn: Callable[[Device], bool | None]


BINARY_SENSOR_TYPES: tuple[SkylightBinarySensorEntityDescription, ...] = (
    SkylightBinarySensorEntityDescription(
        key="nightlight",
        translation_key="nightlight",
        value_fn=lambda device: device.nightlight,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkylightConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Skylight binary sensors from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        SkylightDeviceBinarySensor(coordinator, frame_id, device.id, description)
        for frame_id, frame_data in coordinator.data.items()
        for device in frame_data.devices
        for description in BINARY_SENSOR_TYPES
    )


class SkylightDeviceBinarySensor(SkylightDeviceEntity, BinarySensorEntity):
    """A binary sensor reading one attribute of a physical device."""

    entity_description: SkylightBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: SkylightDataUpdateCoordinator,
        frame_id: str,
        device_id: str,
        description: SkylightBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, frame_id, device_id)
        self.entity_description = description
        self._attr_unique_id = f"device_{device_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return the current value."""
        return self.entity_description.value_fn(self.device)
