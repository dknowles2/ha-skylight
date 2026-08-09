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

    mock_client.complete_chore.assert_awaited_once_with(FRAME_ID, "1")


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

    mock_client.uncomplete_chore.assert_awaited_once_with(FRAME_ID, "3")
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
        FRAME_ID, "9", instance_date=date(2026, 8, 7), instance_time=None
    )


def _positioned(chore_id: str, summary: str, position: int, start_time: str | None = None) -> Chore:
    """A chore carrying a position, and optionally a time of day."""
    suffix = f"-{start_time.replace(':', '')}" if start_time else ""
    return Chore.from_resource(
        {
            "type": "chore",
            "id": f"{chore_id}-2026-08-07{suffix}",
            "attributes": {
                "id": f"{chore_id}-2026-08-07{suffix}",
                "group": chore_id,
                "summary": summary,
                "start": "2026-08-07",
                "start_time": start_time,
                "position": position,
                "recurring": start_time is not None,
                "recurrence_set": ["RRULE:FREQ=DAILY"] if start_time else [],
            },
            "relationships": {"category": {"data": {"type": "category", "id": CATEGORY_ID}}},
        }
    )


async def test_the_day_is_listed_in_the_order_it_happens(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Morning, then evening, then whatever has no time at all.

    Modelled on a real chart. Skylight numbers `position` from 1 within each
    group rather than across the list, so the daily chores and the one-off
    assignments below both start at 1 — sorting on the number alone interleaves
    two unrelated sets of chores.
    """
    mock_client.get_chores.return_value = [
        _positioned("1", "Brush Teeth", 1, "06:00"),
        _positioned("2", "Shower", 2, "20:00"),
        _positioned("3", "Brush Teeth", 3, "20:00"),
        _positioned("4", "Put on deodorant", 4, "06:00"),
        _positioned("5", "Finish Summer Reading", 1),
        _positioned("6", "Finish Summer Math", 2),
    ]
    await setup_integration(hass, mock_config_entry)

    entity = _entity(hass, ALEX_CHORES)
    assert [(c.summary, c.start_time) for c in entity._chores] == [
        ("Brush Teeth", "06:00"),
        ("Put on deodorant", "06:00"),
        ("Shower", "20:00"),
        ("Brush Teeth", "20:00"),
        ("Finish Summer Reading", None),
        ("Finish Summer Math", None),
    ]


async def test_position_still_orders_within_a_time_of_day(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Reordering on the frame has to keep meaning something."""
    mock_client.get_chores.return_value = [
        _positioned("1", "Third", 12, "06:00"),
        _positioned("2", "First", 2, "06:00"),
        _positioned("3", "Second", 8, "06:00"),
    ]
    await setup_integration(hass, mock_config_entry)

    entity = _entity(hass, ALEX_CHORES)
    assert [c.summary for c in entity._chores] == ["First", "Second", "Third"]


async def test_a_chore_without_a_position_sorts_last(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A missing position must not crash the comparison or jump the queue."""
    unpositioned = Chore.from_resource(
        {
            "type": "chore",
            "id": "9-2026-08-07",
            "attributes": {"id": "9-2026-08-07", "group": "9", "summary": "Nowhere in particular"},
            "relationships": {"category": {"data": {"type": "category", "id": CATEGORY_ID}}},
        }
    )
    mock_client.get_chores.return_value = [unpositioned, _positioned("1", "Somewhere", 4)]
    await setup_integration(hass, mock_config_entry)

    entity = _entity(hass, ALEX_CHORES)
    assert [c.summary for c in entity._chores] == ["Somewhere", "Nowhere in particular"]


def _timed(chore_id: str, summary: str, start_time: str) -> Chore:
    """A recurring chore that repeats at a time of day."""
    return Chore.from_resource(
        {
            "type": "chore",
            "id": f"{chore_id}-2026-08-07-{start_time.replace(':', '')}",
            "attributes": {
                "id": f"{chore_id}-2026-08-07-{start_time.replace(':', '')}",
                "group": chore_id,
                "summary": summary,
                "start": "2026-08-07",
                "start_time": start_time,
                "recurring": True,
                "recurrence_set": ["RRULE:FREQ=DAILY"],
            },
            "relationships": {"category": {"data": {"type": "category", "id": CATEGORY_ID}}},
        }
    )


async def test_chores_at_the_same_time_of_day_are_distinguishable(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    hass_ws_client,
) -> None:
    """The same chore morning and night must not collapse into two identical rows."""
    mock_client.get_chores.return_value = [
        _timed("20", "Brush Teeth", "06:00"),
        _timed("21", "Brush Teeth", "20:00"),
    ]
    await setup_integration(hass, mock_config_entry)

    client = await hass_ws_client()
    await client.send_json_auto_id({"type": "todo/item/list", "entity_id": ALEX_CHORES})
    items = (await client.receive_json())["result"]["items"]

    assert [i["summary"] for i in items] == ["Brush Teeth", "Brush Teeth"]
    # Same name, same day: the time is the only thing telling them apart.
    assert "T06:00:00" in items[0]["due"]
    assert "T20:00:00" in items[1]["due"]


async def test_completing_a_timed_chore_passes_the_time(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A date alone does not name the occurrence of a chore with a time of day.

    Sending only instance_date answers `422 instance_time can't be blank`, which
    made every timed chore impossible to check off.
    """
    mock_client.get_chores.return_value = [_timed("20", "Brush Teeth", "06:00")]
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {ATTR_ENTITY_ID: ALEX_CHORES, "item": "Brush Teeth", "status": "completed"},
        blocking=True,
    )

    mock_client.complete_chore.assert_awaited_once_with(
        FRAME_ID, "20", instance_date=date(2026, 8, 7), instance_time="06:00"
    )


async def test_rescheduling_a_timed_chore_writes_both_halves(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Moving a timed chore changes the date and the time, not just the date."""
    mock_client.get_chores.return_value = [_timed("20", "Brush Teeth", "06:00")]
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {
            ATTR_ENTITY_ID: ALEX_CHORES,
            "item": "Brush Teeth",
            "due_datetime": "2026-08-08 07:30:00",
        },
        blocking=True,
    )

    mock_client.update_chore.assert_awaited_once_with(
        FRAME_ID, "20", start="2026-08-08", start_time="07:30"
    )


async def test_an_unchanged_due_time_writes_nothing(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Checking a timed chore off must not look like a reschedule.

    The frontend sends the whole item back on every edit, so the due time
    arrives unchanged with each tap. Comparing it against the date alone would
    make every one of those a spurious update.
    """
    mock_client.get_chores.return_value = [_timed("20", "Brush Teeth", "06:00")]
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {
            ATTR_ENTITY_ID: ALEX_CHORES,
            "item": "Brush Teeth",
            "status": "completed",
            "due_datetime": "2026-08-07 06:00:00",
        },
        blocking=True,
    )

    mock_client.update_chore.assert_not_awaited()


def _worth(chore_id: str, summary: str, points: int | None, description: str | None) -> Chore:
    """A chore that may earn reward points, and may already carry notes."""
    return Chore.from_resource(
        {
            "type": "chore",
            "id": f"{chore_id}-2026-08-07",
            "attributes": {
                "id": f"{chore_id}-2026-08-07",
                "group": chore_id,
                "summary": summary,
                "start": "2026-08-07",
                "reward_points": points,
                "description": description,
            },
            "relationships": {"category": {"data": {"type": "category", "id": CATEGORY_ID}}},
        }
    )


async def test_points_are_shown_on_the_item(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    hass_ws_client,
) -> None:
    """A chore worth points says so; one worth none is left alone."""
    mock_client.get_chores.return_value = [
        _worth("1", "Vacuum", 2, None),
        _worth("2", "Make bed", 1, None),
        _worth("3", "Brush Teeth", None, None),
    ]
    await setup_integration(hass, mock_config_entry)

    client = await hass_ws_client()
    await client.send_json_auto_id({"type": "todo/item/list", "entity_id": ALEX_CHORES})
    items = (await client.receive_json())["result"]["items"]

    assert [(i["summary"], i.get("description")) for i in items] == [
        ("Vacuum", "⭐ 2"),
        ("Make bed", "⭐ 1"),
        ("Brush Teeth", None),
    ]


async def test_points_do_not_displace_existing_notes(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    hass_ws_client,
) -> None:
    """The chore's own notes survive, with the points above them."""
    mock_client.get_chores.return_value = [
        _worth("1", "Clean up after dinner", 1, "Clear the table & load the dishwasher")
    ]
    await setup_integration(hass, mock_config_entry)

    client = await hass_ws_client()
    await client.send_json_auto_id({"type": "todo/item/list", "entity_id": ALEX_CHORES})
    items = (await client.receive_json())["result"]["items"]

    assert items[0]["description"] == "⭐ 1\n\nClear the table & load the dishwasher"


async def test_checking_off_a_chore_worth_points_writes_no_description(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The guard that matters.

    The frontend returns the whole item on every tap, points line included. If
    that counted as an edit, the star would be written into the chore's notes on
    Skylight, re-prefixed on the next poll, and written again.
    """
    mock_client.get_chores.return_value = [_worth("1", "Vacuum", 2, None)]
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {
            ATTR_ENTITY_ID: ALEX_CHORES,
            "item": "Vacuum",
            "status": "completed",
            "description": "⭐ 2",
        },
        blocking=True,
    )

    mock_client.complete_chore.assert_awaited_once()
    mock_client.update_chore.assert_not_awaited()


async def test_editing_notes_on_a_chore_worth_points_strips_the_star(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Only the half the user wrote reaches Skylight."""
    mock_client.get_chores.return_value = [_worth("1", "Vacuum", 2, "Under the sofa too")]
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {
            ATTR_ENTITY_ID: ALEX_CHORES,
            "item": "Vacuum",
            "description": "⭐ 2\n\nUnder the sofa and behind it",
        },
        blocking=True,
    )

    mock_client.update_chore.assert_awaited_once_with(
        FRAME_ID, "1", description="Under the sofa and behind it"
    )


async def test_deleting_everything_but_the_star_writes_nothing(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Emptying a description is not supported, and must not write the star."""
    mock_client.get_chores.return_value = [_worth("1", "Vacuum", 2, "Under the sofa too")]
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {ATTR_ENTITY_ID: ALEX_CHORES, "item": "Vacuum", "description": "⭐ 2"},
        blocking=True,
    )

    mock_client.update_chore.assert_not_awaited()


async def test_editing_a_description_writes_it(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Notes on a chore are editable, and unchanged notes write nothing."""
    mock_client.get_chores.return_value = [_timed("20", "Brush Teeth", "06:00")]
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {
            ATTR_ENTITY_ID: ALEX_CHORES,
            "item": "Brush Teeth",
            "description": "Two minutes, top and bottom",
        },
        blocking=True,
    )

    mock_client.update_chore.assert_awaited_once_with(
        FRAME_ID, "20", description="Two minutes, top and bottom"
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
