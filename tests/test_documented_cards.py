"""Render the dashboard templates in docs/dashboard.md the way a card would.

A Lovelace template is not just Jinja — it is Jinja that survived YAML. The
rewards card shipped with `content: >-`, which folds every newline into a space,
so the whole list rendered as one unreadable line while the template itself was
perfectly correct. Checking the template as text missed it, because the text is
not what Home Assistant receives.

These tests parse the YAML first and render what comes out.
"""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template

DOC = pathlib.Path(__file__).parent.parent / "docs" / "dashboard.md"

REWARDS = [
    # cost, name, points_needed, progress
    (10, "$10 Robux", 4, 60),
    (25, "Five Guys Dinner", 19, 24),
    (15, "30 minutes extra iPad time", 9, 40),
    (5, "10 minutes extra YouTube shorts", 0, 100),
]


def _cards() -> list[dict]:
    """Every YAML block in the doc that parses to a single card."""
    blocks = re.findall(r"```yaml\n(.*?)```", DOC.read_text(), re.S)
    cards = []
    for block in blocks:
        parsed = yaml.safe_load(block)
        if isinstance(parsed, dict) and "type" in parsed:
            cards.append(parsed)
    return cards


def _rewards_card() -> dict:
    """The markdown card that lists a profile's rewards."""
    for card in _cards():
        if card.get("type") == "markdown" and "points_needed" in str(card.get("content", "")):
            return card
    pytest.fail("docs/dashboard.md no longer contains the rewards markdown card")


def _populate(hass: HomeAssistant) -> None:
    """Put reward entities in the state machine, shaped like the real ones."""
    for index, (cost, name, needed, progress) in enumerate(REWARDS):
        hass.states.async_set(
            f"number.frame_jacob_reward_{index}",
            str(cost),
            {
                "profile": "Jacob",
                "reward": name,
                "points_needed": needed,
                "progress": progress,
                "affordable": needed == 0,
            },
        )
    # A second profile's reward, and a device number with no reward attributes:
    # both must be filtered out.
    hass.states.async_set(
        "number.frame_sarah_reward_0",
        "3",
        {"profile": "Sarah", "reward": "Ice cream", "points_needed": 0, "progress": 100},
    )
    hass.states.async_set("number.kitchen_brightness", "180", {})


def _generated_cards(name: str) -> str:
    """The auto-entities template that emits cards of the given type."""
    for card in _cards():
        template = str(card.get("filter", {}).get("template", ""))
        if card.get("type") == "custom:auto-entities" and name in template:
            return template
    pytest.fail(f"docs/dashboard.md no longer generates {name} cards")


async def test_generated_progress_cards_are_valid_configs(hass: HomeAssistant) -> None:
    """auto-entities parses the output as YAML, so it has to be parseable.

    One card per reward, nearest first, aimed at the right entity, and reading
    the percentage from the attribute rather than the state — the state is the
    cost.
    """
    _populate(hass)
    rendered = Template(_generated_cards("entity-progress-card"), hass).async_render(
        parse_result=False
    )

    cards = yaml.safe_load(f"[{rendered.strip().rstrip(',')}]")
    assert [card["name"] for card in cards] == [
        "10 minutes extra YouTube shorts",
        "$10 Robux",
        "30 minutes extra iPad time",
        "Five Guys Dinner",
    ]
    # The card computes balance over cost itself: `attribute` is ignored for a
    # `number` entity, so reading `progress` off the reward silently measures
    # the price instead.
    assert "attribute" not in cards[0]
    assert {card["entity"] for card in cards} == {"sensor.the_knowles_jacob_reward_points"}
    # An object, not a bare entity id. The card's schema is a number, or
    # {entity, attribute}, or {jinja}; a string raises one configuration error
    # per card, which is what a real dashboard reported.
    assert [card["max_value"] for card in cards] == [
        {"entity": "number.frame_jacob_reward_3"},
        {"entity": "number.frame_jacob_reward_0"},
        {"entity": "number.frame_jacob_reward_2"},
        {"entity": "number.frame_jacob_reward_1"},
    ]
    # A tap must never spend points: the redeem buttons are the way to do that.
    assert {card["tap_action"]["action"] for card in cards} == {"none"}
    assert cards[0]["custom_info"] == "Ready!"
    assert cards[1]["custom_info"] == "4 more"
    # Nobody else's rewards.
    assert not any("Ice cream" in card["name"] for card in cards)


async def test_generated_cards_survive_a_quote_in_a_name(hass: HomeAssistant) -> None:
    """A reward named on the frame can contain anything a person would type.

    This does not prove `| to_json` is required — Python's repr quotes an
    apostrophe correctly too — but the card config has to survive one either
    way, and nothing else here would notice if it stopped.
    """
    hass.states.async_set(
        "number.frame_jacob_reward_quote",
        "8",
        {
            "profile": "Jacob",
            "reward": "Dad's car wash",
            "points_needed": 2,
            "progress": 75,
            "affordable": False,
        },
    )
    rendered = Template(_generated_cards("entity-progress-card"), hass).async_render(
        parse_result=False
    )

    cards = yaml.safe_load(f"[{rendered.strip().rstrip(',')}]")
    assert cards[0]["name"] == "Dad's car wash"
    assert cards[0]["max_value"] == {"entity": "number.frame_jacob_reward_quote"}


async def test_rewards_card_renders_one_line_per_reward(hass: HomeAssistant) -> None:
    """The failure this test exists for produced every reward on a single line."""
    _populate(hass)
    rendered = Template(_rewards_card()["content"], hass).async_render(parse_result=False)

    lines = [line for line in rendered.strip().splitlines() if line.strip()]
    assert len(lines) == len(REWARDS)


async def test_rewards_card_is_sorted_by_cost(hass: HomeAssistant) -> None:
    """Cheapest first, so the nearest reward is the one at the top.

    Sorting the entities directly would order by `state` as a string, which puts
    "10" before "5".
    """
    _populate(hass)
    rendered = Template(_rewards_card()["content"], hass).async_render(parse_result=False)

    names = [name for _, name, _, _ in REWARDS]
    lines = [line for line in rendered.strip().splitlines() if line.strip()]
    order = [next(name for name in names if name in line) for line in lines]
    assert order == [
        "10 minutes extra YouTube shorts",
        "$10 Robux",
        "30 minutes extra iPad time",
        "Five Guys Dinner",
    ]


async def test_rewards_card_shows_progress_and_excludes_other_profiles(
    hass: HomeAssistant,
) -> None:
    """A bar per reward, and nobody else's rewards on this person's card."""
    _populate(hass)
    rendered = Template(_rewards_card()["content"], hass).async_render(parse_result=False)

    assert "Ice cream" not in rendered
    assert "★★★★★★☆☆☆☆" in rendered  # $10 Robux, 60%
    assert "4 more" in rendered
    assert "Ready!" in rendered
