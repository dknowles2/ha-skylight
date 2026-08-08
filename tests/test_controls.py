"""Tests for the display control platforms.

Every field exercised here was verified writable against real hardware; the
values and enum members match what the live display accepted.

The household holds two displays on purpose: a calendar and a Buddy. Some of
these settings exist only on a Buddy — see `is_buddy` — so testing them against
a calendar would be testing a control that is no longer built.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import time
from unittest.mock import AsyncMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.number import (
    ATTR_VALUE as NUMBER_VALUE,
)
from homeassistant.components.number import (
    DOMAIN as NUMBER_DOMAIN,
)
from homeassistant.components.select import ATTR_OPTION
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.time import ATTR_TIME
from homeassistant.components.time import DOMAIN as TIME_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pyskylight.exceptions import ApiError
from pyskylight.models import Device
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import BUDDY_ID, DEVICE_ID, FRAME_ID, async_poll, setup_integration

NIGHTLIGHT = "switch.bedside_buddy_nightlight"
BRIGHTNESS = "number.kitchen_calendar_brightness"
SLEEPS_AT = "time.kitchen_calendar_sleeps_at"
COLOR = "select.bedside_buddy_nightlight_color"


@pytest.fixture(autouse=True)
def _household(mock_client: AsyncMock, devices: list[Device], buddy: Device) -> None:
    """Put both a calendar and a Buddy on the frame."""
    mock_client.get_devices.return_value = [*devices, buddy]


async def test_initial_states(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Controls read their current value from the display."""
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(NIGHTLIGHT).state == "off"
    assert hass.states.get(BRIGHTNESS).state == "255"
    assert hass.states.get(SLEEPS_AT).state == "23:00:00"
    assert hass.states.get("time.kitchen_calendar_wakes_at").state == "06:00:00"
    assert hass.states.get(COLOR).state == "off"
    assert hass.states.get("number.kitchen_calendar_slideshow_speed").state == "10"
    assert hass.states.get("switch.kitchen_calendar_show_captions").state == "on"


@pytest.mark.parametrize(
    ("entity_id", "service", "device_id", "expected"),
    [
        (NIGHTLIGHT, SERVICE_TURN_ON, BUDDY_ID, {"nightlight": True}),
        (NIGHTLIGHT, SERVICE_TURN_OFF, BUDDY_ID, {"nightlight": False}),
        (
            "switch.kitchen_calendar_show_captions",
            SERVICE_TURN_OFF,
            DEVICE_ID,
            {"show_caption": False},
        ),
        (
            "switch.kitchen_calendar_blur_effect",
            SERVICE_TURN_OFF,
            DEVICE_ID,
            {"blur_effect": False},
        ),
        (
            "switch.kitchen_calendar_side_by_side",
            SERVICE_TURN_ON,
            DEVICE_ID,
            {"side_by_side": True},
        ),
        ("switch.kitchen_calendar_show_heart", SERVICE_TURN_ON, DEVICE_ID, {"show_heart": True}),
    ],
)
async def test_switches(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_id: str,
    service: str,
    device_id: str,
    expected: dict,
) -> None:
    """Each switch writes its own field to the display it belongs to."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    mock_client.update_device.assert_awaited_once_with(FRAME_ID, device_id, **expected)


@pytest.mark.parametrize(
    ("entity_id", "value", "device_id", "expected"),
    [
        (BRIGHTNESS, 180, DEVICE_ID, {"brightness": 180}),
        (
            "number.bedside_buddy_nightlight_brightness",
            40,
            BUDDY_ID,
            {"nightlight_brightness": 40},
        ),
        ("number.bedside_buddy_sleep_sound_volume", 50, BUDDY_ID, {"sleep_sound_volume": 50}),
        ("number.kitchen_calendar_slideshow_speed", 15, DEVICE_ID, {"slideshow_speed": 15}),
    ],
)
async def test_numbers(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_id: str,
    value: int,
    device_id: str,
    expected: dict,
) -> None:
    """Numbers write integers, since the API rejects floats for these fields."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        NUMBER_DOMAIN,
        "set_value",
        {ATTR_ENTITY_ID: entity_id, NUMBER_VALUE: value},
        blocking=True,
    )

    mock_client.update_device.assert_awaited_once_with(FRAME_ID, device_id, **expected)
    (sent,) = [v for v in mock_client.update_device.await_args.kwargs.values()]
    assert isinstance(sent, int)


async def test_times_use_the_api_format(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The API stores "HH:MM", not Home Assistant's "HH:MM:SS"."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TIME_DOMAIN,
        "set_value",
        {ATTR_ENTITY_ID: SLEEPS_AT, ATTR_TIME: time(22, 30)},
        blocking=True,
    )

    mock_client.update_device.assert_awaited_once_with(FRAME_ID, DEVICE_ID, sleeps_at="22:30")


async def test_select_offers_only_accepted_colors(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The option list is the set the live API accepted, nothing more."""
    await setup_integration(hass, mock_config_entry)

    options = hass.states.get(COLOR).attributes["options"]
    assert options == ["off", "red", "orange", "yellow", "green", "blue", "pink"]
    # Rejected by the API with "Nightlight color is not included in the list".
    assert "white" not in options
    assert "purple" not in options

    await hass.services.async_call(
        SELECT_DOMAIN,
        "select_option",
        {ATTR_ENTITY_ID: COLOR, ATTR_OPTION: "blue"},
        blocking=True,
    )
    mock_client.update_device.assert_awaited_once_with(FRAME_ID, BUDDY_ID, nightlight_color="blue")


async def test_unknown_color_reads_as_none(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    buddy: Device,
) -> None:
    """A colour we do not model must not crash the select entity."""
    mock_client.get_devices.return_value = [replace(buddy, nightlight_color="chartreuse")]
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(COLOR).state == "unknown"


async def test_unparseable_time_reads_as_none(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    devices: list[Device],
) -> None:
    """A malformed time from the API must not crash the entity."""
    mock_client.get_devices.return_value = [replace(devices[0], sleeps_at="whenever")]
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(SLEEPS_AT).state == "unknown"


@pytest.mark.parametrize(
    ("domain", "service", "entity_id", "data", "field", "value", "expected_state", "on_buddy"),
    [
        (SWITCH_DOMAIN, SERVICE_TURN_ON, NIGHTLIGHT, {}, "nightlight", True, "on", True),
        (
            NUMBER_DOMAIN,
            "set_value",
            BRIGHTNESS,
            {NUMBER_VALUE: 180},
            "brightness",
            180,
            "180",
            False,
        ),
        (
            SELECT_DOMAIN,
            "select_option",
            COLOR,
            {ATTR_OPTION: "blue"},
            "nightlight_color",
            "blue",
            "blue",
            True,
        ),
        (
            TIME_DOMAIN,
            "set_value",
            SLEEPS_AT,
            {ATTR_TIME: time(22, 30)},
            "sleeps_at",
            "22:30",
            "22:30:00",
            False,
        ),
    ],
)
async def test_entity_updates_immediately_after_a_write(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    devices: list[Device],
    buddy: Device,
    domain: str,
    service: str,
    entity_id: str,
    data: dict,
    field: str,
    value: object,
    expected_state: str,
    on_buddy: bool,
) -> None:
    """The control must not snap back to its old value.

    `async_request_refresh()` is debounced, so relying on it alone leaves the
    entity showing the previous value for seconds after the user acted. Caught
    against real hardware, and pinned here.
    """
    await setup_integration(hass, mock_config_entry)
    # The API echoes the updated device; the write path must use that response.
    written = buddy if on_buddy else devices[0]
    mock_client.update_device.return_value = replace(written, **{field: value})

    await hass.services.async_call(
        domain, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
    )

    assert hass.states.get(entity_id).state == expected_state


async def test_write_also_requests_a_refresh(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Beyond the immediate update, a refresh reconciles anything else."""
    await setup_integration(hass, mock_config_entry)
    mock_client.get_devices.reset_mock()

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: NIGHTLIGHT}, blocking=True
    )

    assert mock_client.get_devices.await_count == 1


async def test_write_failure_surfaces(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A rejected write raises rather than silently doing nothing."""
    await setup_integration(hass, mock_config_entry)
    mock_client.update_device.side_effect = ApiError(422, "nope")

    with pytest.raises(HomeAssistantError, match="Could not change the Skylight display"):
        await hass.services.async_call(
            SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: NIGHTLIGHT}, blocking=True
        )


async def test_controls_follow_the_display(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    devices: list[Device],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A change made at the frame shows up on the next poll."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(BRIGHTNESS).state == "255"

    mock_client.get_devices.return_value = [replace(devices[0], brightness=120)]
    await async_poll(hass, freezer)

    assert hass.states.get(BRIGHTNESS).state == "120"


async def test_controls_unavailable_without_the_display(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Controls for a display that is gone must not look operable."""
    await setup_integration(hass, mock_config_entry)

    mock_client.get_devices.return_value = []
    await async_poll(hass, freezer)

    for entity_id in (NIGHTLIGHT, BRIGHTNESS, SLEEPS_AT, COLOR):
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


async def test_write_survives_a_vanished_frame(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    devices: list[Device],
) -> None:
    """Splicing the response must cope with the frame having gone."""
    await setup_integration(hass, mock_config_entry)
    entity = hass.data["entity_components"][SWITCH_DOMAIN].get_entity(NIGHTLIGHT)

    # The frame disappears between the write being issued and its response.
    coordinator = mock_config_entry.runtime_data
    coordinator.async_set_updated_data({})

    entity._apply(devices[0])  # must not raise
    assert coordinator.data == {}
