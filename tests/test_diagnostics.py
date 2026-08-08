"""Tests for Skylight diagnostics."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from syrupy.assertion import SnapshotAssertion

from .conftest import setup_integration


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Diagnostics include the polled data with identifying fields redacted."""
    await setup_integration(hass, mock_config_entry)
    result = await get_diagnostics_for_config_entry(hass, hass_client, mock_config_entry)
    assert result == snapshot


async def test_diagnostics_redact_credentials(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The password must never appear in a diagnostics download."""
    await setup_integration(hass, mock_config_entry)
    result = await get_diagnostics_for_config_entry(hass, hass_client, mock_config_entry)

    assert "hunter2" not in str(result)
    assert "user@example.com" not in str(result)
    assert result["entry"]["password"] == "**REDACTED**"
