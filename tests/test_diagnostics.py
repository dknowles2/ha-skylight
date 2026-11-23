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


async def test_diagnostics_redact_personal_data(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """No personal value from the fixtures should survive redaction.

    This asserts against the known personal values the test fixtures set —
    the frame's name, household name, profile names, chore summaries, list
    and list-item labels, and the account email — rather than against the
    shape of ``TO_REDACT_DATA``, so it would have caught the frame `name`
    (e.g. "Kitchen") and the unredacted `reward_points` escaping before.
    """
    await setup_integration(hass, mock_config_entry)
    result = await get_diagnostics_for_config_entry(hass, hass_client, mock_config_entry)
    serialized = str(result)

    personal_values = [
        "user@example.com",  # account email / config entry username
        "hunter2",  # account password
        "Kitchen",  # frame name
        "The Knowles",  # household name
        "Alex",  # profile label (person)
        "Sam",  # profile label (person)
        "Dishes",  # chore summary
        "Recycling",  # chore summary
        "Homework",  # chore summary
        "Laundry",  # chore summary
        "Grocery List",  # list label
        "To Do",  # list label
        "Milk",  # list item label
        "Eggs",  # list item label
        "Bread",  # list item label
    ]
    for value in personal_values:
        assert value not in serialized, f"{value!r} leaked into diagnostics"
