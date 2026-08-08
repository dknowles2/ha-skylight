"""Base entity for the Skylight integration."""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyskylight.exceptions import SkylightError
from pyskylight.models import Device

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
            model=self.frame_data.hardware_model,
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

    async def async_write(self, translation_key: str, coro: Coroutine[Any, Any, object]) -> None:
        """Run a write, then refresh.

        A failed write has to be visible: silently doing nothing is the worst
        outcome for something the user just clicked. The refresh means the UI
        reflects the change without waiting out the poll interval.
        """
        try:
            await coro
        except SkylightError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=translation_key,
                translation_placeholders={"error": str(err)},
            ) from err
        await self.coordinator.async_request_refresh()


class SkylightDeviceEntity(SkylightEntity):
    """Base class for entities belonging to a physical Skylight device.

    A frame can hold more than one device — a kitchen display and a bedroom one,
    say — each with its own name, alarms, and nightlight. They are modelled as
    Home Assistant devices linked to the frame with `via_device`, so a
    multi-device household is represented correctly rather than being flattened
    onto the frame.

    Only attributes the device alone carries live here. Anything the frame also
    reports (brightness, sleep schedule, slideshow settings) stays on the frame,
    so the two do not show duplicate copies of the same value.
    """

    def __init__(
        self,
        coordinator: SkylightDataUpdateCoordinator,
        frame_id: str,
        device_id: str,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator, frame_id)
        self._device_id = device_id
        device = self.device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"device_{device_id}")},
            via_device=(DOMAIN, frame_id),
            manufacturer=MANUFACTURER,
            name=device.name,
            model=self.frame_data.hardware_model,
            configuration_url="https://app.ourskylight.com",
        )

    @property
    def device(self) -> Device:
        """The device this entity belongs to."""
        return self.frame_data.devices_by_id[self._device_id]

    @property
    def available(self) -> bool:
        """Whether the device is still registered to the frame."""
        return super().available and self._device_id in self.frame_data.devices_by_id
