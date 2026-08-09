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
from pyskylight.models import Chore
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


def _chore(chore_id: str, summary: str, *, routine: bool, completed: bool) -> Chore:
    """Build a chore that is or is not part of a routine."""
    return Chore.from_resource(
        {
            "type": "chore",
            "id": f"{chore_id}-2026-08-07",
            "attributes": {
                "id": f"{chore_id}-2026-08-07",
                "group": chore_id,
                "summary": summary,
                "start": "2026-08-07",
                "routine": routine,
                "completed_on": "2026-08-07" if completed else None,
            },
            "relationships": {"category": {"data": {"type": "category", "id": CATEGORY_ID}}},
        }
    )


async def test_progress_splits_routine_from_everything_else(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """`routine` is the API's own flag, so the split needs no guessing at the clock."""
    mock_client.get_chores.return_value = [
        _chore("1", "Brush Teeth", routine=True, completed=True),
        _chore("2", "Shower", routine=True, completed=True),
        _chore("3", "Put on PJs", routine=True, completed=False),
        _chore("4", "Put on deodorant", routine=True, completed=False),
        _chore("5", "Summer reading", routine=False, completed=True),
        _chore("6", "Summer maths", routine=False, completed=False),
    ]
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get("sensor.kitchen_alex_routine_progress").state == "50.0"
    assert hass.states.get("sensor.kitchen_alex_other_chores_progress").state == "50.0"
    # Four of six overall, which neither of the two halves says on its own.
    assert hass.states.get("sensor.kitchen_alex_chores_progress").state == "50.0"


async def test_progress_carries_the_counts_behind_it(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A percentage alone does not say out of how many."""
    mock_client.get_chores.return_value = [
        _chore("1", "Brush Teeth", routine=True, completed=True),
        _chore("2", "Shower", routine=True, completed=False),
        _chore("3", "Put on PJs", routine=True, completed=False),
    ]
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get("sensor.kitchen_alex_routine_progress")
    assert state.state == "33.3"
    assert state.attributes["completed"] == 1
    assert state.attributes["due"] == 2
    assert state.attributes["total"] == 3


async def test_progress_is_unknown_with_nothing_to_measure(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Not 0%, and not 100%.

    Both are claims about a chart that does not exist. Sam has no routine chores
    in the fixtures, and two of three profiles on a real household had no chores
    at all, so this is the common case rather than the edge.
    """
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get("sensor.kitchen_alex_routine_progress").state == STATE_UNKNOWN


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
