"""Tests for choosing which frames the integration exposes.

An account can hold frames nobody wants in Home Assistant — a throwaway test
frame, or one shared by a relative. The setting is opt-in: absent or empty means
every frame, so an account that never opens the options flow is unaffected.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pyskylight.models import Frame
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skylight.const import CONF_FRAMES, CONF_PROFILE_MAP, DOMAIN

from .conftest import FRAME_ID, SECOND_FRAME_ID, setup_integration


async def open_frames(hass: HomeAssistant, entry: MockConfigEntry):
    """Open the options flow and pick the frame-selection step."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    return await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "frames"}
    )


async def test_every_frame_is_exposed_by_default(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    two_frames: list[Frame],
) -> None:
    """No setting means what it has always meant: all of them."""
    mock_client.get_frames.return_value = two_frames
    await setup_integration(hass, mock_config_entry)

    assert sorted(mock_config_entry.runtime_data.data) == sorted([FRAME_ID, SECOND_FRAME_ID])


async def test_choosing_one_frame(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    two_frames: list[Frame],
) -> None:
    """The unchosen frame stops being polled at all."""
    mock_client.get_frames.return_value = two_frames
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, options={CONF_FRAMES: [FRAME_ID]})
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert list(mock_config_entry.runtime_data.data) == [FRAME_ID]
    assert hass.states.get("sensor.playroom_alex_chores_due") is None


async def test_the_form_offers_frames_that_are_currently_excluded(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    two_frames: list[Frame],
) -> None:
    """Otherwise excluding a frame would be a one-way door.

    The choices come from every frame the account owns, not from the ones being
    exposed — which is why the coordinator keeps that list separately.
    """
    mock_client.get_frames.return_value = two_frames
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, options={CONF_FRAMES: [FRAME_ID]})
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await open_frames(hass, mock_config_entry)

    selector = result["data_schema"].schema[CONF_FRAMES]
    values = [option["value"] for option in selector.config["options"]]
    assert sorted(values) == sorted([FRAME_ID, SECOND_FRAME_ID])


async def test_choosing_them_all_is_stored_as_no_choice(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    two_frames: list[Frame],
) -> None:
    """A frame added to the account later should appear on its own.

    Storing today's full list would freeze the account as it is now, and the
    next frame would be silently excluded by a choice made before it existed.
    """
    mock_client.get_frames.return_value = two_frames
    await setup_integration(hass, mock_config_entry)

    result = await open_frames(hass, mock_config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_FRAMES: [FRAME_ID, SECOND_FRAME_ID]}
    )

    assert result["type"] == "create_entry"
    assert mock_config_entry.options[CONF_FRAMES] == []


async def test_the_two_settings_do_not_overwrite_each_other(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    two_frames: list[Frame],
) -> None:
    """`async_create_entry` replaces the whole options dict, so each step merges."""
    mock_client.get_frames.return_value = two_frames
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_PROFILE_MAP: {"77": "person.alex"}}
    )
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await open_frames(hass, mock_config_entry)
    await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_FRAMES: [FRAME_ID]}
    )
    await hass.async_block_till_done()

    assert mock_config_entry.options[CONF_PROFILE_MAP] == {"77": "person.alex"}
    assert mock_config_entry.options[CONF_FRAMES] == [FRAME_ID]


async def test_changing_the_setting_reloads(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    two_frames: list[Frame],
) -> None:
    """Only a reload can build or tear down whole devices."""
    mock_client.get_frames.return_value = two_frames
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get("sensor.playroom_alex_chores_due") is not None

    result = await open_frames(hass, mock_config_entry)
    await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_FRAMES: [FRAME_ID]}
    )
    await hass.async_block_till_done()

    assert list(mock_config_entry.runtime_data.data) == [FRAME_ID]


async def test_an_excluded_frame_leaves_nothing_behind(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    two_frames: list[Frame],
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Excluding a frame is an explicit choice, so its device is deleted.

    Left alone it would sit in the registry unavailable for ever. This is safe
    in a way the other cleanups have to be careful about: it keys on the user's
    choice, never on a frame being absent from a refresh.
    """
    mock_client.get_frames.return_value = two_frames
    await setup_integration(hass, mock_config_entry)
    assert device_registry.async_get_device(identifiers={(DOMAIN, SECOND_FRAME_ID)})

    result = await open_frames(hass, mock_config_entry)
    await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_FRAMES: [FRAME_ID]}
    )
    await hass.async_block_till_done()

    assert device_registry.async_get_device(identifiers={(DOMAIN, SECOND_FRAME_ID)}) is None
    assert device_registry.async_get_device(identifiers={(DOMAIN, FRAME_ID)})
    assert not [
        entry
        for entry in er.async_entries_for_config_entry(entity_registry, mock_config_entry.entry_id)
        if entry.unique_id.startswith(f"{SECOND_FRAME_ID}_")
    ]


async def test_a_display_goes_with_its_frame(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    two_frames: list[Frame],
    device_registry: dr.DeviceRegistry,
) -> None:
    """Displays hang off the frame by `via_device` and belong to nothing without it."""
    mock_client.get_frames.return_value = two_frames
    await setup_integration(hass, mock_config_entry)
    frame = device_registry.async_get_device(identifiers={(DOMAIN, SECOND_FRAME_ID)})
    display = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "device_9999")},
        via_device=(DOMAIN, SECOND_FRAME_ID),
    )
    assert display.via_device_id == frame.id

    result = await open_frames(hass, mock_config_entry)
    await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_FRAMES: [FRAME_ID]}
    )
    await hass.async_block_till_done()

    assert device_registry.async_get_device(identifiers={(DOMAIN, "device_9999")}) is None


async def test_the_form_with_no_frames_at_all(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    two_frames: list[Frame],
) -> None:
    """An empty form would be a dead end, so it says why instead."""
    mock_client.get_frames.return_value = two_frames
    await setup_integration(hass, mock_config_entry)
    mock_config_entry.runtime_data.available_frames = {}

    result = await open_frames(hass, mock_config_entry)

    assert result["type"] == "abort"
    assert result["reason"] == "no_frames"


async def test_a_chosen_frame_that_left_the_account(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The setting is not rewritten from a poll; it simply matches nothing."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, options={CONF_FRAMES: ["gone-frame"]})
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.runtime_data.data == {}
    assert mock_config_entry.options[CONF_FRAMES] == ["gone-frame"]
