"""Tests for the Skylight sensor platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from .conftest import CATEGORY_ID, FRAME_ID, setup_integration


async def test_all_entities(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Every entity the platform creates, pinned to a snapshot."""
    with patch("custom_components.skylight.PLATFORMS", [Platform.SENSOR]):
        await setup_integration(hass, mock_config_entry)

    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.parametrize(
    ("entity_id", "expected"),
    [
        ("sensor.kitchen_alex_chores_due", "2"),
        ("sensor.kitchen_alex_chores_completed", "1"),
        ("sensor.kitchen_alex_reward_points", "12"),
        ("sensor.kitchen_sam_chores_due", "1"),
        ("sensor.kitchen_sam_chores_completed", "0"),
        # Sam has no point balance recorded, which is distinct from zero.
        ("sensor.kitchen_sam_reward_points", STATE_UNKNOWN),
    ],
)
async def test_sensor_values(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_id: str,
    expected: str,
) -> None:
    """Each sensor reports the value derived from the API snapshot."""
    await setup_integration(hass, mock_config_entry)
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    assert state.state == expected


async def test_unique_ids_are_stable_and_distinct(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Unique ids are scoped by frame, profile, and measure."""
    await setup_integration(hass, mock_config_entry)

    entries = er.async_entries_for_config_entry(entity_registry, mock_config_entry.entry_id)
    unique_ids = {entry.unique_id for entry in entries}
    assert len(unique_ids) == len(entries)
    assert f"{FRAME_ID}_{CATEGORY_ID}_chores_due" in unique_ids


async def test_profile_removed_from_frame(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    categories: list,
) -> None:
    """A profile that disappears leaves its entities unavailable, not wrong."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get("sensor.kitchen_sam_chores_due").state == "1"

    mock_client.get_categories.return_value = categories[:1]
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.kitchen_sam_chores_due").state == "unavailable"
    assert hass.states.get("sensor.kitchen_alex_chores_due").state == "2"


async def test_no_categories_creates_no_entities(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A frame with no family profiles is not an error."""
    mock_client.get_categories.return_value = []
    await setup_integration(hass, mock_config_entry)

    assert not er.async_entries_for_config_entry(entity_registry, mock_config_entry.entry_id)
