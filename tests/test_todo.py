"""Tests for the Skylight to-do platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.todo import DOMAIN as TODO_DOMAIN
from homeassistant.components.todo import TodoItem
from homeassistant.components.todo.const import TodoItemStatus
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pyskylight.exceptions import ApiError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.skylight.const import SCAN_INTERVAL
from custom_components.skylight.todo import SkylightTodoListEntity

from .conftest import FRAME_ID, LIST_ID, setup_integration


def _entity(hass: HomeAssistant, entity_id: str) -> SkylightTodoListEntity:
    """Reach the entity object for behaviour with no service-call route."""
    component = hass.data["entity_components"][TODO_DOMAIN]
    entity = component.get_entity(entity_id)
    assert isinstance(entity, SkylightTodoListEntity)
    return entity


GROCERIES = "todo.kitchen_grocery_list"
TODO_LIST = "todo.kitchen_to_do"


async def test_all_entities(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Every to-do list the platform creates, pinned to a snapshot."""
    with patch("custom_components.skylight.PLATFORMS", [Platform.TODO]):
        await setup_integration(hass, mock_config_entry)

    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


async def test_state_counts_open_items(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A to-do entity's state is its number of unfinished items."""
    await setup_integration(hass, mock_config_entry)

    # Milk and Bread are open; Eggs is done.
    assert hass.states.get(GROCERIES).state == "2"
    assert hass.states.get(TODO_LIST).state == "0"


async def test_items_are_exposed(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    hass_ws_client,
) -> None:
    """Item summaries and statuses survive the round trip to Home Assistant."""
    await setup_integration(hass, mock_config_entry)
    client = await hass_ws_client()
    await client.send_json_auto_id({"type": "todo/item/list", "entity_id": GROCERIES})
    result = await client.receive_json()

    assert [(i["summary"], i["status"]) for i in result["result"]["items"]] == [
        ("Milk", "needs_action"),
        ("Eggs", "completed"),
        ("Bread", "needs_action"),
    ]


async def test_add_item(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Adding an item calls the API and refreshes."""
    await setup_integration(hass, mock_config_entry)
    mock_client.get_lists.reset_mock()

    await hass.services.async_call(
        TODO_DOMAIN,
        "add_item",
        {ATTR_ENTITY_ID: GROCERIES, "item": "Coffee"},
        blocking=True,
    )

    mock_client.create_list_item.assert_awaited_once_with(FRAME_ID, LIST_ID, "Coffee")
    # A write is followed by a refresh so the UI does not sit on stale data.
    assert mock_client.get_lists.await_count == 1


async def test_complete_item(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Checking an item off maps to Skylight's completed status."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {ATTR_ENTITY_ID: GROCERIES, "item": "Milk", "status": "completed"},
        blocking=True,
    )

    mock_client.update_list_item.assert_awaited_once_with(
        FRAME_ID, LIST_ID, "101", label="Milk", status="completed"
    )


async def test_rename_item(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Renaming an item sends the new label."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {ATTR_ENTITY_ID: GROCERIES, "item": "Milk", "rename": "Oat milk"},
        blocking=True,
    )

    mock_client.update_list_item.assert_awaited_once_with(
        FRAME_ID, LIST_ID, "101", label="Oat milk", status="pending"
    )


async def test_remove_items(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Removing items deletes them one at a time."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        "remove_item",
        {ATTR_ENTITY_ID: GROCERIES, "item": ["Milk", "Bread"]},
        blocking=True,
    )

    assert [call.args for call in mock_client.delete_list_item.await_args_list] == [
        (FRAME_ID, LIST_ID, "101"),
        (FRAME_ID, LIST_ID, "103"),
    ]


@pytest.mark.parametrize(
    ("previous_uid", "expected_position"),
    [
        # Moving Bread to the top of the list.
        (None, 0),
        # Moving Bread to just after Milk, which is index 0 once Bread is out.
        ("101", 1),
        # Moving Bread to just after Eggs.
        ("102", 2),
    ],
)
async def test_move_item(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    hass_ws_client,
    previous_uid: str | None,
    expected_position: int,
) -> None:
    """Home Assistant's "put after this" becomes Skylight's position index."""
    await setup_integration(hass, mock_config_entry)
    client = await hass_ws_client()

    payload = {"type": "todo/item/move", "entity_id": GROCERIES, "uid": "103"}
    if previous_uid is not None:
        payload["previous_uid"] = previous_uid
    await client.send_json_auto_id(payload)
    result = await client.receive_json()
    assert result["success"]

    mock_client.move_list_item.assert_awaited_once_with(
        FRAME_ID, LIST_ID, "103", position=expected_position
    )


async def test_api_error_surfaces_to_the_user(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A failed write raises rather than silently doing nothing."""
    await setup_integration(hass, mock_config_entry)
    mock_client.create_list_item.side_effect = ApiError(500, "boom")

    with pytest.raises(HomeAssistantError, match="Could not add the item"):
        await hass.services.async_call(
            TODO_DOMAIN,
            "add_item",
            {ATTR_ENTITY_ID: GROCERIES, "item": "Coffee"},
            blocking=True,
        )


async def test_list_removed_from_frame(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    lists: list,
) -> None:
    """A deleted list leaves its entity unavailable, not showing stale items."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(TODO_LIST).state == "0"

    mock_client.get_lists.return_value = lists[:1]
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(TODO_LIST).state == "unavailable"


async def test_items_hidden_while_unavailable(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    lists: list,
) -> None:
    """A list deleted between polls reports no items rather than a stale copy."""
    await setup_integration(hass, mock_config_entry)
    entity = _entity(hass, TODO_LIST)
    assert entity.todo_items == []

    mock_client.get_lists.return_value = lists[:1]
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert entity.todo_items is None
    assert hass.states.get(TODO_LIST).state == "unavailable"


async def test_move_unknown_item_is_rejected(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Moving an item that is not on the list is an error, not a no-op."""
    await setup_integration(hass, mock_config_entry)
    entity = _entity(hass, GROCERIES)

    with pytest.raises(HomeAssistantError, match="unknown item"):
        await entity.async_move_todo_item("does-not-exist")
    mock_client.move_list_item.assert_not_awaited()


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        # Only a status: do not blank the label by sending an empty one.
        (
            TodoItem(uid="101", summary=None, status=TodoItemStatus.COMPLETED),
            {"status": "completed"},
        ),
        # Only a summary: leave the status alone.
        (TodoItem(uid="101", summary="Milk", status=None), {"label": "Milk"}),
    ],
)
async def test_partial_updates_send_only_what_changed(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    item: TodoItem,
    expected: dict[str, str],
) -> None:
    """A field the caller left unset is not sent to the API."""
    await setup_integration(hass, mock_config_entry)
    entity = _entity(hass, GROCERIES)

    await entity.async_update_todo_item(item)

    mock_client.update_list_item.assert_awaited_once_with(FRAME_ID, LIST_ID, "101", **expected)
