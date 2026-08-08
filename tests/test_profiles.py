"""Tests for which Skylight categories become entities.

Skylight's "categories" mix family members with calendar buckets — a shared
`Family` calendar, a `Family Birthdays` feed, an `(unused)` leftover. Only the
people can hold chores or reward points, so only they get entities.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pyskylight.models import Category
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import BUCKET_CATEGORY_ID, CATEGORY_ID, FRAME_ID, setup_integration


def _unique_ids(entity_registry: er.EntityRegistry, entry: MockConfigEntry) -> set[str]:
    return {
        registry_entry.unique_id
        for registry_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    }


async def test_calendar_buckets_get_no_entities(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A category that is not a person creates nothing."""
    await setup_integration(hass, mock_config_entry)

    assert not [
        unique_id
        for unique_id in _unique_ids(entity_registry, mock_config_entry)
        if unique_id.startswith(f"{FRAME_ID}_{BUCKET_CATEGORY_ID}_")
    ]
    assert hass.states.get("todo.the_knowles_family_birthdays_chores") is None
    # The real profiles are untouched.
    assert f"{FRAME_ID}_{CATEGORY_ID}_chores" in _unique_ids(entity_registry, mock_config_entry)


@pytest.mark.parametrize(
    "suffix",
    ["chores", "chores_due", "chores_completed", "reward_points"],
)
async def test_existing_bucket_entities_are_removed(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    suffix: str,
) -> None:
    """Entities an earlier version created for a bucket are cleaned up.

    `reward_points` matters most: its unique id has an underscore of its own, so
    a prefix match that split on the last underscore would miss it.
    """
    mock_config_entry.add_to_hass(hass)
    stale = entity_registry.async_get_or_create(
        "sensor",
        "skylight",
        f"{FRAME_ID}_{BUCKET_CATEGORY_ID}_{suffix}",
        config_entry=mock_config_entry,
    )

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get(stale.entity_id) is None


async def test_unrelated_entities_survive_the_cleanup(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Only category-scoped ids are matched, not lists, calendars or displays."""
    await setup_integration(hass, mock_config_entry)

    unique_ids = _unique_ids(entity_registry, mock_config_entry)
    assert f"{FRAME_ID}_calendar" in unique_ids
    assert any(unique_id.startswith("device_") for unique_id in unique_ids)
    assert any(
        unique_id.startswith(f"{FRAME_ID}_") and unique_id.endswith("_reward_points")
        for unique_id in unique_ids
    )


async def test_a_failed_frame_cannot_delete_anything(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    categories: list[Category],
) -> None:
    """Categories missing from a refresh are not treated as buckets.

    A frame that failed to poll drops out of the snapshot entirely. Deleting on
    absence would wipe a household's entities over one bad request.
    """
    mock_config_entry.add_to_hass(hass)
    existing = entity_registry.async_get_or_create(
        "todo",
        "skylight",
        f"{FRAME_ID}_{CATEGORY_ID}_chores",
        config_entry=mock_config_entry,
    )
    mock_client.get_categories.return_value = []

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get(existing.entity_id) is not None
