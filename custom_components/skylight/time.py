"""Time platform for the Skylight integration.

The display's sleep and wake schedule.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import time

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyskylight.models import Device

from .coordinator import SkylightConfigEntry, SkylightDataUpdateCoordinator
from .entity import SkylightDeviceEntity


@dataclass(frozen=True, kw_only=True)
class SkylightTimeEntityDescription(TimeEntityDescription):
    """Describes a time-of-day setting on a physical display."""

    value_fn: Callable[[Device], str | None]


TIME_TYPES: tuple[SkylightTimeEntityDescription, ...] = (
    SkylightTimeEntityDescription(
        key="sleeps_at",
        translation_key="sleeps_at",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda device: device.sleeps_at,
    ),
    SkylightTimeEntityDescription(
        key="wakes_at",
        translation_key="wakes_at",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda device: device.wakes_at,
    ),
)


def _parse(value: str | None) -> time | None:
    """Parse the API's "HH:MM" into a time, tolerating anything else."""
    if not value:
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkylightConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Skylight time entities from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        SkylightDeviceTime(coordinator, frame_id, device.id, description)
        for frame_id, frame_data in coordinator.data.items()
        for device in frame_data.devices
        for description in TIME_TYPES
    )


class SkylightDeviceTime(SkylightDeviceEntity, TimeEntity):
    """A time-of-day setting on a physical display."""

    entity_description: SkylightTimeEntityDescription

    def __init__(
        self,
        coordinator: SkylightDataUpdateCoordinator,
        frame_id: str,
        device_id: str,
        description: SkylightTimeEntityDescription,
    ) -> None:
        """Initialize the time entity."""
        super().__init__(coordinator, frame_id, device_id)
        self.entity_description = description
        self._attr_unique_id = f"device_{device_id}_{description.key}"

    @property
    def native_value(self) -> time | None:
        """Return the configured time."""
        return _parse(self.entity_description.value_fn(self.device))

    async def async_set_value(self, value: time) -> None:
        """Set the time, in the "HH:MM" form the API uses."""
        await self.async_set_device(**{self.entity_description.key: value.strftime("%H:%M")})
