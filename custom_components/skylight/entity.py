"""Base entity for the Skylight integration."""

from __future__ import annotations

from collections.abc import Coroutine
from dataclasses import replace
from typing import Any, TypeVar

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyskylight.exceptions import SkylightError
from pyskylight.models import Device

from .const import DOMAIN, MANUFACTURER
from .coordinator import FrameData, SkylightDataUpdateCoordinator

_T = TypeVar("_T")

#: The value of a device's `role` that marks it as a Skylight Buddy.
#:
#: Skylight's own app draws the line here — its `deviceUtils.isBuddy` is exactly
#: `device.attributes.role === 'buddy'`, and everything Buddy-shaped in the app
#: is reached through it. A calendar display reports `null`.
#:
#: This constant is a protocol fact and belongs in pyskylight, which records it
#: in `docs/api-notes.md`; it lives here only until the pinned version grows a
#: `Device.is_buddy` to import instead.
BUDDY_ROLE = "buddy"


def is_buddy(device: Device) -> bool:
    """Whether a display is a Skylight Buddy rather than a calendar or frame.

    This decides whether the Buddy-only settings — the nightlight and the sleep
    sound — get entities at all, and the API cannot answer that question. A
    calendar display reports those fields, accepts writes to them, stores what
    it is given and validates the enum, which is indistinguishable from a
    setting that works. Alarms are refused outright, so there is a Buddy check
    on the server, but it does not cover these.

    The vendor's own client is what settles it: the nightlight and sleep sound
    controls are rendered only on its Buddy screens, and `nightlight_color` it
    never reads or writes anywhere. A `200` means the server stored the value,
    not that any hardware acts on it — and a switch that flips, persists, and
    does nothing is worse than no switch.
    """
    return device.role == BUDDY_ROLE


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
        """The current snapshot for this entity's frame.

        Only valid while the entity is available. Every caller either checks
        that first or is reached through something that does — with one class of
        exception worth knowing about: Home Assistant reads a calendar entity's
        `event` *before* consulting `available`, so that one guards explicitly.
        """
        return self.coordinator.data[self._frame_id]

    @property
    def frame_data_or_none(self) -> FrameData | None:
        """The snapshot, or None if the frame is not in the latest refresh."""
        return self.coordinator.data.get(self._frame_id)

    @property
    def available(self) -> bool:
        """Whether the frame was present in the most recent refresh."""
        return super().available and self._frame_id in self.coordinator.data

    async def async_write(self, translation_key: str, coro: Coroutine[Any, Any, _T]) -> _T:
        """Run a write, then refresh.

        A failed write has to be visible: silently doing nothing is the worst
        outcome for something the user just clicked.
        """
        try:
            result = await coro
        except SkylightError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=translation_key,
                translation_placeholders={"error": str(err)},
            ) from err
        await self.coordinator.async_request_refresh()
        return result


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

    async def async_set_device(self, **fields: Any) -> None:
        """Change settings on this entity's display.

        Display settings live on the device: the frame endpoint accepts the same
        fields, returns 200, and applies nothing.

        The updated device from the response is written straight into the
        coordinator's snapshot. `async_request_refresh()` on its own is
        debounced, so without this the entity keeps showing its old value for
        several seconds after the user changed it — the control appears to snap
        back. Verified against real hardware.
        """
        updated = await self.async_write(
            "set_device_failed",
            self.coordinator.client.update_device(self._frame_id, self._device_id, **fields),
        )
        self._apply(updated)

    def _apply(self, updated: Device) -> None:
        """Splice a freshly written device into the coordinator's snapshot."""
        frame_data = self.coordinator.data.get(self._frame_id)
        if frame_data is None:
            return
        data = dict(self.coordinator.data)
        data[self._frame_id] = replace(
            frame_data,
            devices=[
                updated if device.id == updated.id else device for device in frame_data.devices
            ],
        )
        self.coordinator.async_set_updated_data(data)
