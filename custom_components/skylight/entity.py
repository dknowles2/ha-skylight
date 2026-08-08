"""Base entity for the Skylight integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import FrameData, SkylightDataUpdateCoordinator


class SkylightEntity(CoordinatorEntity[SkylightDataUpdateCoordinator]):
    """Base class for entities belonging to one Skylight frame.

    A frame is the household's device and calendar, so it maps onto a Home
    Assistant device; everything a frame shows hangs off it.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: SkylightDataUpdateCoordinator, frame_id: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._frame_id = frame_id
        frame = self.frame_data.frame
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, frame_id)},
            manufacturer=MANUFACTURER,
            name=frame.name or frame.household_name,
            model=frame.hardware_model,
            configuration_url="https://app.ourskylight.com",
        )

    @property
    def frame_data(self) -> FrameData:
        """The current snapshot for this entity's frame."""
        return self.coordinator.data[self._frame_id]

    @property
    def available(self) -> bool:
        """Whether the frame was present in the most recent refresh."""
        return super().available and self._frame_id in self.coordinator.data
