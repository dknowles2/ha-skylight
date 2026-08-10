"""Tests for shipping the Lovelace cards with the integration.

The cards are served by the integration and registered with the frontend, so a
user installs nothing and configures no resource. What can be tested here is the
Python either side of them: that the files are served, that the frontend is told
to load each one, that a second config entry does not register the same path
twice, and that none of it can stop the integration setting up.

The rest is the seams. A card is JavaScript reading strings Python wrote and
options a document promised, and neither end notices when the other moves — so
the points line, the documented options and the documented examples are each
checked against the file that has to honour them.

Rendering needs a browser and is not covered.
"""

from __future__ import annotations

import json
import pathlib
import re
from unittest.mock import AsyncMock

import pytest
import yaml
from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pyskylight.models import Chore
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skylight.frontend import (
    CARDS,
    DATA_REGISTERED,
    URL_BASE,
    async_register_card,
)
from custom_components.skylight.todo import _chore_description

from .conftest import setup_integration

COMPONENT = pathlib.Path(__file__).parent.parent / "custom_components" / "skylight"
WWW = COMPONENT / "www"
CARD_FILE = WWW / "skylight-rewards.js"
CHORES_FILE = WWW / "skylight-chores.js"

#: Each card's file, the element it defines, and the name the picker shows.
ELEMENTS = [
    (CARD_FILE, "skylight-rewards", "Skylight rewards"),
    (CHORES_FILE, "skylight-chores", "Skylight chores"),
]


@pytest.mark.parametrize(("path", "element", "name"), ELEMENTS)
def test_the_cards_ship_with_the_component(path: pathlib.Path, element: str, name: str) -> None:
    """They have to be inside the component directory to reach the release zip.

    The zip is built from that directory, so a card kept anywhere else in the
    repository would be missing from every install that came from a release.
    """
    assert path.is_file()
    assert path.name in CARDS
    body = path.read_text()
    assert f'customElements.define("{element}"' in body
    # Registered with the card picker, so it is findable without reading docs.
    assert f'name: "{name}"' in body


@pytest.mark.parametrize(("path", "element", "name"), ELEMENTS)
def test_the_cards_have_a_visual_editor(path: pathlib.Path, element: str, name: str) -> None:
    """Each card is configurable without anyone opening the YAML editor.

    Home Assistant asks the card class for `getConfigElement`, so a missing
    editor is not an error — the dialog just falls back to raw YAML, which is
    the failure this guards against.
    """
    body = path.read_text()
    assert "static async getConfigElement()" in body
    assert f'customElements.define("{element}-editor"' in body
    # The editor tells Home Assistant about a change by firing this; without it
    # the dialog appears to work and saves nothing.
    assert "config-changed" in body


@pytest.mark.parametrize(("path", "element", "name"), ELEMENTS)
def test_registering_an_element_twice_is_harmless(
    path: pathlib.Path, element: str, name: str
) -> None:
    """A card can legitimately be loaded twice.

    The integration registers it with the frontend, and a display whose browser
    never picked that up can be pointed at the same URL as a Lovelace resource
    instead. `customElements.define` throws on the second call, and that error
    would surface as the card not working at all.
    """
    body = path.read_text()
    assert f'if (!customElements.get("{element}")) {{' in body
    assert f'if (!customElements.get("{element}-editor")) {{' in body
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


def test_the_chore_card_reads_its_items_from_the_subscription() -> None:
    """A to-do entity's state is a count, not its items.

    The items only exist over the websocket, so a card built to read attributes
    would render an empty list forever and look like a data problem.
    """
    body = CHORES_FILE.read_text()
    assert '"todo/item/subscribe"' in body
    # And the subscription has to be closed when the card leaves the dashboard.
    assert "disconnectedCallback()" in body


def test_the_chore_card_checks_items_off_through_the_service() -> None:
    """Going through `todo.update_item` is what credits the right child.

    An Up for Grabs chore is claimed by whoever tapped it, which Home Assistant
    knows only because the service call carries the signed-in user's context. A
    card that wrote to the API another way would credit nobody.
    """
    body = CHORES_FILE.read_text()
    assert '"todo",\n      "update_item"' in body
    assert '"needs_action"' in body and '"completed"' in body


def test_every_documented_chore_card_option_exists() -> None:
    """The docs list the card's options in a table; the card has to read them.

    An option that only exists in the table is worse than an undocumented one —
    it is written into a dashboard, silently ignored, and looks like the card is
    broken.
    """
    doc = (pathlib.Path(__file__).parent.parent / "docs" / "dashboard.md").read_text()
    section = doc.split("### What it does that a to-do list does not")[0]
    options = set(re.findall(r"^\| `(\w+)` \|", section, re.M))
    assert options, "docs/dashboard.md no longer documents the chore card's options"

    body = CHORES_FILE.read_text()
    for option in options:
        assert f"_config.{option}" in body, option


def test_documented_chore_cards_are_configs_the_card_accepts() -> None:
    """Every `custom:skylight-chores` block in the docs is a usable card.

    `setConfig` refuses a card with no entity, and refuses one aimed at
    something that is not a to-do list. A doc example that trips either is a
    paste-and-see-a-red-box.
    """
    doc = (pathlib.Path(__file__).parent.parent / "docs" / "dashboard.md").read_text()
    blocks = re.findall(r"```yaml\n(.*?)```", doc, re.S)
    cards = [
        card
        for block in blocks
        for card in _flatten(yaml.safe_load(block))
        if card.get("type") == "custom:skylight-chores"
    ]
    assert cards, "docs/dashboard.md no longer shows the chore card"
    for card in cards:
        assert card["entity"].startswith("todo."), card


def _flatten(parsed: object) -> list[dict]:
    """Every mapping anywhere in a parsed YAML block."""
    if isinstance(parsed, dict):
        return [parsed, *(c for value in parsed.values() for c in _flatten(value))]
    if isinstance(parsed, list):
        return [c for item in parsed for c in _flatten(item)]
    return []


ROOT = pathlib.Path(__file__).parent.parent
IMAGES = ROOT / "docs" / "images"


def _referenced_images() -> dict[pathlib.Path, list[pathlib.Path]]:
    """Every screenshot referenced by a document, and which document wants it."""
    found: dict[pathlib.Path, list[pathlib.Path]] = {}
    for doc in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        for path in re.findall(r'(?:src|srcset)="([^"]+\.png)"', doc.read_text()):
            found.setdefault((doc.parent / path).resolve(), []).append(doc)
    return found


def test_every_screenshot_a_document_asks_for_exists() -> None:
    """A missing image renders as a broken icon, and only a reader notices.

    Nothing else in the repository refers to these files, so a rename or a
    tidy-up takes them away silently.
    """
    referenced = _referenced_images()
    assert referenced, "no documentation refers to a screenshot any more"
    for path, docs in referenced.items():
        assert path.is_file(), f"{path.name} is referenced by {[d.name for d in docs]}"


def test_every_screenshot_is_used_and_has_both_themes() -> None:
    """No orphans, and nothing that only looks right in one colour scheme.

    The documents pick between the two with `prefers-color-scheme`, so a card
    photographed in only one theme is a white rectangle on somebody's dark page.
    """
    referenced = set(_referenced_images())
    on_disk = set(IMAGES.glob("*.png"))
    assert not on_disk - referenced, "screenshots nothing refers to"

    for image in on_disk:
        assert image.name.endswith(("-light.png", "-dark.png")), image.name
        other = "-dark.png" if image.name.endswith("-light.png") else "-light.png"
        counterpart = image.with_name(re.sub(r"-(light|dark)\.png$", other, image.name))
        assert counterpart.is_file(), f"{image.name} has no {counterpart.name}"


def test_screenshots_are_exactly_what_the_tool_makes() -> None:
    """`scripts/shoot.py` has to still know how to remake all of them, and only them.

    A screenshot nobody can remake is one that stops matching the card and
    cannot be put right, which is the usual fate of an image pasted in by hand.
    Read from the `SHOTS` table rather than searching the whole file: the names
    appear elsewhere in it, so a looser check passes on a shot that has been
    dropped from the table entirely.
    """
    script = (ROOT / "scripts" / "shoot.py").read_text()
    table = re.search(r"^SHOTS = \[$(.*?)^\]$", script, re.M | re.S)
    assert table, "scripts/shoot.py no longer has a SHOTS table"

    makes = set(re.findall(r'^\s*Shot\("([\w-]+)"', table.group(1), re.M))
    committed = {re.sub(r"-(light|dark)\.png$", "", image.name) for image in IMAGES.glob("*.png")}
    assert makes == committed


def _card_points_pattern() -> re.Pattern[str]:
    """The chore card's own points regex, lifted out of the JavaScript.

    Read from the file rather than copied here on purpose. A copy would be a
    second thing to keep in step, and this test exists precisely because keeping
    two things in step by hand is what fails.
    """
    body = CHORES_FILE.read_text()
    match = re.search(r"^const POINTS_LINE = /(.*)/;$", body, re.M)
    assert match, "the chore card no longer declares POINTS_LINE"
    # The JavaScript flavour used here — anchors, a digit class, `[\s\S]` — is
    # spelled the same way in Python.
    return re.compile(match.group(1))


@pytest.mark.parametrize(
    ("points", "notes"),
    [
        (2, None),
        (2, "Both sides of the sink"),
        (10, "Two loads.\n\nSort the darks."),
        (0, "Just the notes"),
        (None, None),
    ],
)
def test_the_chore_card_can_read_back_what_todo_py_writes(
    points: int | None, notes: str | None
) -> None:
    """The points badge is a string agreement between Python and JavaScript.

    `TodoItem` has six fields and no room for a seventh, so reward points ride
    inside `description` and the card parses them back out. Nothing but this
    test connects the two ends: change the star, the spacing or the blank line
    in `_points_line`, and the card silently stops showing points and starts
    showing "⭐ 2" as if the child had typed it.
    """
    chore = Chore.from_resource(
        {
            "type": "chore",
            "id": "1-2026-08-07",
            "attributes": {
                "summary": "Dishes",
                "start": "2026-08-07",
                "reward_points": points,
                "description": notes,
                "recurring": False,
                "recurrence_set": [],
            },
        }
    )
    description = _chore_description(chore)
    match = _card_points_pattern().match(description or "")

    if not points:
        # Nothing to badge, so the card must show the whole description as the
        # child's own notes rather than eating the first line.
        assert match is None
        assert description == notes
        return

    assert match, f"the card cannot parse {description!r}"
    assert int(match.group(1)) == points
    assert (match.group(2) or None) == notes


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
    """One static path, and a module url per card carrying the version."""
    await setup_integration(hass, mock_config_entry)

    frontend.assert_awaited_once()
    (configs,) = frontend.await_args.args
    assert configs[0].url_path == URL_BASE
    assert configs[0].path.endswith("/www")

    urls = hass.data[DATA_EXTRA_MODULE_URL]
    # Every card needs its own url: the static path serves the directory, but
    # the frontend only loads the modules it has been told about, so a card left
    # out here is served and never fetched.
    assert len(urls) == len(CARDS)
    for card in CARDS:
        # Versioned, so a browser cannot keep serving the previous card after an
        # upgrade — the file name alone never changes.
        assert any(url.startswith(f"{URL_BASE}/{card}?v=") for url in urls), card


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
