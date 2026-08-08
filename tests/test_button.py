"""Tests for reward redemption.

A reward belongs to exactly one profile, so redeeming needs no say in who is
claiming it — the awkward part of up-for-grabs chores does not arise here.
Skylight also owns the rules: it deducts the points, refuses a second
redemption, and refuses one the balance cannot cover.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pyskylight.exceptions import ApiError
from pyskylight.models import Reward
from pytest_homeassistant_custom_component.common import MockConfigEntry, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from custom_components.skylight.const import REWARD_LOOKBACK

from .conftest import FRAME_ID, async_poll, setup_integration

SCREEN_TIME = "button.kitchen_alex_extra_screen_time"
PIZZA = "button.kitchen_alex_pizza_night"


async def test_all_entities(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Every button the platform creates, pinned to a snapshot."""
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr("custom_components.skylight.PLATFORMS", [Platform.BUTTON])
        await setup_integration(hass, mock_config_entry)

    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


async def test_a_button_per_reward(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Named for the profile that owns the reward, since rewards are personal."""
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(SCREEN_TIME).name == "Kitchen Alex Extra screen time"
    assert hass.states.get("button.kitchen_sam_new_book") is not None


async def test_cost_and_last_redemption_are_exposed(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A button's own state only records presses made here, not on the frame."""
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(SCREEN_TIME).attributes["point_value"] == 5
    assert hass.states.get(SCREEN_TIME).attributes["redeemed_at"] is None
    assert hass.states.get(PIZZA).attributes["redeemed_at"] is not None


async def test_pressing_redeems(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """No category is sent: the reward already knows whose it is."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: SCREEN_TIME}, blocking=True
    )

    mock_client.redeem_reward.assert_awaited_once_with(FRAME_ID, "900")


async def test_pressing_refreshes_the_balance(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Skylight deducts the points, so a refresh is how the balance catches up."""
    await setup_integration(hass, mock_config_entry)
    mock_client.get_reward_points.reset_mock()

    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: SCREEN_TIME}, blocking=True
    )

    assert mock_client.get_reward_points.await_count == 1


@pytest.mark.parametrize(
    "message",
    ["Not enough points to redeem reward.", "Reward has already been redeemed."],
)
async def test_a_refused_redemption_surfaces(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    message: str,
) -> None:
    """Skylight is the authority on both rules, and says so clearly."""
    await setup_integration(hass, mock_config_entry)
    mock_client.redeem_reward.side_effect = ApiError(400, message)

    with pytest.raises(HomeAssistantError, match="Could not redeem the Skylight reward"):
        await hass.services.async_call(
            BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: SCREEN_TIME}, blocking=True
        )


async def test_redeemed_rewards_are_still_fetched(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A lookback keeps a redeemed reward's button from vanishing on the press.

    Without it the entity would leave the registry when redeemed and return when
    it respawned, breaking any dashboard card pointing at it.
    """
    await setup_integration(hass, mock_config_entry)

    _, kwargs = mock_client.get_rewards.await_args
    assert kwargs["redeemed_at_min"] is not None
    assert hass.states.get(PIZZA) is not None
    assert REWARD_LOOKBACK.days == 7


async def test_a_deleted_reward_goes_unavailable(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A reward removed on the frame must not look pressable."""
    await setup_integration(hass, mock_config_entry)

    entity = hass.data["entity_components"][BUTTON_DOMAIN].get_entity(SCREEN_TIME)
    mock_client.get_rewards.return_value = [rewards[1], rewards[2]]
    await async_poll(hass, freezer)

    assert hass.states.get(SCREEN_TIME).state == STATE_UNAVAILABLE
    # Nothing to describe once the reward is gone.
    assert entity.extra_state_attributes is None


async def test_a_frame_that_dropped_out(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The whole frame failing takes its buttons with it."""
    await setup_integration(hass, mock_config_entry)

    entity = hass.data["entity_components"][BUTTON_DOMAIN].get_entity(SCREEN_TIME)
    mock_client.get_frames.return_value = []
    await async_poll(hass, freezer)

    assert hass.states.get(SCREEN_TIME).state == STATE_UNAVAILABLE
    # Looking the reward up must cope with the frame having gone entirely.
    assert entity.extra_state_attributes is None


async def test_a_reward_with_no_profile(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
) -> None:
    """A reward pointing at a category we filtered out still gets a usable name."""
    mock_client.get_rewards.return_value = [replace(rewards[0], category_id=None)]
    await setup_integration(hass, mock_config_entry)

    entity_id = "button.kitchen_extra_screen_time"
    assert hass.states.get(entity_id) is not None


async def test_no_rewards(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A profile with points but nothing to spend them on gets no buttons."""
    mock_client.get_rewards.return_value = []
    await setup_integration(hass, mock_config_entry)

    assert not [
        entry
        for entry in er.async_entries_for_config_entry(entity_registry, mock_config_entry.entry_id)
        if entry.domain == BUTTON_DOMAIN
    ]
