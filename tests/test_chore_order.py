"""Tests for reordering a profile's chore chart.

Skylight expresses a move as a neighbour rather than an index — every scalar
form of `position` is rejected with `422 Position is required` — and the move
changes each chore's `position` while leaving the response order alone. Both
were established against a test frame.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.todo import DOMAIN as TODO_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pyskylight.exceptions import ApiError
from pyskylight.models import Chore
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import FRAME_ID, setup_integration

ALEX_CHORES = "todo.kitchen_alex_chores"


def _entity(hass: HomeAssistant):
    """The chore list entity itself.

    Moving is a websocket command rather than a service, so the tests that care
    about an error reaching the caller go through the entity directly; the ones
    about the mapping go over the websocket, like the panel does.
    """
    return hass.data["entity_components"][TODO_DOMAIN].get_entity(ALEX_CHORES)


async def move(hass: HomeAssistant, uid: str, previous_uid: str | None = None) -> None:
    """Move a chore the way the to-do panel does."""
    await _entity(hass).async_move_todo_item(uid, previous_uid)


def _uids(hass: HomeAssistant) -> list[str]:
    return [item.summary for item in _entity(hass).todo_items or []]


async def test_the_list_follows_position_not_response_order(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
) -> None:
    """Moving a chore changes `position` and nothing else.

    The response keeps arriving in its own order, so a list rendered as it
    arrives would show the old order for ever.
    """
    mock_client.get_chores.return_value = [
        replace(chores[0], position=3),
        replace(chores[1], position=1),
        replace(chores[2], position=2),
    ]
    await setup_integration(hass, mock_config_entry)

    assert _uids(hass) == ["Recycling", "Homework", "Dishes"]


async def test_a_chore_without_a_position_sorts_last(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
) -> None:
    """Position is optional in the model, so an absent one must not crash."""
    mock_client.get_chores.return_value = [
        replace(chores[0], position=None),
        replace(chores[1], position=2),
    ]
    await setup_integration(hass, mock_config_entry)

    assert _uids(hass) == ["Recycling", "Dishes"]


async def test_moving_after_another_chore(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
    hass_ws_client,
) -> None:
    """ "Put this after that one" is exactly Skylight's `after`.

    Driven over the websocket, since that is how the to-do panel moves an item.
    """
    await setup_integration(hass, mock_config_entry)
    client = await hass_ws_client()

    await client.send_json_auto_id(
        {
            "type": "todo/item/move",
            "entity_id": ALEX_CHORES,
            "uid": chores[0].id,
            "previous_uid": chores[1].id,
        }
    )
    assert (await client.receive_json())["success"]

    mock_client.move_chore.assert_awaited_once_with(FRAME_ID, "1", after="2")


async def test_moving_to_the_top(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
) -> None:
    """No previous item means the top, which Skylight says as `before`."""
    await setup_integration(hass, mock_config_entry)

    await move(hass, chores[2].id)

    mock_client.move_chore.assert_awaited_once_with(FRAME_ID, "3", before="1")


async def test_moving_the_only_chore_does_nothing(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
) -> None:
    """With nothing to sit in front of, there is no neighbour to name."""
    mock_client.get_chores.return_value = [chores[0]]
    await setup_integration(hass, mock_config_entry)

    await move(hass, chores[0].id)

    mock_client.move_chore.assert_not_awaited()


async def test_moving_an_unknown_chore(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A stale uid from a dashboard must say so."""
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError, match="unknown chore"):
        await move(hass, "nope-2026-08-07")


async def test_a_chore_with_no_addressable_id(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
) -> None:
    """`group` is what the move endpoint addresses; without it there is nothing."""
    mock_client.get_chores.return_value = [
        replace(chores[0], chore_id=None),
        chores[1],
    ]
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError, match="no addressable id"):
        await move(hass, chores[0].id, chores[1].id)


async def test_a_neighbour_with_no_addressable_id(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
) -> None:
    """The neighbour is sent to the API too, so it needs an id just as much."""
    mock_client.get_chores.return_value = [
        chores[0],
        replace(chores[1], chore_id=None),
    ]
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError, match="no addressable id"):
        await move(hass, chores[0].id, chores[1].id)

    mock_client.move_chore.assert_not_awaited()


async def test_a_refused_move_surfaces(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
) -> None:
    """A failed write must not look like one that worked."""
    await setup_integration(hass, mock_config_entry)
    mock_client.move_chore.side_effect = ApiError(422, "Position is required")

    with pytest.raises(HomeAssistantError, match="Could not reorder the Skylight chore"):
        await move(hass, chores[0].id, chores[1].id)
