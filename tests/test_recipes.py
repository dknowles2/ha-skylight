"""Tests for pushing a recipe's ingredients onto the grocery list.

Two API facts shape all of this. Skylight parses the ingredients out of the
recipe's free text on its own servers, so they arrive seconds after the call
returns; and they always land on the list flagged `default_grocery_list`,
whatever list the caller had in mind.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pyskylight.exceptions import ApiError
from pyskylight.models import Recipe
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.skylight.const import (
    ATTR_RECIPE,
    DOMAIN,
    RECIPE_INGREDIENT_DELAY,
    SERVICE_ADD_RECIPE,
)

from .conftest import FRAME_ID, setup_integration

GROCERY = "todo.kitchen_grocery_list"
TO_DO = "todo.kitchen_to_do"
CHORES = "todo.kitchen_alex_chores"


async def add_recipe(hass: HomeAssistant, entity_id: str, recipe: str) -> None:
    """Call the action against one to-do entity."""
    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_RECIPE,
        {ATTR_ENTITY_ID: entity_id, ATTR_RECIPE: recipe},
        blocking=True,
    )


async def test_adding_a_recipe(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The recipe is named, not identified: the id is looked up here."""
    await setup_integration(hass, mock_config_entry)

    await add_recipe(hass, GROCERY, "Taco Night")

    mock_client.add_recipe_to_grocery_list.assert_awaited_once_with(FRAME_ID, "500")


async def test_the_name_is_matched_loosely(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Nobody typing into an automation should have to match capitals."""
    await setup_integration(hass, mock_config_entry)

    await add_recipe(hass, GROCERY, "  taco NIGHT ")

    mock_client.add_recipe_to_grocery_list.assert_awaited_once_with(FRAME_ID, "500")


async def test_a_second_refresh_collects_the_ingredients(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The items do not exist yet when the call returns.

    Skylight parses them server-side and they appear about ten seconds later,
    so the write's own refresh finds an unchanged list. Without the scheduled
    one, the groceries would not show up until the next poll.
    """
    await setup_integration(hass, mock_config_entry)
    mock_client.get_lists.reset_mock()

    await add_recipe(hass, GROCERY, "Taco Night")
    polls_after_the_write = mock_client.get_lists.await_count

    freezer.tick(RECIPE_INGREDIENT_DELAY)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert mock_client.get_lists.await_count > polls_after_the_write


async def test_the_wrong_list_is_refused(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Skylight ignores the choice, so pretending to honour it would mislead.

    Ingredients always land on the default grocery list. Filling a list other
    than the one the user targeted is worse than refusing.
    """
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError, match="Grocery List"):
        await add_recipe(hass, TO_DO, "Taco Night")

    mock_client.add_recipe_to_grocery_list.assert_not_awaited()


async def test_a_chore_list_is_refused_with_an_explanation(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The action is registered platform-wide, so chore lists can be targeted."""
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError, match="chore list"):
        await add_recipe(hass, CHORES, "Taco Night")

    mock_client.add_recipe_to_grocery_list.assert_not_awaited()


async def test_an_unknown_recipe(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A typo must say so rather than quietly doing nothing."""
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError, match="No Skylight recipe is named"):
        await add_recipe(hass, GROCERY, "Beef Wellington")

    mock_client.add_recipe_to_grocery_list.assert_not_awaited()


async def test_two_recipes_with_one_name(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    recipes: list[Recipe],
) -> None:
    """Nothing stops a household naming two recipes the same; guessing is worse."""
    mock_client.get_meal_recipes.return_value = [*recipes, recipes[0]]
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError, match="2 Skylight recipes are named"):
        await add_recipe(hass, GROCERY, "Taco Night")

    mock_client.add_recipe_to_grocery_list.assert_not_awaited()


async def test_a_refused_add_surfaces(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A failed write must not look like one that worked."""
    await setup_integration(hass, mock_config_entry)
    mock_client.add_recipe_to_grocery_list.side_effect = ApiError(500, "boom")

    with pytest.raises(HomeAssistantError, match="Could not add the recipe"):
        await add_recipe(hass, GROCERY, "Taco Night")


async def test_a_frame_with_no_default_grocery_list(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    lists: list,
) -> None:
    """Without a default list there is nowhere for Skylight to put anything."""
    mock_client.get_lists.return_value = [replace(lists[0], default_grocery_list=False), lists[1]]
    mock_client.get_list.side_effect = None
    mock_client.get_list.return_value = replace(lists[0], default_grocery_list=False)
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError, match="none"):
        await add_recipe(hass, GROCERY, "Taco Night")

    mock_client.add_recipe_to_grocery_list.assert_not_awaited()


async def test_a_frame_with_no_recipes(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """An empty meal planner is a frame's state, not a failure to set up."""
    mock_client.get_meal_recipes.return_value = []
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(GROCERY) is not None
    with pytest.raises(HomeAssistantError, match="No Skylight recipe is named"):
        await add_recipe(hass, GROCERY, "Taco Night")
