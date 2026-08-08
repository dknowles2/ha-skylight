"""Tests for the reward redemption event.

Redemptions mostly happen at the frame, so the only way Home Assistant learns
about one is a poll noticing `redeemed_at` appear. This turns that into
something an automation can trigger on.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pyskylight.models import Chore, ChoreGroups, Reward
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import CATEGORY_ID, OTHER_CATEGORY_ID, async_poll, setup_integration

REDEEMED = "event.kitchen_reward_redeemed"
WHEN = "2026-08-08T12:00:00+00:00"
LATER = "2026-08-08T18:30:00+00:00"


def _redeem(reward: Reward, when: str = WHEN) -> Reward:
    """Return the reward as the API would report it once redeemed."""
    return replace(reward, redeemed_at=dt_util.parse_datetime(when))


async def test_nothing_fires_before_a_redemption(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The entity exists but has seen nothing."""
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(REDEEMED).state == STATE_UNKNOWN


async def test_history_does_not_fire_on_startup(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Rewards are fetched with a week's lookback, and one is already redeemed.

    Firing for that on every restart would spray notifications for redemptions
    the user saw days ago.
    """
    await setup_integration(hass, mock_config_entry)

    # The `Pizza night` fixture carries a redeemed_at from before setup.
    assert hass.states.get(REDEEMED).state == STATE_UNKNOWN


async def test_a_redemption_at_the_frame_fires(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
    freezer: FrozenDateTimeFactory,
) -> None:
    """The case this exists for: somebody cashed points in on the frame."""
    await setup_integration(hass, mock_config_entry)

    mock_client.get_rewards.return_value = [_redeem(rewards[0]), rewards[1], rewards[2]]
    await async_poll(hass, freezer)

    state = hass.states.get(REDEEMED)
    # An event entity's state is when it fired; the redemption time is an
    # attribute, and the two differ when a poll notices it late.
    assert state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
    assert state.attributes["redeemed_at"] == WHEN
    assert state.attributes["event_type"] == "redeemed"
    assert state.attributes["reward"] == "Extra screen time"
    assert state.attributes["reward_id"] == "900"
    assert state.attributes["point_value"] == 5
    assert state.attributes["profile"] == "Alex"
    assert state.attributes["category_id"] == CATEGORY_ID


async def test_an_automation_can_trigger_on_it(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
    freezer: FrozenDateTimeFactory,
) -> None:
    """What the notification use case actually hangs off: a state change."""
    await setup_integration(hass, mock_config_entry)
    events: list[Event] = []
    hass.bus.async_listen(
        "state_changed",
        lambda event: events.append(event) if event.data["entity_id"] == REDEEMED else None,
    )

    mock_client.get_rewards.return_value = [_redeem(rewards[0]), rewards[1], rewards[2]]
    await async_poll(hass, freezer)

    assert len(events) == 1
    assert events[0].data["new_state"].attributes["reward"] == "Extra screen time"


async def test_two_redemptions_in_one_poll_are_two_events(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A minute is long enough for two children to cash in."""
    await setup_integration(hass, mock_config_entry)
    fired: list[str] = []

    @callback
    def record(event: Event) -> None:
        if event.data["entity_id"] != REDEEMED:
            return
        # Availability writes reach this entity too, and carry no event.
        if (reward := event.data["new_state"].attributes.get("reward")) is not None:
            fired.append(reward)

    hass.bus.async_listen("state_changed", record)

    mock_client.get_rewards.return_value = [
        _redeem(rewards[0]),
        rewards[1],
        _redeem(rewards[2], LATER),
    ]
    await async_poll(hass, freezer)

    assert fired == ["Extra screen time", "New book"]


async def test_the_same_redemption_fires_once(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A redeemed reward stays redeemed across polls; that is not news."""
    await setup_integration(hass, mock_config_entry)
    mock_client.get_rewards.return_value = [_redeem(rewards[0]), rewards[1], rewards[2]]
    await async_poll(hass, freezer)
    count: list[int] = []
    hass.bus.async_listen(
        "state_changed",
        lambda event: count.append(1) if event.data["entity_id"] == REDEEMED else None,
    )

    await async_poll(hass, freezer)
    await async_poll(hass, freezer)

    assert not count


async def test_redeeming_again_after_respawn_fires_again(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
    freezer: FrozenDateTimeFactory,
) -> None:
    """`respawn_on_redemption` means the same reward can be earned repeatedly."""
    await setup_integration(hass, mock_config_entry)
    mock_client.get_rewards.return_value = [_redeem(rewards[0]), rewards[1], rewards[2]]
    await async_poll(hass, freezer)

    # It respawns...
    mock_client.get_rewards.return_value = list(rewards)
    await async_poll(hass, freezer)
    # ...and is redeemed again later.
    mock_client.get_rewards.return_value = [_redeem(rewards[0], LATER), rewards[1], rewards[2]]
    await async_poll(hass, freezer)

    assert hass.states.get(REDEEMED).attributes["redeemed_at"] == LATER


async def test_a_frame_that_dropped_out(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failed frame must not look like a redemption."""
    await setup_integration(hass, mock_config_entry)

    mock_client.get_frames.return_value = []
    await async_poll(hass, freezer)

    assert hass.states.get(REDEEMED).state == STATE_UNAVAILABLE


async def test_a_redemption_for_an_unknown_profile(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A reward whose category we filter out still reports the redemption."""
    await setup_integration(hass, mock_config_entry)

    orphan = replace(_redeem(rewards[0]), category_id=None)
    mock_client.get_rewards.return_value = [orphan, rewards[1], rewards[2]]
    await async_poll(hass, freezer)

    assert hass.states.get(REDEEMED).attributes["profile"] is None


@pytest.mark.usefixtures("mock_client")
async def test_one_entity_per_frame(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """One of each kind per frame, not one per reward or chore.

    Rewards respawn and chores fall out of today's window; entities keyed to
    individual ones would churn through the registry.
    """
    await setup_integration(hass, mock_config_entry)

    assert sorted(
        entry.unique_id
        for entry in er.async_entries_for_config_entry(entity_registry, mock_config_entry.entry_id)
        if entry.domain == "event"
    ) == ["5455113_chore_completed", "5455113_reward_redeemed"]


COMPLETED = "event.kitchen_chore_completed"


def _complete(chore: Chore, when: str = WHEN) -> Chore:
    """Return the chore as the API would report it once completed."""
    return replace(chore, status="complete", completed_at=dt_util.parse_datetime(when))


async def test_todays_completed_chores_do_not_fire_on_startup(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The chore fixtures already include one Alex finished earlier today."""
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(COMPLETED).state == STATE_UNKNOWN


async def test_a_chore_completed_at_the_frame_fires(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A child ticked something off on the chore chart."""
    await setup_integration(hass, mock_config_entry)

    mock_client.get_chores.return_value = [_complete(chores[0]), *chores[1:]]
    await async_poll(hass, freezer)

    state = hass.states.get(COMPLETED)
    assert state.attributes["event_type"] == "completed"
    assert state.attributes["chore"] == "Dishes"
    assert state.attributes["profile"] == "Alex"
    assert state.attributes["category_id"] == CATEGORY_ID
    assert state.attributes["completed_at"] == WHEN
    assert state.attributes["up_for_grabs"] is False


async def test_an_up_for_grabs_chore_credits_the_claimant(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    unassigned_chores: ChoreGroups,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The interesting case: the chore had no owner until somebody claimed it.

    `completed_category` is who did it, and for an unassigned chore that is the
    only record of who to credit.
    """
    await setup_integration(hass, mock_config_entry)
    chore = unassigned_chores.chores["today"][0]
    claimed = replace(_complete(chore), completed_category_id=OTHER_CATEGORY_ID)
    mock_client.get_all_chores.return_value = replace(
        unassigned_chores, chores={**unassigned_chores.chores, "today": [claimed]}
    )

    await async_poll(hass, freezer)

    state = hass.states.get(COMPLETED)
    assert state.attributes["chore"] == "Vacuum"
    assert state.attributes["profile"] == "Sam"
    assert state.attributes["up_for_grabs"] is True


async def test_reopening_a_chore_does_not_fire(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Unchecking is not an achievement."""
    await setup_integration(hass, mock_config_entry)
    mock_client.get_chores.return_value = [_complete(chores[0]), *chores[1:]]
    await async_poll(hass, freezer)
    fired: list[str] = []

    @callback
    def record(event: Event) -> None:
        if event.data["entity_id"] == COMPLETED:
            fired.append(event.data["new_state"].state)

    hass.bus.async_listen("state_changed", record)
    mock_client.get_chores.return_value = list(chores)
    await async_poll(hass, freezer)

    assert not fired


async def test_completing_again_after_reopening_fires_again(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A chore put back and finished properly is news again."""
    await setup_integration(hass, mock_config_entry)
    mock_client.get_chores.return_value = [_complete(chores[0]), *chores[1:]]
    await async_poll(hass, freezer)
    mock_client.get_chores.return_value = list(chores)
    await async_poll(hass, freezer)

    mock_client.get_chores.return_value = [_complete(chores[0], LATER), *chores[1:]]
    await async_poll(hass, freezer)

    assert hass.states.get(COMPLETED).attributes["completed_at"] == LATER


async def test_two_chores_completed_in_one_poll(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A minute is long enough for a child to finish two things."""
    await setup_integration(hass, mock_config_entry)
    fired: list[str] = []

    @callback
    def record(event: Event) -> None:
        if event.data["entity_id"] != COMPLETED:
            return
        if (chore := event.data["new_state"].attributes.get("chore")) is not None:
            fired.append(chore)

    hass.bus.async_listen("state_changed", record)
    mock_client.get_chores.return_value = [
        _complete(chores[0]),
        _complete(chores[1], LATER),
        *chores[2:],
    ]
    await async_poll(hass, freezer)

    assert fired == ["Dishes", "Recycling"]


async def test_chore_events_survive_a_lost_frame(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failed frame must not look like a completion."""
    await setup_integration(hass, mock_config_entry)

    mock_client.get_frames.return_value = []
    await async_poll(hass, freezer)

    assert hass.states.get(COMPLETED).state == STATE_UNAVAILABLE
