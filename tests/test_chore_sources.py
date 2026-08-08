"""Tests for where a profile's chores come from.

Neither Skylight endpoint is complete on its own, which is the whole reason
there are two:

* `GET /chores` returns chores only for a profile with
  `selected_for_chore_chart` set, but it does include what has been ticked off.
* `/chores/all` covers every profile regardless of the chart, and drops a chore
  the moment it is completed.

Both were established against a test frame — a new profile's chores were
invisible to `GET /chores` until the flag was set, and a completed chore
vanished from `/chores/all`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pyskylight.models import Chore, ChoreGroups
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import setup_integration

SAM_CHORES = "todo.kitchen_sam_chores"
ALEX_CHORES = "todo.kitchen_alex_chores"


def _summaries(hass: HomeAssistant, entity_id: str) -> list[str]:
    entity = hass.data["entity_components"]["todo"].get_entity(entity_id)
    return [item.summary for item in entity.todo_items or []]


async def test_a_profile_off_the_chore_chart_still_has_chores(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
    unassigned_chores: ChoreGroups,
) -> None:
    """The bug this fixes: a family member taken off the chart went blank.

    Their chores still exist and the frame still shows them; only `GET /chores`
    stops admitting it. On a real account two of three people were in that
    state, so two of the three chore lists could never fill.
    """
    off_the_chart = chores[3]
    # `GET /chores` knows nothing about Sam.
    mock_client.get_chores.return_value = chores[:3]
    mock_client.get_all_chores.return_value = ChoreGroups(
        chores={
            **unassigned_chores.chores,
            "today": [*unassigned_chores.chores["today"], off_the_chart],
        },
        routines={},
    )
    await setup_integration(hass, mock_config_entry)

    assert _summaries(hass, SAM_CHORES) == ["Laundry"]


async def test_a_chore_in_both_sources_appears_once(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
    unassigned_chores: ChoreGroups,
) -> None:
    """A charted profile's chores are in both responses."""
    mock_client.get_all_chores.return_value = ChoreGroups(
        chores={**unassigned_chores.chores, "late": [*unassigned_chores.chores["late"], chores[0]]},
        routines={},
    )
    await setup_integration(hass, mock_config_entry)

    assert _summaries(hass, ALEX_CHORES).count("Dishes") == 1


async def test_completed_chores_survive_the_merge(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """`/chores/all` drops them, so only the charted source carries them."""
    await setup_integration(hass, mock_config_entry)

    assert "Homework" in _summaries(hass, ALEX_CHORES)
    assert hass.states.get("sensor.kitchen_alex_chores_completed").state == "1"


async def test_unclaimed_chores_are_not_credited_to_a_profile(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Up for Grabs chores have no category and must not join anyone's list."""
    await setup_integration(hass, mock_config_entry)

    for entity_id in (ALEX_CHORES, SAM_CHORES):
        assert "Vacuum" not in _summaries(hass, entity_id)


async def test_future_chores_stay_out(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
    unassigned_chores: ChoreGroups,
) -> None:
    """The merge takes the same buckets as before: now, not next week."""
    later = chores[3]
    mock_client.get_chores.return_value = chores[:3]
    mock_client.get_all_chores.return_value = ChoreGroups(
        chores={**unassigned_chores.chores, "future": [later]}, routines={}
    )
    await setup_integration(hass, mock_config_entry)

    assert _summaries(hass, SAM_CHORES) == []


async def test_one_call_serves_both_lists(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Up for Grabs and the merge share a response rather than each fetching."""
    await setup_integration(hass, mock_config_entry)

    assert mock_client.get_all_chores.await_count == 1
