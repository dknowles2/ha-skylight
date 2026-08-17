"""End-to-end test for the reward-redeemed blueprint.

It used to take one input — a sequence of actions to run — which loads and
validates perfectly while telling a user nothing about what to put there. It now
offers a device to notify instead, and the actions only as an escape hatch.

Neither route is proved by loading the blueprint: the device is turned into a
`notify.mobile_app_<name>` service name by a template, and a template that
produces a service name with a stray space in it fails at the point of calling.
So this drives both, and the case where somebody supplies neither.
"""

from __future__ import annotations

import asyncio
import pathlib
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pyskylight.exceptions import ApiError
from pyskylight.models import Reward
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.skylight.const import SCAN_INTERVAL, TOLERATED_FAILURES

from .conftest import setup_integration

BLUEPRINT = (
    pathlib.Path(__file__).parent.parent
    / "blueprints"
    / "automation"
    / "skylight"
    / "reward_redeemed.yaml"
)
REDEEMED_EVENT = "event.kitchen_reward_redeemed"


def _install(config_dir: str) -> None:
    """Put the blueprint where Home Assistant looks for one."""
    installed = pathlib.Path(config_dir) / "blueprints" / "automation" / "skylight"
    installed.mkdir(parents=True, exist_ok=True)
    (installed / BLUEPRINT.name).write_text(BLUEPRINT.read_text())


class Calls:
    """A service, and what it was sent."""

    def __init__(self) -> None:
        """Start with nothing sent."""
        self.calls: list[ServiceCall] = []
        self._arrived = asyncio.Event()

    async def record(self, call: ServiceCall) -> None:
        """Note one down."""
        self.calls.append(call)
        self._arrived.set()

    async def async_wait(self) -> ServiceCall:
        """Wait for the next one, on the wall clock rather than the frozen one."""
        async with asyncio.timeout(10):
            await self._arrived.wait()
        self._arrived.clear()
        return self.calls[-1]


@pytest.fixture
def notifications(hass: HomeAssistant) -> Calls:
    """The mobile app's notify service, as the blueprint's template names it."""
    recorder = Calls()
    hass.services.async_register("notify", "mobile_app_test_phone", recorder.record)
    return recorder


@pytest.fixture
def spoken(hass: HomeAssistant) -> Calls:
    """Somewhere for a hand-written action to land, standing in for a speaker."""
    recorder = Calls()
    hass.services.async_register("notify", "somewhere_else", recorder.record)
    return recorder


@pytest.fixture
def phone(hass: HomeAssistant, device_registry: dr.DeviceRegistry) -> str:
    """A device named so the blueprint resolves notify.mobile_app_test_phone."""
    entry = MockConfigEntry(domain="mobile_app")
    entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("mobile_app", "test-phone")},
        name="Test Phone",
    )
    return device.id


async def _automate(hass: HomeAssistant, **inputs: object) -> None:
    """Set the blueprint up with whichever inputs a test cares about."""
    await hass.async_add_executor_job(_install, hass.config.config_dir)
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "alias": "reward redeemed",
                "use_blueprint": {
                    "path": f"skylight/{BLUEPRINT.name}",
                    "input": {"redeemed_event": REDEEMED_EVENT, **inputs},
                },
            }
        },
    )
    await hass.async_block_till_done()


async def _redeem(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    rewards: list[Reward],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Make the frame report Extra screen time as redeemed, just now.

    `dt_util.utcnow()` rather than the fixture's own timestamp: the event entity
    refuses to announce anything that happened before it started watching, and
    the fixture's redemption is dated in the past. A redemption that just
    happened is what this blueprint is for anyway.
    """
    mock_client.get_rewards.return_value = [
        replace(rewards[0], redeemed_at=dt_util.utcnow()),
        *rewards[1:],
    ]
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_a_device_is_all_it_takes(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
    freezer: FrozenDateTimeFactory,
    notifications: Calls,
    phone: str,
) -> None:
    """Pick a phone and the notification writes itself.

    The point of the change: the blueprint used to demand a sequence of actions
    and offer no hint of what belonged in it.
    """
    await setup_integration(hass, mock_config_entry)
    await _automate(hass, notify_device=phone)

    await _redeem(hass, mock_client, rewards, freezer)

    sent = (await notifications.async_wait()).data
    assert sent["title"] == "Alex redeemed a reward"
    assert "Extra screen time" in sent["message"]
    assert "5 points" in sent["message"]


async def test_hand_written_actions_still_work(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
    freezer: FrozenDateTimeFactory,
    spoken: Calls,
) -> None:
    """The escape hatch is why the action input is still there.

    Anyone who was already using this blueprint supplies exactly this and no
    device, and their automation has to go on working untouched.
    """
    await setup_integration(hass, mock_config_entry)
    await _automate(
        hass,
        notification=[
            {
                "action": "notify.somewhere_else",
                "data": {"message": "{{ profile }} spent {{ points }} on {{ reward }}"},
            }
        ],
    )

    await _redeem(hass, mock_client, rewards, freezer)

    assert (await spoken.async_wait()).data["message"] == "Alex spent 5 on Extra screen time"


async def test_a_device_and_actions_both_run(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
    freezer: FrozenDateTimeFactory,
    notifications: Calls,
    spoken: Calls,
    phone: str,
) -> None:
    """Choosing both is not an either/or, and the description says so."""
    await setup_integration(hass, mock_config_entry)
    await _automate(
        hass,
        notify_device=phone,
        notification=[{"action": "notify.somewhere_else", "data": {"message": "also this"}}],
    )

    await _redeem(hass, mock_client, rewards, freezer)

    assert (await notifications.async_wait()).data["title"] == "Alex redeemed a reward"
    assert (await spoken.async_wait()).data["message"] == "also this"


async def test_neither_is_not_an_error(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
    freezer: FrozenDateTimeFactory,
    notifications: Calls,
) -> None:
    """Both inputs are optional, so both can be left empty.

    Nothing useful happens, but the automation must not throw — an empty device
    id would otherwise be turned into a service name and called.
    """
    await setup_integration(hass, mock_config_entry)
    await _automate(hass)

    await _redeem(hass, mock_client, rewards, freezer)

    assert notifications.calls == []
    assert hass.states.get("automation.reward_redeemed").state == "on"


async def test_a_dropped_poll_does_not_replay_the_last_redemption(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    rewards: list[Reward],
    freezer: FrozenDateTimeFactory,
    notifications: Calls,
    phone: str,
) -> None:
    """A redemption nobody made must not be announced because the network blipped.

    Same shape as the chore blueprint's version. An event entity's state is the
    timestamp of its last event and its attributes are that event's payload, so
    when a run of failed polls takes the entity unavailable, its recovery
    restores the identical timestamp and the old payload — a state change that a
    bare state trigger fires on.
    """
    await setup_integration(hass, mock_config_entry)
    await _automate(hass, notify_device=phone)
    await _redeem(hass, mock_client, rewards, freezer)
    await notifications.async_wait()
    announced = len(notifications.calls)

    mock_client.get_rewards.side_effect = ApiError(500, "boom")
    for _ in range(TOLERATED_FAILURES + 1):
        freezer.tick(SCAN_INTERVAL)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert hass.states.get(REDEEMED_EVENT).state == STATE_UNAVAILABLE

    mock_client.get_rewards.side_effect = None
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert len(notifications.calls) == announced, "coming back is not a redemption"
