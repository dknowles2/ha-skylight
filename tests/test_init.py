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
    async_fire_time_changed,
)

from custom_components.skylight.const import DOMAIN, SCAN_INTERVAL

from .conftest import FRAME_ID, setup_integration


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
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

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
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.kitchen_alex_chores_due").state == "unavailable"
