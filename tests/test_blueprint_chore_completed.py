"""End-to-end test for the chore-completed blueprint.

Loading a blueprint proves its shape. It does not prove the templates inside it
resolve, that the notification carries an action, or that tapping that action
reaches the API — and those are the parts of this one that would fail quietly.
So this drives the whole path: a chore is completed on the frame, the
notification goes out, somebody disagrees, and the chore is reopened.

**Why this does not use `async_poll`.** The automation notifies and then parks on
`wait_for_trigger` until somebody answers or the undo window runs out. Time is
frozen in these tests, so that window never elapses on its own — and
`async_block_till_done` waits for pending tasks, including the parked script. The
shared helper drains twice and would therefore wait for a script that is waiting
for a clock nothing is advancing. Every test here instead lets the poll run,
waits for the notification alone, and only drains once the script has been given
a reason to finish.
"""

from __future__ import annotations

import asyncio
import pathlib
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pyskylight.exceptions import ApiError
from pyskylight.models import Chore
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
    / "chore_completed.yaml"
)
CHORE_EVENT = "event.kitchen_chore_completed"
ALEX_CHORES = "todo.kitchen_alex_chores"

#: Short enough that the clock can be pushed past it, long enough that nothing
#: times out while a test is still setting up.
UNDO_WINDOW = timedelta(minutes=5)


def _install(config_dir: str) -> None:
    """Put the blueprint where Home Assistant looks for one."""
    installed = pathlib.Path(config_dir) / "blueprints" / "automation" / "skylight"
    installed.mkdir(parents=True, exist_ok=True)
    (installed / BLUEPRINT.name).write_text(BLUEPRINT.read_text())


class Notifications:
    """The mobile app's notify service, and what it was sent."""

    def __init__(self) -> None:
        """Start with nothing sent."""
        self.calls: list[ServiceCall] = []
        self._arrived = asyncio.Event()

    async def record(self, call: ServiceCall) -> None:
        """Stand in for the app receiving one."""
        self.calls.append(call)
        self._arrived.set()

    async def async_wait(self) -> ServiceCall:
        """Wait for the next notification and return it.

        The timeout is wall-clock rather than Home Assistant's, so freezing time
        does not disarm it: a blueprint that never notifies fails here in
        seconds instead of hanging until pytest gives up.
        """
        async with asyncio.timeout(10):
            await self._arrived.wait()
        self._arrived.clear()
        return self.calls[-1]


@pytest.fixture
def notifications(hass: HomeAssistant) -> Notifications:
    """Register a notify service the blueprint's template will resolve to."""
    recorder = Notifications()
    hass.services.async_register("notify", "mobile_app_test_phone", recorder.record)
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


async def _automate(hass: HomeAssistant, phone: str) -> None:
    """Set the blueprint up against the integration's real entities."""
    await hass.async_add_executor_job(_install, hass.config.config_dir)
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "alias": "chore completed",
                "use_blueprint": {
                    "path": f"skylight/{BLUEPRINT.name}",
                    "input": {
                        "chore_event": CHORE_EVENT,
                        "chore_lists": [ALEX_CHORES],
                        "notify_device": phone,
                        "undo_window": {"minutes": UNDO_WINDOW.seconds // 60},
                    },
                },
            }
        },
    )
    await hass.async_block_till_done()


async def _complete_a_chore(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    chores: list[Chore],
    freezer: FrozenDateTimeFactory,
    notifications: Notifications,
) -> ServiceCall:
    """Make the frame report Dishes as done, and return the notification.

    Deliberately not `async_poll`: see the module docstring. The automation is
    still parked when this returns, which is the state the caller needs it in.
    """
    mock_client.get_chores.return_value = [
        replace(chores[0], completed_on=chores[2].completed_on),
        *chores[1:],
    ]
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    return await notifications.async_wait()


async def _let_the_window_close(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Push the clock past the undo window so the parked script finishes.

    Every test has to do this, whether or not it cares about the timeout: a
    script still waiting when the test ends is a lingering timer, and the test
    harness fails the run for one.
    """
    freezer.tick(UNDO_WINDOW * 2)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_completion_notifies_with_an_undo(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
    freezer: FrozenDateTimeFactory,
    notifications: Notifications,
    phone: str,
) -> None:
    """The notification names the chore and carries a button."""
    await setup_integration(hass, mock_config_entry)
    await _automate(hass, phone)

    sent = (await _complete_a_chore(hass, mock_client, chores, freezer, notifications)).data

    assert sent["title"] == "Alex finished a chore"
    assert "Dishes" in sent["message"]
    actions = sent["data"]["actions"]
    assert len(actions) == 1
    assert actions[0]["title"] == "Not done"
    # Unique per run, so two pending notifications cannot answer for each other.
    assert actions[0]["action"].startswith("SKYLIGHT_UNDO_")

    await _let_the_window_close(hass, freezer)


async def test_disagreeing_reopens_the_chore(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
    freezer: FrozenDateTimeFactory,
    notifications: Notifications,
    phone: str,
) -> None:
    """Tapping the button puts the chore back on the list.

    The whole point of the blueprint, and the part no amount of schema checking
    would have caught.
    """
    await setup_integration(hass, mock_config_entry)
    await _automate(hass, phone)
    sent = await _complete_a_chore(hass, mock_client, chores, freezer, notifications)

    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": sent.data["data"]["actions"][0]["action"]},
    )
    await hass.async_block_till_done()

    # Dishes is a one-off chore, so no instance date is sent.
    mock_client.uncomplete_chore.assert_awaited_once_with("5455113", "1")
    assert notifications.calls[-1].data["title"] == "Put back"


async def test_ignoring_the_notification_changes_nothing(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
    freezer: FrozenDateTimeFactory,
    notifications: Notifications,
    phone: str,
) -> None:
    """Agreeing is silence, and silence must not reopen anything."""
    await setup_integration(hass, mock_config_entry)
    await _automate(hass, phone)
    await _complete_a_chore(hass, mock_client, chores, freezer, notifications)

    hass.bus.async_fire("mobile_app_notification_action", {"action": "SOMETHING_ELSE"})
    await _let_the_window_close(hass, freezer)

    mock_client.uncomplete_chore.assert_not_awaited()


async def test_the_undo_expires(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
    freezer: FrozenDateTimeFactory,
    notifications: Notifications,
    phone: str,
) -> None:
    """A button tapped after the window has closed must not reopen the chore.

    The window is what stops a notification sitting on a phone for days and
    then undoing something finished long ago. Without it the automation would
    wait for ever, and the action id it is listening for would stay live.
    """
    await setup_integration(hass, mock_config_entry)
    await _automate(hass, phone)
    sent = await _complete_a_chore(hass, mock_client, chores, freezer, notifications)

    await _let_the_window_close(hass, freezer)
    # The notification is cleared when the window closes, whichever way it went.
    assert notifications.calls[-1].data["message"] == "clear_notification"

    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": sent.data["data"]["actions"][0]["action"]},
    )
    await hass.async_block_till_done()

    mock_client.uncomplete_chore.assert_not_awaited()


async def test_a_dropped_poll_does_not_replay_the_last_completion(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    chores: list[Chore],
    freezer: FrozenDateTimeFactory,
    notifications: Notifications,
    phone: str,
) -> None:
    """A chore nobody touched must not be announced because the network blipped.

    An event entity's state is the timestamp of its last event, and its
    attributes are that event's payload. A failed poll makes the entity
    unavailable; the next good one restores the same timestamp. That is a state
    change, a bare state trigger fires on it, and the automation then reads
    attributes describing something finished days ago.

    This is what produced a "Take pills" notification for a chore still sitting
    on the list, unticked, with its owner out of the house.
    """
    await setup_integration(hass, mock_config_entry)
    await _automate(hass, phone)

    await _complete_a_chore(hass, mock_client, chores, freezer, notifications)
    await _let_the_window_close(hass, freezer)
    announced = len(notifications.calls)

    # More than the coordinator tolerates: it serves the previous snapshot for a
    # few consecutive failures before giving up, which is why one bad poll is
    # not enough to reach this.
    mock_client.get_chores.side_effect = ApiError(500, "boom")
    for _ in range(TOLERATED_FAILURES + 1):
        freezer.tick(SCAN_INTERVAL)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert hass.states.get(CHORE_EVENT).state == STATE_UNAVAILABLE

    # And comes back, with nothing having happened in between.
    mock_client.get_chores.side_effect = None
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert len(notifications.calls) == announced, "coming back is not a completion"
