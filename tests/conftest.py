"""Fixtures for the Skylight integration tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pyskylight.models import Category, Chore, Frame, RewardPoint, User
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skylight.const import DOMAIN

USER_ID = "12345"
EMAIL = "user@example.com"
PASSWORD = "hunter2"
FRAME_ID = "5455113"
CATEGORY_ID = "77"
OTHER_CATEGORY_ID = "78"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom_components/ in every test."""


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Stub out setup so config flow tests only exercise the flow."""
    with patch("custom_components.skylight.async_setup_entry", return_value=True) as mock:
        yield mock


def _frame(**overrides: Any) -> Frame:
    attributes = {
        "name": "Kitchen",
        "household_name": "The Knowles",
        "timezone": "America/New_York",
        "hardware_model": "skylight-cal-15",
        **overrides,
    }
    return Frame.from_resource({"type": "frame", "id": FRAME_ID, "attributes": attributes})


def _category(category_id: str, label: str) -> Category:
    return Category.from_resource(
        {
            "type": "category",
            "id": category_id,
            "attributes": {"id": int(category_id), "label": label, "color": "#00526D"},
        }
    )


def _chore(chore_id: str, summary: str, category_id: str, *, completed: bool) -> Chore:
    return Chore.from_resource(
        {
            "type": "chore",
            "id": f"{chore_id}-2026-08-07",
            "attributes": {
                "id": f"{chore_id}-2026-08-07",
                "group": chore_id,
                "series": chore_id,
                "summary": summary,
                "status": "pending",
                "start": "2026-08-07",
                "completed_on": "2026-08-07" if completed else None,
                "recurring": False,
                "recurrence_set": [],
            },
            "relationships": {"category": {"data": {"type": "category", "id": category_id}}},
        }
    )


@pytest.fixture
def frames() -> list[Frame]:
    """The frames the fake account owns."""
    return [_frame()]


@pytest.fixture
def categories() -> list[Category]:
    """The family profiles on the frame."""
    return [_category(CATEGORY_ID, "Alex"), _category(OTHER_CATEGORY_ID, "Sam")]


@pytest.fixture
def chores() -> list[Chore]:
    """Today's chores: two open and one done for Alex, one open for Sam."""
    return [
        _chore("1", "Dishes", CATEGORY_ID, completed=False),
        _chore("2", "Recycling", CATEGORY_ID, completed=False),
        _chore("3", "Homework", CATEGORY_ID, completed=True),
        _chore("4", "Laundry", OTHER_CATEGORY_ID, completed=False),
    ]


@pytest.fixture
def reward_points() -> list[RewardPoint]:
    """Point balances; Sam deliberately has none, to exercise the None path."""
    return RewardPoint.from_response(
        [
            {
                "category_id": int(CATEGORY_ID),
                "current_point_balance": 12,
                "lifetime_points_earned": 30,
            }
        ]
    )


@pytest.fixture
def mock_client(
    frames: list[Frame],
    categories: list[Category],
    chores: list[Chore],
    reward_points: list[RewardPoint],
) -> Generator[AsyncMock]:
    """Patch the pyskylight client used by the integration."""
    user = User.from_response({"id": USER_ID, "email": EMAIL, "profile": {"name": "Alex"}})
    with (
        patch("custom_components.skylight.Skylight", autospec=True) as init_client,
        patch("custom_components.skylight.config_flow.Skylight", autospec=True) as flow_client,
    ):
        client = init_client.return_value
        client.get_user.return_value = user
        client.get_frames.return_value = frames
        client.get_categories.return_value = categories
        client.get_chores.return_value = chores
        client.get_reward_points.return_value = reward_points
        flow_client.return_value = client
        yield client


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A config entry matching what the flow would create."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=USER_ID,
        data={CONF_USERNAME: EMAIL, CONF_PASSWORD: PASSWORD},
    )


async def setup_integration(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Add a config entry and wait for it to finish setting up."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
