"""Tests for shipping the Lovelace card with the integration.

The card is served by the integration and registered with the frontend, so a
user installs nothing and configures no resource. What can be tested here is the
Python either side of it: that the file is served, that the frontend is told to
load it, that a second config entry does not register the same path twice, and
that none of it can stop the integration setting up.

The card's own rendering needs a browser and is not covered.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skylight.frontend import (
    CARD,
    DATA_REGISTERED,
    URL_BASE,
    async_register_card,
)

from .conftest import setup_integration

COMPONENT = pathlib.Path(__file__).parent.parent / "custom_components" / "skylight"
CARD_FILE = COMPONENT / "www" / CARD


def test_the_card_ships_with_the_component() -> None:
    """It has to be inside the component directory to reach the release zip.

    The zip is built from that directory, so a card kept anywhere else in the
    repository would be missing from every install that came from a release.
    """
    assert CARD_FILE.is_file()
    body = CARD_FILE.read_text()
    assert 'customElements.define("skylight-rewards"' in body
    # Registered with the card picker, so it is findable without reading docs.
    assert "window.customCards" in body


def test_the_card_has_a_visual_editor() -> None:
    """The card is configurable without anyone opening the YAML editor.

    Home Assistant asks the card class for `getConfigElement`, so a missing
    editor is not an error — the dialog just falls back to raw YAML, which is
    the failure this guards against.
    """
    body = CARD_FILE.read_text()
    assert "static async getConfigElement()" in body
    assert 'customElements.define("skylight-rewards-editor"' in body
    # The editor tells Home Assistant about a change by firing this; without it
    # the dialog appears to work and saves nothing.
    assert "config-changed" in body


def test_registering_the_element_twice_is_harmless() -> None:
    """The card can legitimately be loaded twice.

    The integration registers it with the frontend, and a display whose browser
    never picked that up can be pointed at the same URL as a Lovelace resource
    instead. `customElements.define` throws on the second call, and that error
    would surface as the card not working at all.
    """
    body = CARD_FILE.read_text()
    assert 'if (!customElements.get("skylight-rewards")) {' in body
    assert 'if (!customElements.get("skylight-rewards-editor")) {' in body
    # And the picker must not list it twice.
    assert "window.customCards.some(" in body


def test_the_card_shows_the_star_balance() -> None:
    """Read from a reward's `balance`, not from a second entity.

    Every reward already carries the profile's balance, so the card needs
    nothing pointed at it and cannot be pointed at the wrong thing.
    """
    body = CARD_FILE.read_text()
    assert "attributes.balance" in body
    # Null is not zero: a profile with no balance recorded must not put the word
    # "null" on a child's wall.
    assert "stars === null || stars === undefined" in body
    assert "show_balance" in body


def test_the_manifest_does_not_hard_depend_on_the_frontend() -> None:
    """A card that cannot be served must not stop the chore chart working."""
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert "frontend" not in manifest["dependencies"]
    assert "frontend" in manifest["after_dependencies"]


@pytest.fixture
def frontend(hass: HomeAssistant) -> AsyncMock:
    """Pretend the frontend and http components are up, and catch the paths."""
    hass.config.components.add("frontend")
    register = AsyncMock()
    hass.http = type("Http", (), {"async_register_static_paths": register})()
    hass.data.setdefault(DATA_EXTRA_MODULE_URL, set())
    return register


async def test_the_card_is_served_and_registered(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    frontend: AsyncMock,
) -> None:
    """One static path, and one module url carrying the version."""
    await setup_integration(hass, mock_config_entry)

    frontend.assert_awaited_once()
    (configs,) = frontend.await_args.args
    assert configs[0].url_path == URL_BASE
    assert configs[0].path.endswith("/www")

    urls = hass.data[DATA_EXTRA_MODULE_URL]
    assert len(urls) == 1
    url = next(iter(urls))
    # Versioned, so a browser cannot keep serving the previous card after an
    # upgrade — the file name alone never changes.
    assert url.startswith(f"{URL_BASE}/{CARD}?v=")


async def test_registering_twice_serves_the_path_once(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    frontend: AsyncMock,
) -> None:
    """A second account on the same Home Assistant must not re-register.

    Registering the same static path twice raises, and every config entry runs
    this on setup.
    """
    await setup_integration(hass, mock_config_entry)
    await async_register_card(hass)

    frontend.assert_awaited_once()


async def test_setup_survives_a_frontend_that_refuses(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    frontend: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A card that cannot be served is a warning, not a failed integration."""
    frontend.side_effect = RuntimeError("already registered")

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("todo.kitchen_alex_chores") is not None
    assert "Could not serve the Skylight card" in caplog.text


async def test_no_frontend_is_not_an_error(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Home Assistant without the frontend loaded still gets its entities.

    No `frontend` fixture here, so the component is absent — which is the state
    this guards against.
    """
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get("todo.kitchen_alex_chores") is not None
    assert not hass.data.get(DATA_REGISTERED)
