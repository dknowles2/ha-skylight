"""Tests for "Up for Grabs" chores — the ones that belong to nobody.

The API refuses to complete one without naming the profile that claimed it, so
these tests are mostly about where that name comes from: the Home Assistant user
who pressed the button, via their person entity, via the options mapping.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from unittest.mock import AsyncMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.todo import DOMAIN as TODO_DOMAIN
from homeassistant.components.todo import TodoItem
from homeassistant.components.todo.const import TodoServices
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pyskylight.models import ChoreGroups
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skylight.const import CONF_PROFILE_MAP

from .conftest import (
    CATEGORY_ID,
    FRAME_ID,
    OTHER_CATEGORY_ID,
    async_poll,
    setup_integration,
)

UP_FOR_GRABS = "todo.kitchen_up_for_grabs"
PERSON = "person.alex"


@pytest.fixture
async def user_id(hass: HomeAssistant) -> str:
    """A real Home Assistant user; service calls reject unknown ones."""
    user = await hass.auth.async_create_user("Alex")
    return user.id


@pytest.fixture
def person(hass: HomeAssistant, user_id: str) -> str:
    """A person entity linked to that user account.

    A second, unlinked person exists too, so the lookup has to actually match
    rather than take whoever comes first.
    """
    hass.states.async_set("person.sam", "home", {"user_id": None})
    hass.states.async_set(PERSON, "home", {"user_id": user_id})
    return PERSON


async def setup_with_map(
    hass: HomeAssistant, entry: MockConfigEntry, profile_map: dict[str, str]
) -> None:
    """Set the entry up with a people mapping already in place."""
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_PROFILE_MAP: profile_map})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _items(hass: HomeAssistant, entity_id: str = UP_FOR_GRABS) -> list[dict]:
    result = await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.GET_ITEMS,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
        return_response=True,
    )
    return result[entity_id]["items"]


async def test_unclaimed_chores_are_listed(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The chores no profile owns show up on their own list."""
    await setup_integration(hass, mock_config_entry)

    summaries = [item["summary"] for item in await _items(hass)]
    assert summaries == ["Put away laundry", "Vacuum", "Unload dishwasher", "Water plants"]


async def test_upcoming_chores_are_left_out(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Scope matches the per-profile lists: overdue, today, undated — not future."""
    await setup_integration(hass, mock_config_entry)

    assert "Change sheets" not in [item["summary"] for item in await _items(hass)]


async def test_state_counts_only_open_chores(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """One of the four fixtures is already done."""
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(UP_FOR_GRABS).state == "3"


async def test_claiming_credits_the_mapped_profile(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    person: str,
    user_id: str,
) -> None:
    """Whoever ticks the box gets the credit, resolved user -> person -> profile."""
    await setup_with_map(hass, mock_config_entry, {CATEGORY_ID: person})

    await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.UPDATE_ITEM,
        {ATTR_ENTITY_ID: UP_FOR_GRABS, "item": "Vacuum", "status": "completed"},
        blocking=True,
        context=Context(user_id=user_id),
    )

    mock_client.complete_chore.assert_awaited_once_with(
        FRAME_ID, "11", instance_date=None, category_id=CATEGORY_ID
    )


async def test_claiming_without_a_mapping_is_refused(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    person: str,
    user_id: str,
) -> None:
    """Nothing is guessed: the wrong child's chore chart is worse than an error."""
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError, match="no Skylight profile is matched"):
        await hass.services.async_call(
            TODO_DOMAIN,
            TodoServices.UPDATE_ITEM,
            {ATTR_ENTITY_ID: UP_FOR_GRABS, "item": "Vacuum", "status": "completed"},
            blocking=True,
            context=Context(user_id=user_id),
        )

    mock_client.complete_chore.assert_not_awaited()


async def test_claiming_from_an_automation_is_refused(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    person: str,
    user_id: str,
) -> None:
    """A call with no user behind it — an automation, or a voice assistant."""
    await setup_with_map(hass, mock_config_entry, {CATEGORY_ID: person})

    with pytest.raises(HomeAssistantError, match="no Skylight profile is matched"):
        await hass.services.async_call(
            TODO_DOMAIN,
            TodoServices.UPDATE_ITEM,
            {ATTR_ENTITY_ID: UP_FOR_GRABS, "item": "Vacuum", "status": "completed"},
            blocking=True,
        )


async def test_a_user_without_a_person_is_refused(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    user_id: str,
) -> None:
    """A Home Assistant account nobody has made a person for."""
    await setup_with_map(hass, mock_config_entry, {CATEGORY_ID: PERSON})

    with pytest.raises(HomeAssistantError, match="no Skylight profile is matched"):
        await hass.services.async_call(
            TODO_DOMAIN,
            TodoServices.UPDATE_ITEM,
            {ATTR_ENTITY_ID: UP_FOR_GRABS, "item": "Vacuum", "status": "completed"},
            blocking=True,
            context=Context(user_id=user_id),
        )


async def test_a_person_mapped_to_another_profile_is_refused(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    person: str,
    user_id: str,
) -> None:
    """Someone else's mapping must not stand in for a missing one."""
    await setup_with_map(hass, mock_config_entry, {OTHER_CATEGORY_ID: "person.sam"})

    with pytest.raises(HomeAssistantError, match="no Skylight profile is matched"):
        await hass.services.async_call(
            TODO_DOMAIN,
            TodoServices.UPDATE_ITEM,
            {ATTR_ENTITY_ID: UP_FOR_GRABS, "item": "Vacuum", "status": "completed"},
            blocking=True,
            context=Context(user_id=user_id),
        )


async def test_releasing_a_chore_needs_no_mapping(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Only completion carries attribution; reopening does not."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.UPDATE_ITEM,
        {ATTR_ENTITY_ID: UP_FOR_GRABS, "item": "Unload dishwasher", "status": "needs_action"},
        blocking=True,
    )

    mock_client.uncomplete_chore.assert_awaited_once_with(FRAME_ID, "12", instance_date=None)


async def test_renaming_needs_no_mapping(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Editing a chore is not claiming it."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.UPDATE_ITEM,
        {ATTR_ENTITY_ID: UP_FOR_GRABS, "item": "Vacuum", "rename": "Vacuum upstairs"},
        blocking=True,
    )

    mock_client.update_chore.assert_awaited_once_with(FRAME_ID, "11", summary="Vacuum upstairs")


async def test_deleting_an_unclaimed_chore(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Removing a chore nobody took."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.REMOVE_ITEM,
        {ATTR_ENTITY_ID: UP_FOR_GRABS, "item": "Vacuum"},
        blocking=True,
    )

    mock_client.delete_chore.assert_awaited_once_with(FRAME_ID, "11", apply_to=None)


async def test_no_creating_from_home_assistant(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The API cannot make a chore that starts out unowned, so neither can we."""
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(UP_FOR_GRABS)
    assert not state.attributes["supported_features"] & 1  # CREATE_TODO_ITEM


async def test_options_flow_maps_people_to_profiles(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    person: str,
) -> None:
    """The Configure button offers one person picker per profile."""
    await setup_integration(hass, mock_config_entry)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "init"
    # One field per family profile, and nothing for the calendar buckets.
    assert set(result["data_schema"].schema) == {CATEGORY_ID, OTHER_CATEGORY_ID}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CATEGORY_ID: person}
    )
    assert result["type"] == "create_entry"
    assert mock_config_entry.options == {CONF_PROFILE_MAP: {CATEGORY_ID: person}}


async def test_options_flow_forgets_a_cleared_profile(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    person: str,
) -> None:
    """Clearing a picker removes the mapping rather than leaving it behind."""
    await setup_with_map(hass, mock_config_entry, {CATEGORY_ID: person})

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={})

    assert result["type"] == "create_entry"
    assert mock_config_entry.options == {CONF_PROFILE_MAP: {}}


async def test_options_flow_without_profiles(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Nothing to map on an account with no family profiles."""
    mock_client.get_categories.return_value = []
    await setup_integration(hass, mock_config_entry)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    assert result["type"] == "abort"
    assert result["reason"] == "no_profiles"


async def test_unavailable_list_reports_no_items(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A frame that dropped out of the snapshot has nothing to show."""
    await setup_integration(hass, mock_config_entry)

    entity = hass.data["entity_components"][TODO_DOMAIN].get_entity(UP_FOR_GRABS)
    mock_client.get_frames.return_value = []
    await async_poll(hass, freezer)

    assert hass.states.get(UP_FOR_GRABS).state == STATE_UNAVAILABLE
    assert entity.todo_items is None


async def test_acting_on_an_unknown_chore_is_rejected(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A stale uid must not silently do nothing."""
    await setup_integration(hass, mock_config_entry)
    entity = hass.data["entity_components"][TODO_DOMAIN].get_entity(UP_FOR_GRABS)

    with pytest.raises(HomeAssistantError, match="unknown chore"):
        await entity.async_update_todo_item(TodoItem(uid="nope", summary="Nope"))


@pytest.mark.parametrize("method", ["async_update_todo_item", "async_delete_todo_items"])
async def test_a_chore_with_no_addressable_id(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    unassigned_chores: ChoreGroups,
    method: str,
) -> None:
    """`group` is what update and delete address; without it there is nothing to call.

    Deleting skips the chore, while updating says so — a delete of several
    should not abort partway through on one bad item.
    """
    chore = unassigned_chores.chores["today"][0]
    mock_client.get_all_chores.return_value = replace(
        unassigned_chores,
        chores={**unassigned_chores.chores, "today": [replace(chore, chore_id=None)]},
    )
    await setup_integration(hass, mock_config_entry)
    entity = hass.data["entity_components"][TODO_DOMAIN].get_entity(UP_FOR_GRABS)

    if method == "async_delete_todo_items":
        await entity.async_delete_todo_items([chore.id])
        mock_client.delete_chore.assert_not_awaited()
    else:
        with pytest.raises(HomeAssistantError, match="has no id"):
            await entity.async_update_todo_item(TodoItem(uid=chore.id, summary="x"))


async def test_rescheduling_an_unclaimed_chore(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Due dates are sent in the API's own format."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.UPDATE_ITEM,
        {ATTR_ENTITY_ID: UP_FOR_GRABS, "item": "Vacuum", "due_date": date(2026, 8, 20)},
        blocking=True,
    )

    mock_client.update_chore.assert_awaited_once_with(FRAME_ID, "11", start="2026-08-20")
