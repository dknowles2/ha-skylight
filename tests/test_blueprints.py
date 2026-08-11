"""Tests for the automation blueprints shipped alongside the integration.

These are not loaded by the integration — Home Assistant only discovers
blueprints under the user's own `blueprints/` folder, and `async_populate()`
copies from the `automation` integration rather than from ours. They are
published in the repository and imported by URL.

That makes them the one part of this project with no runtime that would notice a
mistake, so they are validated here against Home Assistant's own schema instead.
"""

from __future__ import annotations

import pathlib

import pytest
from homeassistant.components.blueprint.models import Blueprint
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import parse_yaml

BLUEPRINTS = sorted(
    (pathlib.Path(__file__).parent.parent / "blueprints" / "automation" / "skylight").glob("*.yaml")
)


def _install(config_dir: str, path: pathlib.Path) -> set[str]:
    """Copy a blueprint where Home Assistant looks for one, and name its inputs.

    Reads the file once and returns what the caller needs, so the async test
    never touches the filesystem itself.
    """
    installed = pathlib.Path(config_dir) / "blueprints" / "automation" / "skylight"
    installed.mkdir(parents=True, exist_ok=True)
    body = path.read_text()
    (installed / path.name).write_text(body)
    blueprint = Blueprint(
        parse_yaml(body), path=str(path), expected_domain="automation", schema=BLUEPRINT_SCHEMA
    )
    return set(blueprint.inputs)


def test_there_are_blueprints_to_check() -> None:
    """A glob that matches nothing would make every test below vacuous."""
    assert [path.name for path in BLUEPRINTS] == [
        "chore_completed.yaml",
        "reward_redeemed.yaml",
        "reward_within_reach.yaml",
    ]


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda path: path.name)
def test_blueprint_is_valid(path: pathlib.Path) -> None:
    """Each file is a blueprint Home Assistant would accept."""
    blueprint = Blueprint(
        parse_yaml(path.read_text()),
        path=str(path),
        expected_domain="automation",
        schema=BLUEPRINT_SCHEMA,
    )
    assert blueprint.domain == "automation"
    assert blueprint.metadata["source_url"].endswith(path.name)


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda path: path.name)
def test_every_input_is_used(path: pathlib.Path) -> None:
    """An input nobody reads is a question asked of the user for nothing."""
    body = path.read_text()
    blueprint = Blueprint(
        parse_yaml(body), path=str(path), expected_domain="automation", schema=BLUEPRINT_SCHEMA
    )
    for name in blueprint.inputs:
        assert f"!input {name}" in body, f"{path.name} never uses input {name!r}"


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda path: path.name)
async def test_blueprint_loads_as_an_automation(hass: HomeAssistant, path: pathlib.Path) -> None:
    """Home Assistant accepts it and produces a running automation.

    Set up rather than schema-checked, deliberately. `PLATFORM_SCHEMA` does not
    validate trigger platforms — a blueprint whose trigger reads `nonsense`
    passes it — so anything short of setting the automation up is a test that
    cannot fail for the mistakes most likely to be made here.
    """
    # Through the executor: file reads and writes belong off the event loop,
    # and Home Assistant's own lint rules say so.
    declared = await hass.async_add_executor_job(_install, hass.config.config_dir, path)

    # Only the inputs this blueprint declares; an unknown one is ignored on
    # substitution and would mask a missing `!input`.
    supplied = {
        "rewards": ["number.frame_alex_extra_screen_time"],
        "redeemed_event": "event.frame_reward_redeemed",
        "profile": "Alex",
        "notification": [{"action": "notify.persistent_notification", "data": {"message": "hi"}}],
        "chore_event": "event.frame_chore_completed",
        "chore_lists": ["todo.frame_alex_chores"],
        # Never resolved here: the blueprint only reads the device at run time,
        # and this test is about whether Home Assistant will load the thing.
        "notify_device": "0123456789abcdef0123456789abcdef",
        "undo_window": {"hours": 1},
    }
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "alias": path.stem,
                "use_blueprint": {
                    "path": f"skylight/{path.name}",
                    "input": {name: value for name, value in supplied.items() if name in declared},
                },
            }
        },
    )
    await hass.async_block_till_done()

    assert hass.states.async_entity_ids("automation") == [f"automation.{path.stem}"]
    assert hass.states.get(f"automation.{path.stem}").state == "on"
