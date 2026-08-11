"""Tests for rewards: what they cost, and redeeming them.

The shape here is driven by one API behaviour. `respawn_on_redemption` does not
reset a reward — Skylight mints a new resource and keeps the old one as a record
of the redemption. So the listing is part catalogue, part history, and only the
unredeemed part is something anyone can act on.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.number import ATTR_VALUE
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pyskylight.exceptions import ApiError
from pyskylight.models import Category, Reward
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skylight.const import DOMAIN, SERVICE_REDEEM_REWARD

from .conftest import CATEGORY_ID, FRAME_ID, _reward, async_poll, setup_integration

SCREEN_TIME = "number.kitchen_alex_extra_screen_time"
PIZZA = "number.kitchen_alex_pizza_night"


async def test_only_unredeemed_rewards_get_entities(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The redeemed history must not become a pile of identical entities.

    This is the bug that prompted the redesign: three `$10 Robux` entities on a
    real account, two of them already spent.
    """
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(SCREEN_TIME) is not None
    # `Pizza night` carries a redeemed_at, so it is history, not a reward.
    assert hass.states.get(PIZZA) is None


async def test_repeated_redemptions_of_one_reward_collapse(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
) -> None:
    """A reward redeemed twice leaves two records and one live resource."""
    live = rewards[0]
    mock_client.get_rewards.return_value = [
        live,
        replace(live, id="800", redeemed_at=rewards[1].redeemed_at),
        replace(live, id="801", redeemed_at=rewards[1].redeemed_at),
    ]
    await setup_integration(hass, mock_config_entry)

    matching = [
        entity_id
        for entity_id in hass.states.async_entity_ids(NUMBER_DOMAIN)
        if "extra_screen_time" in entity_id
    ]
    assert matching == [SCREEN_TIME]


async def test_the_value_is_the_cost(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The point price, as state rather than an attribute nobody records."""
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(SCREEN_TIME).state == "5"


async def test_affordability_is_reported(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Alex has 12 points and the reward costs 5."""
    await setup_integration(hass, mock_config_entry)

    attributes = hass.states.get(SCREEN_TIME).attributes
    assert attributes["balance"] == 12
    assert attributes["affordable"] is True


async def test_rewards_say_whose_they_are(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """So a dashboard can find these entities instead of listing them.

    Rewards are created and renamed on the frame; a card naming four entity ids
    goes stale the moment somebody adds a fifth.
    """
    await setup_integration(hass, mock_config_entry)

    attributes = hass.states.get(SCREEN_TIME).attributes
    assert attributes["profile"] == "Alex"
    assert attributes["category_id"] == CATEGORY_ID
    assert attributes["reward"] == "Extra screen time"


async def test_a_renamed_profile_is_reflected_without_a_reload(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    categories: list[Category],
    freezer: FrozenDateTimeFactory,
) -> None:
    """The label is read from the current snapshot, not cached at startup."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(SCREEN_TIME).attributes["profile"] == "Alex"

    mock_client.get_categories.return_value = [
        replace(categories[0], label="Alexandra"),
        *categories[1:],
    ]
    await async_poll(hass, freezer)

    assert hass.states.get(SCREEN_TIME).attributes["profile"] == "Alexandra"


async def test_progress_is_capped_once_a_reward_is_affordable(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Points keep accruing past the price; a bar that overfills says nothing."""
    await setup_integration(hass, mock_config_entry)

    attributes = hass.states.get(SCREEN_TIME).attributes
    # 12 points against a cost of 5.
    assert attributes["progress"] == 100
    assert attributes["points_needed"] == 0


async def test_progress_towards_a_reward_out_of_reach(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
) -> None:
    """The interesting case, and the common one: how much further to go."""
    mock_client.get_rewards.return_value = [replace(rewards[0], point_value=16)]
    await setup_integration(hass, mock_config_entry)

    attributes = hass.states.get(SCREEN_TIME).attributes
    # 12 of 16.
    assert attributes["progress"] == 75
    assert attributes["points_needed"] == 4
    assert attributes["affordable"] is False


async def test_a_free_reward_is_earned_not_a_division_by_zero(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
) -> None:
    """Skylight accepts a point_value of 0, so this is reachable."""
    mock_client.get_rewards.return_value = [replace(rewards[0], point_value=0)]
    await setup_integration(hass, mock_config_entry)

    attributes = hass.states.get(SCREEN_TIME).attributes
    assert attributes["progress"] == 100
    assert attributes["points_needed"] == 0


async def test_progress_is_unknown_without_a_balance(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A profile with no recorded balance is not a profile with zero points."""
    mock_client.get_reward_points.return_value = []
    await setup_integration(hass, mock_config_entry)

    attributes = hass.states.get(SCREEN_TIME).attributes
    assert attributes["balance"] is None
    assert attributes["progress"] is None
    assert attributes["points_needed"] is None
    assert attributes["affordable"] is None


async def test_changing_the_cost(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Skylight accepts a new point_value, verified against a test frame."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        NUMBER_DOMAIN,
        "set_value",
        {ATTR_ENTITY_ID: SCREEN_TIME, ATTR_VALUE: 8},
        blocking=True,
    )

    mock_client.update_reward.assert_awaited_once_with(FRAME_ID, "900", point_value=8)


async def test_redeeming(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """No profile is sent: the reward belongs to one, and the API rejects it."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REDEEM_REWARD,
        {ATTR_ENTITY_ID: SCREEN_TIME},
        blocking=True,
    )

    mock_client.redeem_reward.assert_awaited_once_with(FRAME_ID, "900")


async def test_redeeming_refreshes_the_balance(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Skylight deducts the points, so a refresh is how the balance catches up."""
    await setup_integration(hass, mock_config_entry)
    mock_client.get_reward_points.reset_mock()

    await hass.services.async_call(
        DOMAIN, SERVICE_REDEEM_REWARD, {ATTR_ENTITY_ID: SCREEN_TIME}, blocking=True
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
    """Skylight owns both rules; nothing is predicted locally."""
    await setup_integration(hass, mock_config_entry)
    mock_client.redeem_reward.side_effect = ApiError(400, message)

    with pytest.raises(HomeAssistantError, match="Could not redeem the Skylight reward"):
        await hass.services.async_call(
            DOMAIN, SERVICE_REDEEM_REWARD, {ATTR_ENTITY_ID: SCREEN_TIME}, blocking=True
        )


async def test_identity_survives_a_respawn(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Redeeming mints a new resource id; the entity must not follow it.

    Keying on the id would hand out a new entity after every redemption, which
    breaks any dashboard card or automation pointing at the old one.
    """
    await setup_integration(hass, mock_config_entry)
    before = entity_registry.async_get(SCREEN_TIME).unique_id

    # The reward is redeemed and respawns under a fresh id.
    mock_client.get_rewards.return_value = [
        replace(rewards[0], id="950"),
        replace(rewards[0], id="900", redeemed_at=rewards[1].redeemed_at),
        *rewards[1:],
    ]
    await async_poll(hass, freezer)

    assert entity_registry.async_get(SCREEN_TIME).unique_id == before
    assert hass.states.get(SCREEN_TIME).state == "5"


async def test_acting_on_a_withdrawn_reward(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A reward deleted on the frame stops being actionable."""
    await setup_integration(hass, mock_config_entry)

    mock_client.get_rewards.return_value = rewards[1:]
    await async_poll(hass, freezer)

    assert hass.states.get(SCREEN_TIME).state == STATE_UNAVAILABLE
    mock_client.redeem_reward.assert_not_awaited()


async def test_old_reward_buttons_are_cleaned_up(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """The previous design's buttons must not linger as unavailable."""
    mock_config_entry.add_to_hass(hass)
    stale = entity_registry.async_get_or_create(
        Platform.BUTTON, DOMAIN, f"{FRAME_ID}_reward_900", config_entry=mock_config_entry
    )

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get(stale.entity_id) is None


async def test_a_profile_with_no_rewards(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Points with nothing to spend them on is the frame's state, not a gap."""
    mock_client.get_rewards.return_value = []
    await setup_integration(hass, mock_config_entry)

    # `reward_points` shares the prefix, so the domain has to be checked too.
    assert not [
        entry
        for entry in er.async_entries_for_config_entry(entity_registry, mock_config_entry.entry_id)
        if entry.domain == NUMBER_DOMAIN
        and entry.unique_id.startswith(f"{FRAME_ID}_{CATEGORY_ID}_reward_")
    ]


async def test_acting_on_a_reward_that_vanished_mid_call(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Redeeming something the frame no longer offers says so, rather than 500s."""
    await setup_integration(hass, mock_config_entry)
    entity = hass.data["entity_components"][NUMBER_DOMAIN].get_entity(SCREEN_TIME)

    mock_client.get_rewards.return_value = rewards[1:]
    await async_poll(hass, freezer)

    assert entity.extra_state_attributes is None
    with pytest.raises(HomeAssistantError, match="no longer offered"):
        await entity.async_redeem()
    with pytest.raises(HomeAssistantError, match="no longer offered"):
        await entity.async_set_native_value(9)


async def test_a_frame_that_dropped_out(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The whole frame failing takes its rewards with it."""
    await setup_integration(hass, mock_config_entry)
    entity = hass.data["entity_components"][NUMBER_DOMAIN].get_entity(SCREEN_TIME)

    mock_client.get_frames.return_value = []
    await async_poll(hass, freezer)

    assert hass.states.get(SCREEN_TIME).state == STATE_UNAVAILABLE
    # Looking the reward up must cope with the frame having gone entirely.
    assert entity.native_value is None


async def test_a_reward_renamed_on_the_frame_comes_back(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Renaming a reward in the Skylight app must not lose it.

    The unique id is keyed on the profile and the name — deliberately, because
    redeeming a respawning reward mints a new resource and an id-based key would
    hand out a fresh entity after every redemption. The cost of that trade is
    that a rename is a different entity, and it has to actually arrive.
    """
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(SCREEN_TIME) is not None

    mock_client.get_rewards.return_value = [
        replace(rewards[0], name="Extra tablet time"),
        *rewards[1:],
    ]
    await async_poll(hass, freezer)

    renamed = "number.kitchen_alex_extra_tablet_time"
    assert hass.states.get(renamed) is not None, "the renamed reward never appeared"
    assert hass.states.get(renamed).attributes["reward"] == "Extra tablet time"
    # And the one under the old name is unavailable rather than lingering with a
    # stale price someone might act on.
    assert hass.states.get(SCREEN_TIME).state == STATE_UNAVAILABLE


async def test_a_reward_added_on_the_frame_appears(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A new reward is the ordinary case, and the documentation promises it.

    Rewards are created on the frame by a parent, not in Home Assistant, so
    needing to reload the integration to see one is the whole feature failing.
    """
    await setup_integration(hass, mock_config_entry)

    mock_client.get_rewards.return_value = [
        *rewards,
        _reward("903", "Cinema trip", 40, CATEGORY_ID),
    ]
    await async_poll(hass, freezer)

    assert hass.states.get("number.kitchen_alex_cinema_trip") is not None


async def test_polling_does_not_pile_up_duplicate_rewards(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Adding on every refresh has to mean adding only what is new.

    The "have I built this one" key and the entity's unique id are two ways of
    naming the same thing, and if they disagree every poll tries to add the
    whole catalogue again. Home Assistant refuses the duplicates, so nothing
    visibly breaks — it just writes a warning per reward per poll for ever.
    """
    await setup_integration(hass, mock_config_entry)
    registry = er.async_get(hass)
    before = len(er.async_entries_for_config_entry(registry, mock_config_entry.entry_id))

    caplog.clear()
    for _ in range(3):
        await async_poll(hass, freezer)

    after = len(er.async_entries_for_config_entry(registry, mock_config_entry.entry_id))
    assert after == before
    assert "already exists" not in caplog.text
