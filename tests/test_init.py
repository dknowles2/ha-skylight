"""Tests for setting up and tearing down the Skylight config entry."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pyskylight.exceptions import ApiError, AuthenticationError, NotAuthorizedError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
)

from custom_components.skylight.const import DOMAIN

from .conftest import FRAME_ID, SECOND_FRAME_ID, async_poll, setup_integration


async def test_setup_and_unload(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The entry loads, then unloads cleanly."""
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


async def test_device_registered_per_frame(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Each frame becomes one device."""
    await setup_integration(hass, mock_config_entry)

    device = device_registry.async_get_device(identifiers={(DOMAIN, FRAME_ID)})
    assert device is not None
    assert device.manufacturer == "Skylight"
    assert device.name == "Kitchen"
    assert device.model == "skylight-cal-15"


@pytest.mark.parametrize("error", [ApiError(500, "boom"), ApiError(429, "slow down")])
async def test_api_failure_retries(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    error: Exception,
) -> None:
    """A transient API failure leaves the entry in retry, not failed."""
    mock_client.get_frames.side_effect = error
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.parametrize(
    "error", [AuthenticationError("nope"), NotAuthorizedError(401, "Invalid token")]
)
async def test_auth_failure_starts_reauth(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    error: Exception,
) -> None:
    """Bad credentials ask the user to re-authenticate rather than retrying."""
    mock_client.get_frames.side_effect = error
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR

    flows = [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"].get("source") == "reauth"
    ]
    assert len(flows) == 1


async def test_polling_updates_state(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    chores: list,
) -> None:
    """State follows the API on the next poll."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get("sensor.kitchen_alex_chores_due").state == "2"

    # Alex finishes the dishes.
    mock_client.get_chores.return_value = chores[1:]
    await async_poll(hass, freezer)

    assert hass.states.get("sensor.kitchen_alex_chores_due").state == "1"


async def test_entities_go_unavailable_on_failure(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failed refresh marks entities unavailable rather than going stale."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get("sensor.kitchen_alex_chores_due").state == "2"

    mock_client.get_frames.side_effect = ApiError(500, "boom")
    await async_poll(hass, freezer)

    assert hass.states.get("sensor.kitchen_alex_chores_due").state == "unavailable"


async def test_one_frame_failing_does_not_blank_the_others(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    two_frames: list,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An account can hold several frames; one erroring must not take out the rest."""
    mock_client.get_frames.return_value = two_frames

    def categories_for(frame_id: str) -> list:
        if frame_id == SECOND_FRAME_ID:
            raise ApiError(500, "that frame is having a bad day")
        return mock_client.get_categories.return_value

    mock_client.get_categories.side_effect = categories_for
    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    # The healthy frame is fully present...
    assert hass.states.get("sensor.kitchen_alex_chores_due").state == "2"
    # ...and the broken one simply is not in the snapshot.
    coordinator = mock_config_entry.runtime_data
    assert list(coordinator.data) == [FRAME_ID]
    assert "Could not update Skylight frame 5594280" in caplog.text


async def test_every_frame_failing_is_an_update_failure(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """If nothing could be fetched, back off rather than report an empty account."""
    mock_client.get_categories.side_effect = ApiError(500, "boom")
    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_auth_failure_on_one_frame_starts_reauth(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    two_frames: list,
) -> None:
    """A rejected token is an account-level problem, not a per-frame one."""
    mock_client.get_frames.return_value = two_frames
    mock_client.get_categories.side_effect = NotAuthorizedError(401, "Invalid token")
    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"].get("source") == "reauth"
    ]


async def test_a_frame_recovering_comes_back(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    two_frames: list,
    freezer: FrozenDateTimeFactory,
    categories: list,
) -> None:
    """A frame that failed once reappears on the next successful poll."""
    mock_client.get_frames.return_value = two_frames
    mock_client.get_categories.side_effect = ApiError(500, "boom")
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY

    mock_client.get_categories.side_effect = None
    mock_client.get_categories.return_value = categories
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert sorted(mock_config_entry.runtime_data.data) == sorted([FRAME_ID, SECOND_FRAME_ID])
