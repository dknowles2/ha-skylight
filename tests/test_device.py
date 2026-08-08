"""Tests for physical Skylight devices modelled as Home Assistant devices."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pyskylight.models import Device
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skylight.const import DOMAIN

from .conftest import BUDDY_ID, DEVICE_ID, FRAME_ID, async_poll, setup_integration

SLEEP_MODE = "sensor.kitchen_calendar_sleep_mode"
BLUR = "switch.kitchen_calendar_blur_effect"

# Settings that only mean something on a Skylight Buddy. A calendar display
# reports them and accepts writes to them, which is why they need naming here
# rather than being inferred from the API.
BUDDY_ONLY = (
    "nightlight",
    "nightlight_brightness",
    "nightlight_color",
    "sleep_sound",
    "sleep_sound_volume",
)


async def test_device_is_linked_to_its_frame(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """The hardware is its own device, hanging off the frame."""
    await setup_integration(hass, mock_config_entry)

    frame = device_registry.async_get_device(identifiers={(DOMAIN, FRAME_ID)})
    device = device_registry.async_get_device(identifiers={(DOMAIN, f"device_{DEVICE_ID}")})
    assert frame is not None
    assert device is not None
    assert device.name == "Kitchen Calendar"
    assert device.manufacturer == "Skylight"
    # via_device is what makes Home Assistant show it beneath the frame.
    assert device.via_device_id == frame.id


async def test_hardware_model_comes_from_the_detail_endpoint(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The model is only on GET /frames/{id}, and is fetched once, not per poll."""
    await setup_integration(hass, mock_config_entry)

    device = device_registry.async_get_device(identifiers={(DOMAIN, f"device_{DEVICE_ID}")})
    assert device.model == "15-CAL-2.0"
    assert mock_client.get_frame.await_count == 1

    await async_poll(hass, freezer)
    assert mock_client.get_frame.await_count == 1


async def test_device_only_attributes_are_exposed(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry, buddy: Device
) -> None:
    """The device carries what the frame does not."""
    mock_client.get_devices.return_value = [*mock_client.get_devices.return_value, buddy]
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(SLEEP_MODE).state == "screen_off"
    assert hass.states.get("switch.bedside_buddy_nightlight").state == "off"
    assert hass.states.get("number.bedside_buddy_nightlight_brightness").state == "65"
    assert hass.states.get("select.bedside_buddy_nightlight_color").state == "off"
    assert hass.states.get("number.bedside_buddy_sleep_sound_volume").state == "70"
    # Not set on this device, which is distinct from being off.
    assert hass.states.get("sensor.bedside_buddy_sleep_sound").state == STATE_UNKNOWN


async def test_buddy_only_settings_are_not_built_for_a_calendar(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    buddy: Device,
) -> None:
    """A calendar display gets no nightlight and no sleep sound.

    The API is no help in deciding this: a calendar reports every one of these
    fields, accepts writes to them, stores what it is given, and validates the
    colour against its enum — a live display returned 200 and read the new value
    back for all three nightlight fields. Alarms are refused with `422 Device
    must be a buddy device`, so a Buddy check exists on the server, but it does
    not cover these.

    Skylight's own client is what draws the line: it renders these controls only
    on its Buddy screens, keys that off `role == "buddy"`, and never reads or
    writes `nightlight_color` at all. So the entities would have flipped,
    persisted, and done nothing on this hardware.
    """
    mock_client.get_devices.return_value = [*mock_client.get_devices.return_value, buddy]
    await setup_integration(hass, mock_config_entry)

    def keys_for(device_id: str) -> set[str]:
        return {
            entry.unique_id.removeprefix(f"device_{device_id}_")
            for entry in er.async_entries_for_config_entry(
                entity_registry, mock_config_entry.entry_id
            )
            if entry.unique_id.startswith(f"device_{device_id}_")
        }

    assert keys_for(DEVICE_ID).isdisjoint(BUDDY_ONLY)
    assert set(BUDDY_ONLY) <= keys_for(BUDDY_ID)
    # Everything else is offered on both.
    assert keys_for(DEVICE_ID) == keys_for(BUDDY_ID) - set(BUDDY_ONLY)


async def test_duplicated_attributes_stay_on_the_frame(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Values the frame also reports are not duplicated onto the device."""
    await setup_integration(hass, mock_config_entry)

    device_entities = {
        entry.unique_id.removeprefix(f"device_{DEVICE_ID}_")
        for entry in er.async_entries_for_config_entry(entity_registry, mock_config_entry.entry_id)
        if entry.unique_id.startswith("device_")
    }
    # Exactly the device-only attributes, and nothing the frame also reports.
    # The Buddy-only settings are absent because this display is a calendar.
    assert device_entities == {
        # Writable: controls.
        "blur_effect",
        "brightness",
        "show_caption",
        "show_heart",
        "side_by_side",
        "sleeps_at",
        "slideshow_speed",
        "wakes_at",
        # Read-only: the API will not accept writes to these.
        "sleep_mode",
    }


async def test_state_follows_the_device(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    devices: list[Device],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Changing a setting at the frame shows up here."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(BLUR).state == "on"

    mock_client.get_devices.return_value = [replace(devices[0], blur_effect=False)]
    await async_poll(hass, freezer)

    assert hass.states.get(BLUR).state == "off"


async def test_device_removed_from_frame(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Unregistering the hardware leaves its entities unavailable."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(BLUR).state == "on"

    mock_client.get_devices.return_value = []
    await async_poll(hass, freezer)

    assert hass.states.get(BLUR).state == STATE_UNAVAILABLE
    assert hass.states.get(SLEEP_MODE).state == STATE_UNAVAILABLE


async def test_multiple_devices_on_one_frame(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    devices: list[Device],
    device_registry: dr.DeviceRegistry,
) -> None:
    """The case this modelling exists for: two displays in one household."""
    bedroom = Device.from_resource(
        {
            "type": "device",
            "id": "5759924",
            "attributes": {
                "name": "Bedroom Calendar",
                "blur_effect": False,
                "sleep_mode": "clock",
            },
        }
    )
    mock_client.get_devices.return_value = [*devices, bedroom]
    await setup_integration(hass, mock_config_entry)

    frame = device_registry.async_get_device(identifiers={(DOMAIN, FRAME_ID)})
    for device_id in (DEVICE_ID, "5759924"):
        entry = device_registry.async_get_device(identifiers={(DOMAIN, f"device_{device_id}")})
        assert entry is not None
        assert entry.via_device_id == frame.id

    # Each display reports its own state, which is the whole point.
    assert hass.states.get(BLUR).state == "on"
    assert hass.states.get("switch.bedroom_calendar_blur_effect").state == "off"
    assert hass.states.get("sensor.bedroom_calendar_sleep_mode").state == "clock"


async def test_frame_with_no_devices(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A frame with nothing attached is not an error."""
    mock_client.get_devices.return_value = []
    await setup_integration(hass, mock_config_entry)

    assert not [
        entry
        for entry in er.async_entries_for_config_entry(entity_registry, mock_config_entry.entry_id)
        if entry.unique_id.startswith("device_")
    ]
