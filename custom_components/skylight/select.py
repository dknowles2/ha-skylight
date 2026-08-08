"""Select platform for the Skylight integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyskylight.models import Device, NightlightColor

from .coordinator import SkylightConfigEntry, SkylightDataUpdateCoordinator
from .entity import SkylightDeviceEntity, is_buddy


@dataclass(frozen=True, kw_only=True)
class SkylightSelectEntityDescription(SelectEntityDescription):
    """Describes a fixed-choice setting on a physical display."""

    value_fn: Callable[[Device], str | None]
    #: Only built for a Skylight Buddy; see `is_buddy`.
    buddy_only: bool = False


SELECT_TYPES: tuple[SkylightSelectEntityDescription, ...] = (
    SkylightSelectEntityDescription(
        key="nightlight_color",
        translation_key="nightlight_color",
        # The API rejects anything outside this set, including white and purple.
        options=list(NightlightColor.ALL),
        entity_category=EntityCategory.CONFIG,
        # Buddy-only, and the strongest case of it: Skylight's own app never
        # reads or writes this field on any device. The server still stores it.
        buddy_only=True,
        value_fn=lambda device: device.nightlight_color,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkylightConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Skylight selects from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        SkylightDeviceSelect(coordinator, frame_id, device.id, description)
        for frame_id, frame_data in coordinator.data.items()
        for device in frame_data.devices
        for description in SELECT_TYPES
        if is_buddy(device) or not description.buddy_only
    )


class SkylightDeviceSelect(SkylightDeviceEntity, SelectEntity):
    """A fixed-choice setting on a physical display."""

    entity_description: SkylightSelectEntityDescription

    def __init__(
        self,
        coordinator: SkylightDataUpdateCoordinator,
        frame_id: str,
        device_id: str,
        description: SkylightSelectEntityDescription,
    ) -> None:
        """Initialize the select."""
        super().__init__(coordinator, frame_id, device_id)
        self.entity_description = description
        self._attr_unique_id = f"device_{device_id}_{description.key}"

    @property
    def current_option(self) -> str | None:
        """Return the current choice, or None if it is one we do not know."""
        value = self.entity_description.value_fn(self.device)
        return value if value in self.options else None

    async def async_select_option(self, option: str) -> None:
        """Choose an option."""
        await self.async_set_device(**{self.entity_description.key: option})
