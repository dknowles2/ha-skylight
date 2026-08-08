"""End-to-end test against a real Skylight account and display.

Skipped unless ``SKYLIGHT_LIVE=1`` is set, so CI and ordinary runs stay offline.
Credentials come from ``~/.skylight`` (``username = ...`` / ``password = ...``).

    SKYLIGHT_LIVE=1 uv run pytest tests/test_live.py -v -s

Controls are driven through Home Assistant's own service calls, and each result
is verified with an **independent** pyskylight client reading the API directly —
so a pass cannot be an artefact of the integration believing its own writes.
Everything is restored in a finally block, and the restoration is asserted.

Warning:
    Skylight rate-limits logins hard. An earlier version of this file logged in
    twice per test — about twenty OAuth logins in a few seconds — and the
    account began refusing logins outright for several minutes.

    That is why this is one long test rather than a tidy parametrized suite: a
    whole run performs exactly **two** logins, one for the integration and one
    for the verifier. Do not split it into per-case tests, and never retry a
    failed login in a loop.
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
import pytest_socket
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from pyskylight import PasswordAuth, Skylight
from pyskylight.exceptions import ApiError
from pyskylight.models import Device
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skylight.const import DOMAIN

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("SKYLIGHT_LIVE"),
        reason="live tests need SKYLIGHT_LIVE=1 and real credentials",
    ),
]

CREDENTIALS = pathlib.Path.home() / ".skylight"
# The real household frame and its display.
FRAME_ID = "5455113"
DEVICE_ID = "5759923"
SLUG = "kitchen_calendar"

# Every field the run touches, so restoration can be asserted field by field.
# The nightlight and sleep sound fields are absent because this display is a
# calendar: the API would accept writes to them and this frame reports them, but
# they are Buddy settings and the integration builds no controls for them here.
RESTORED_FIELDS = (
    "name",
    "brightness",
    "sleeps_at",
    "wakes_at",
    "slideshow_speed",
    "show_caption",
    "blur_effect",
    "side_by_side",
    "show_heart",
)


@pytest.fixture(autouse=True)
def _allow_network(socket_enabled: None) -> None:
    """Let this test reach the internet.

    The Home Assistant test harness blocks sockets outright, and `socket_enabled`
    only re-opens them for 127.0.0.1.
    """
    pytest_socket.enable_socket()
    pytest_socket._remove_restrictions()


def _credentials() -> tuple[str, str]:
    values: dict[str, str] = {}
    for line in CREDENTIALS.read_text().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values["username"], values["password"]


async def _device(client: Skylight) -> Device:
    return next(d for d in await client.get_devices(FRAME_ID) if d.id == DEVICE_ID)


@pytest.fixture
async def verifier() -> AsyncGenerator[Skylight]:
    """A second client, so verification never trusts the integration's view."""
    username, password = _credentials()
    async with Skylight(PasswordAuth(username, password)) as client:
        yield client


@pytest.fixture
async def real_session() -> AsyncGenerator[aiohttp.ClientSession]:
    """Give the integration a session created on this test's event loop.

    Home Assistant's shared session is built by the test harness on a different
    loop, which aiohttp refuses to use ("attached to a different loop"). This is
    a harness accommodation only — production still uses Home Assistant's
    session.
    """
    async with aiohttp.ClientSession() as session:
        with patch("custom_components.skylight.async_get_clientsession", return_value=session):
            yield session


async def _buddy_gating_failures(hass: HomeAssistant, verifier: Skylight) -> list[str]:
    """Check that this display gets no nightlight and no sleep sound controls.

    Every server-side signal says it should have them. The fields come back from
    `GET`, a `PUT` to any of them returns 200 and survives a re-read, and
    `nightlight_color` is validated against its enum — all verified against this
    exact display. Only `role` says otherwise, and Skylight's own client agrees:
    it offers these settings solely on Buddy hardware.

    So this is the one thing the rest of the run cannot catch. Driving these
    controls live would pass against a control that does nothing; what is
    checked instead is that the gate still matches the hardware.
    """
    failures: list[str] = []
    if (await _device(verifier)).role == "buddy":
        failures.append("this display now reports role=buddy; the gating needs revisiting")
    for domain, key in (
        ("switch", "nightlight"),
        ("number", "nightlight_brightness"),
        ("select", "nightlight_color"),
        ("number", "sleep_sound_volume"),
        ("sensor", "sleep_sound"),
    ):
        if hass.states.get(f"{domain}.{SLUG}_{key}") is not None:
            failures.append(f"{key} should not be built for a calendar display")

    # The server-side Buddy check that does exist, for contrast. If this ever
    # stops failing, the account has Buddy hardware on it and the gate above
    # should start producing entities.
    try:
        await verifier.create_alarm(FRAME_ID, DEVICE_ID, time="07:00")
    except ApiError as err:
        if err.status != 422 or "buddy" not in str(err).lower():
            failures.append(f"alarms now fail differently on this display: {err}")
    else:
        # Not expected to be reachable; clean up rather than leave an alarm on
        # real hardware if it ever is.
        failures.append("alarms are no longer refused; this display may now be a Buddy")
        for alarm in await verifier.get_alarms(FRAME_ID, DEVICE_ID):
            await verifier.delete_alarm(FRAME_ID, DEVICE_ID, alarm.id)
    return failures


async def test_controls_reach_the_real_display(
    hass: HomeAssistant, verifier: Skylight, real_session: aiohttp.ClientSession
) -> None:
    """Drive every control against real hardware, then put it all back."""
    username, password = _credentials()
    before = await _device(verifier)
    print(f"\nBEFORE: {[(f, getattr(before, f)) for f in RESTORED_FIELDS]}")

    entry = MockConfigEntry(
        domain=DOMAIN,
        title=username,
        unique_id="live",
        data={"username": username, "password": password},
    )
    entry.add_to_hass(hass)
    failures: list[str] = []

    try:
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED, f"setup failed: {entry.state}"

        # Built from real data, so this also proves the whole read path.
        assert hass.states.get(f"switch.{SLUG}_blur_effect") is not None
        assert hass.states.get("calendar.the_knowles_calendar") is not None

        cases: list[tuple[str, str, str, dict[str, Any], str, object]] = [
            (
                "number",
                "set_value",
                f"number.{SLUG}_brightness",
                {"value": 180},
                "brightness",
                180,
            ),
            (
                "time",
                "set_value",
                f"time.{SLUG}_sleeps_at",
                {"time": "22:30:00"},
                "sleeps_at",
                "22:30",
            ),
            (
                "number",
                "set_value",
                f"number.{SLUG}_slideshow_speed",
                {"value": 15},
                "slideshow_speed",
                15,
            ),
            (
                "switch",
                SERVICE_TURN_OFF,
                f"switch.{SLUG}_show_captions",
                {},
                "show_caption",
                False,
            ),
            (
                "switch",
                SERVICE_TURN_ON,
                f"switch.{SLUG}_side_by_side",
                {},
                "side_by_side",
                True,
            ),
        ]

        for domain, service, entity_id, data, field, expected in cases:
            label = f"{entity_id} -> {field}={expected!r}"
            try:
                await hass.services.async_call(
                    domain, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
                )
            except Exception as err:
                failures.append(f"{label}: service raised {err}")
                print(f"  FAIL {label}: service raised {err}")
                continue

            actual = getattr(await _device(verifier), field)
            if actual != expected:
                failures.append(f"{label}: display reads {actual!r}")
                print(f"  FAIL {label}: display reads {actual!r}")
                continue

            # The entity should agree without waiting for the next poll.
            state = hass.states.get(entity_id)
            print(f"  ok   {label}; entity reads {state.state!r}")

        # The reason controls live on the device rather than the frame. If
        # Skylight ever makes this endpoint work, this fails and the integration
        # can be revisited.
        assert before.brightness is not None
        await verifier.update_frame(FRAME_ID, brightness=before.brightness - 50)
        if (await _device(verifier)).brightness == before.brightness - 50:
            failures.append(
                "PUT /api/frames/{id} now applies display settings; revisit update_device"
            )

        failures.extend(await _buddy_gating_failures(hass, verifier))
    finally:
        await verifier.update_device(
            FRAME_ID, DEVICE_ID, **{f: getattr(before, f) for f in RESTORED_FIELDS}
        )
        after = await _device(verifier)
        print(f"AFTER:  {[(f, getattr(after, f)) for f in RESTORED_FIELDS]}")
        drifted = {
            field: (getattr(before, field), getattr(after, field))
            for field in RESTORED_FIELDS
            if getattr(before, field) != getattr(after, field)
        }
        assert not drifted, f"the display was not restored: {drifted}"

    assert not failures, "\n".join(failures)
