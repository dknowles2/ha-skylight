"""Tests for the Skylight sensor platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pyskylight.exceptions import ApiError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.skylight.const import (
    ATTR_POINTS,
    DOMAIN,
    SERVICE_AWARD_POINTS,
    SERVICE_DEDUCT_POINTS,
)

from .conftest import CATEGORY_ID, FRAME_ID, setup_integration


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_all_entities(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Every entity the platform creates, pinned to a snapshot.

    Lifetime points are disabled by default, so the snapshot needs them turned
    on to cover them at all.
    """
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

    entries = er.async_entries_for_config_entry(entity_registry, mock_config_entry.entry_id)
    # Device sensors are unaffected; only the per-profile ones disappear.
    assert not [entry for entry in entries if "chores" in entry.unique_id]


POINTS = "sensor.kitchen_alex_reward_points"


async def test_awarding_points(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Giving a child stars, from the profile's own points sensor."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_AWARD_POINTS,
        {ATTR_ENTITY_ID: POINTS, ATTR_POINTS: 3},
        blocking=True,
    )

    mock_client.update_reward_points.assert_awaited_once_with(FRAME_ID, [CATEGORY_ID], 3)


async def test_deducting_points_sends_a_negative(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The action takes a positive number; the API takes a signed delta."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_DEDUCT_POINTS,
        {ATTR_ENTITY_ID: POINTS, ATTR_POINTS: 2},
        blocking=True,
    )

    mock_client.update_reward_points.assert_awaited_once_with(FRAME_ID, [CATEGORY_ID], -2)


@pytest.mark.parametrize("service", [SERVICE_AWARD_POINTS, SERVICE_DEDUCT_POINTS])
@pytest.mark.parametrize("points", [0, -1])
async def test_a_change_of_zero_or_less_is_rejected(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    service: str,
    points: int,
) -> None:
    """Skylight answers 422 to a change of zero, so the schema stops it first."""
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, service, {ATTR_ENTITY_ID: POINTS, ATTR_POINTS: points}, blocking=True
        )

    mock_client.update_reward_points.assert_not_awaited()


async def test_points_cannot_be_moved_on_another_sensor(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The action targets sensors, so aiming it at a chore count must say no."""
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError, match="not a reward points sensor"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_AWARD_POINTS,
            {ATTR_ENTITY_ID: "sensor.kitchen_alex_chores_due", ATTR_POINTS: 1},
            blocking=True,
        )

    mock_client.update_reward_points.assert_not_awaited()


async def test_a_failed_change_surfaces(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A rejected change is visible rather than silently doing nothing."""
    await setup_integration(hass, mock_config_entry)
    mock_client.update_reward_points.side_effect = ApiError(422, "nope")

    with pytest.raises(HomeAssistantError, match="Could not change the Skylight reward points"):
        await hass.services.async_call(
            DOMAIN, SERVICE_AWARD_POINTS, {ATTR_ENTITY_ID: POINTS, ATTR_POINTS: 1}, blocking=True
        )


async def test_lifetime_points_can_fall(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """So it must not be TOTAL_INCREASING.

    Deducting points lowers the lifetime figure too, verified on a test frame.
    Home Assistant reads a fall in a TOTAL_INCREASING sensor as a counter reset,
    which would corrupt the long-term statistics.
    """
    await setup_integration(hass, mock_config_entry)

    entry = entity_registry.async_get("sensor.kitchen_alex_lifetime_points")
    assert entry.capabilities["state_class"] is SensorStateClass.TOTAL
