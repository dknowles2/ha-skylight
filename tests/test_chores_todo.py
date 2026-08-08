"""Tests for chores exposed as to-do lists."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.todo import DOMAIN as TODO_DOMAIN
from homeassistant.components.todo import TodoItem
from homeassistant.components.todo.const import TodoItemStatus
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pyskylight.exceptions import ApiError
from pyskylight.models import Chore
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
)

from custom_components.skylight.todo import SkylightChoreListEntity

from .conftest import CATEGORY_ID, FRAME_ID, async_poll, setup_integration

ALEX_CHORES = "todo.kitchen_alex_chores"
SAM_CHORES = "todo.kitchen_sam_chores"


def _entity(hass: HomeAssistant, entity_id: str) -> SkylightChoreListEntity:
    component = hass.data["entity_components"][TODO_DOMAIN]
    entity = component.get_entity(entity_id)
    assert isinstance(entity, SkylightChoreListEntity)
    return entity


async def test_one_list_per_profile(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Each profile gets a chore list, stating its open count."""
    await setup_integration(hass, mock_config_entry)

    # Alex has two open and one done; Sam has one open.
    assert hass.states.get(ALEX_CHORES).state == "2"
    assert hass.states.get(SAM_CHORES).state == "1"


async def test_items_carry_due_dates(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    hass_ws_client,
) -> None:
    """Chores appear with their status and start date."""
    await setup_integration(hass, mock_config_entry)
    client = await hass_ws_client()
    await client.send_json_auto_id({"type": "todo/item/list", "entity_id": ALEX_CHORES})
    result = await client.receive_json()

    items = result["result"]["items"]
    assert [(i["summary"], i["status"]) for i in items] == [
        ("Dishes", "needs_action"),
        ("Recycling", "needs_action"),
        ("Homework", "completed"),
    ]
    assert items[0]["due"] == "2026-08-07"


async def test_complete_a_one_off_chore(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A one-off chore is completed without an instance date."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {ATTR_ENTITY_ID: ALEX_CHORES, "item": "Dishes", "status": "completed"},
        blocking=True,
    )

    mock_client.complete_chore.assert_awaited_once_with(FRAME_ID, "1", instance_date=None)


async def test_reopen_a_chore(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Unchecking a completed chore calls the uncomplete endpoint."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {ATTR_ENTITY_ID: ALEX_CHORES, "item": "Homework", "status": "needs_action"},
        blocking=True,
    )

    mock_client.uncomplete_chore.assert_awaited_once_with(FRAME_ID, "3", instance_date=None)
    mock_client.complete_chore.assert_not_awaited()


async def test_complete_a_recurring_chore_passes_the_occurrence(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
) -> None:
    """A recurring chore must say which occurrence is being completed."""
    recurring = Chore.from_resource(
        {
            "type": "chore",
            "id": "9-2026-08-07",
            "attributes": {
                "id": "9-2026-08-07",
                "group": "9",
                "summary": "Recycling day",
                "start": "2026-08-07",
                "recurring": True,
                "recurrence_set": ["RRULE:FREQ=WEEKLY"],
            },
            "relationships": {"category": {"data": {"type": "category", "id": CATEGORY_ID}}},
        }
    )
    mock_client.get_chores.return_value = [recurring]
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {ATTR_ENTITY_ID: ALEX_CHORES, "item": "Recycling day", "status": "completed"},
        blocking=True,
    )

    mock_client.complete_chore.assert_awaited_once_with(
        FRAME_ID, "9", instance_date=date(2026, 8, 7)
    )


async def test_status_unchanged_makes_no_call(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Renaming a chore does not also re-send its current status."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {ATTR_ENTITY_ID: ALEX_CHORES, "item": "Dishes", "rename": "Wash up"},
        blocking=True,
    )

    mock_client.complete_chore.assert_not_awaited()
    mock_client.uncomplete_chore.assert_not_awaited()
    mock_client.update_chore.assert_awaited_once_with(FRAME_ID, "1", summary="Wash up")


async def test_reschedule_a_chore(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Changing the due date moves the chore's start."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {ATTR_ENTITY_ID: ALEX_CHORES, "item": "Dishes", "due_date": "2026-08-09"},
        blocking=True,
    )

    mock_client.update_chore.assert_awaited_once_with(FRAME_ID, "1", start="2026-08-09")


async def test_create_a_chore(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A new chore is assigned to the profile whose list it was added to."""
    freezer.move_to("2026-08-07 12:00:00+00:00")
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        "add_item",
        {ATTR_ENTITY_ID: ALEX_CHORES, "item": "Sweep"},
        blocking=True,
    )

    mock_client.create_chore.assert_awaited_once_with(
        FRAME_ID, "Sweep", CATEGORY_ID, start=date(2026, 8, 7)
    )


async def test_create_a_chore_with_a_due_date(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A due date becomes the chore's start date."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        "add_item",
        {ATTR_ENTITY_ID: ALEX_CHORES, "item": "Sweep", "due_date": "2026-08-20"},
        blocking=True,
    )

    mock_client.create_chore.assert_awaited_once_with(
        FRAME_ID, "Sweep", CATEGORY_ID, start=date(2026, 8, 20)
    )


async def test_delete_a_one_off_chore(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A one-off chore is deleted without apply_to, which the API rejects."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        "remove_item",
        {ATTR_ENTITY_ID: ALEX_CHORES, "item": "Dishes"},
        blocking=True,
    )

    mock_client.delete_chore.assert_awaited_once_with(FRAME_ID, "1", apply_to=None)


async def test_delete_a_recurring_chore_applies_to_all(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A recurring chore needs apply_to, which the API requires."""
    recurring = Chore.from_resource(
        {
            "type": "chore",
            "id": "9-2026-08-07",
            "attributes": {
                "id": "9-2026-08-07",
                "group": "9",
                "summary": "Recycling day",
                "start": "2026-08-07",
                "recurring": True,
            },
            "relationships": {"category": {"data": {"type": "category", "id": CATEGORY_ID}}},
        }
    )
    mock_client.get_chores.return_value = [recurring]
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        "remove_item",
        {ATTR_ENTITY_ID: ALEX_CHORES, "item": "Recycling day"},
        blocking=True,
    )

    mock_client.delete_chore.assert_awaited_once_with(FRAME_ID, "9", apply_to="all")


async def test_unknown_chore_is_rejected(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Acting on a chore that is not on the list is an error, not a no-op."""
    await setup_integration(hass, mock_config_entry)
    entity = _entity(hass, ALEX_CHORES)

    with pytest.raises(HomeAssistantError, match="unknown chore"):
        await entity.async_update_todo_item(
            TodoItem(uid="nope", summary="x", status=TodoItemStatus.COMPLETED)
        )


async def test_chore_without_an_id_is_rejected(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A chore the API returned without a group id cannot be addressed."""
    headless = Chore.from_resource(
        {
            "type": "chore",
            "id": "x-2026-08-07",
            "attributes": {"summary": "Odd", "start": "2026-08-07"},
            "relationships": {"category": {"data": {"type": "category", "id": CATEGORY_ID}}},
        }
    )
    mock_client.get_chores.return_value = [headless]
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError, match="has no id"):
        await _entity(hass, ALEX_CHORES).async_update_todo_item(
            TodoItem(uid="x-2026-08-07", summary="Odd", status=TodoItemStatus.COMPLETED)
        )

    # Deleting skips it rather than raising, so a bulk delete still progresses.
    await _entity(hass, ALEX_CHORES).async_delete_todo_items(["x-2026-08-07"])
    mock_client.delete_chore.assert_not_awaited()


async def test_write_error_surfaces(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A failed completion raises rather than silently doing nothing."""
    await setup_integration(hass, mock_config_entry)
    mock_client.complete_chore.side_effect = ApiError(422, "nope")

    with pytest.raises(HomeAssistantError, match="Could not update the Skylight chore"):
        await hass.services.async_call(
            TODO_DOMAIN,
            "update_item",
            {ATTR_ENTITY_ID: ALEX_CHORES, "item": "Dishes", "status": "completed"},
            blocking=True,
        )


async def test_profile_removed_from_frame(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    categories: list,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A removed profile leaves its chore list unavailable."""

    await setup_integration(hass, mock_config_entry)
    entity = _entity(hass, SAM_CHORES)
    assert entity.todo_items is not None

    mock_client.get_categories.return_value = categories[:1]
    await async_poll(hass, freezer)

    assert entity.todo_items is None
    assert hass.states.get(SAM_CHORES).state == "unavailable"
