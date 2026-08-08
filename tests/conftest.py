"""Fixtures for the Skylight integration tests."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pyskylight.models import (
    CalendarEvent,
    Category,
    Chore,
    ChoreGroups,
    Device,
    Frame,
    ListItem,
    Recipe,
    Reward,
    RewardPoint,
    SkylightList,
    User,
)
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.skylight.const import DOMAIN, SCAN_INTERVAL

USER_ID = "12345"
EMAIL = "user@example.com"
PASSWORD = "hunter2"
FRAME_ID = "5455113"
CATEGORY_ID = "77"
OTHER_CATEGORY_ID = "78"
# A category that is a shared calendar rather than a person.
BUCKET_CATEGORY_ID = "79"
DEVICE_ID = "5759923"
BUDDY_ID = "5759925"
LIST_ID = "7248050"
OTHER_LIST_ID = "7248051"


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Register entities that ship disabled, so a snapshot can cover them.

    Home Assistant core has a fixture of this name; the custom-component test
    harness does not, so it is reimplemented here.
    """
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        new_callable=PropertyMock,
        return_value=True,
    ):
        yield


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
        "hardware_model": "15-CAL-2.0",
        **overrides,
    }
    return Frame.from_resource({"type": "frame", "id": FRAME_ID, "attributes": attributes})


def _category(category_id: str, label: str, *, linked_to_profile: bool = True) -> Category:
    """Build a category.

    `linked_to_profile` is what separates a person from a calendar bucket like
    "Family Birthdays", so it defaults to a real person and the odd ones are
    built explicitly.
    """
    return Category.from_resource(
        {
            "type": "category",
            "id": category_id,
            "attributes": {
                "id": int(category_id),
                "label": label,
                "color": "#00526D",
                "linked_to_profile": linked_to_profile,
            },
            "relationships": {
                "family_member": {
                    "data": {"type": "family_member", "id": "7"} if linked_to_profile else None
                }
            },
        }
    )


def _chore(
    chore_id: str,
    summary: str,
    category_id: str | None,
    *,
    completed: bool,
    up_for_grabs: bool = False,
) -> Chore:
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
                "up_for_grabs": up_for_grabs,
            },
            "relationships": {
                "category": {
                    "data": (
                        {"type": "category", "id": category_id} if category_id is not None else None
                    )
                }
            },
        }
    )


def _list_item(item_id: str, label: str, *, completed: bool = False) -> ListItem:
    return ListItem.from_resource(
        {
            "type": "list_item",
            "id": item_id,
            "attributes": {
                "label": label,
                "status": "completed" if completed else "pending",
                "created_at": "2026-08-07T09:00:00Z",
            },
        }
    )


def _list(list_id: str, label: str, kind: str, items: list[ListItem]) -> SkylightList:
    skylight_list = SkylightList.from_resource(
        {
            "type": "list",
            "id": list_id,
            "attributes": {
                "label": label,
                "kind": kind,
                "color": "#00526D",
                "default_grocery_list": kind == "shopping",
            },
            "relationships": {
                "list_items": {"data": [{"type": "list_item", "id": i.id} for i in items]}
            },
        }
    )
    # get_list() resolves the items; from_resource() alone cannot.
    return replace(skylight_list, items=items)


SECOND_FRAME_ID = "5594280"


@pytest.fixture
def frames() -> list[Frame]:
    """The frames the fake account owns."""
    return [_frame()]


@pytest.fixture
def two_frames() -> list[Frame]:
    """An account with a second frame, to exercise per-frame isolation."""
    second = Frame.from_resource(
        {
            "type": "frame",
            "id": SECOND_FRAME_ID,
            "attributes": {"name": "Playroom", "timezone": "America/New_York"},
        }
    )
    return [_frame(), second]


@pytest.fixture
def categories() -> list[Category]:
    """Two family profiles, plus a calendar bucket that is not a person."""
    return [
        _category(CATEGORY_ID, "Alex"),
        _category(OTHER_CATEGORY_ID, "Sam"),
        _category(BUCKET_CATEGORY_ID, "Family Birthdays", linked_to_profile=False),
    ]


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


def _event(
    event_id: str,
    summary: str,
    starts_at: str,
    ends_at: str,
    *,
    all_day: bool = False,
    **extra: Any,
) -> CalendarEvent:
    return CalendarEvent.from_resource(
        {
            "type": "calendar_event",
            "id": event_id,
            "attributes": {
                "summary": summary,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "all_day": all_day,
                "status": "confirmed",
                "kind": "event",
                **extra,
            },
        }
    )


@pytest.fixture
def devices() -> list[Device]:
    """The physical device attached to the frame, as the real API reports it."""
    return [
        Device.from_resource(
            {
                "type": "device",
                "id": DEVICE_ID,
                "attributes": {
                    "name": "Kitchen Calendar",
                    "activated": True,
                    "role": None,
                    "timezone": "America/New_York",
                    # Duplicated by the frame; deliberately not exposed here.
                    "brightness": 255,
                    "sleeps_at": "23:00",
                    "wakes_at": "06:00",
                    "currently_sleeping": False,
                    # Duplicated by the frame; deliberately not exposed as
                    # frame entities, but writable on the device.
                    "slideshow_speed": 10,
                    "slideshow_style": 0,
                    "show_caption": True,
                    "blur_effect": True,
                    "side_by_side": False,
                    "show_heart": False,
                    # Device-only.
                    "nightlight": False,
                    "nightlight_brightness": 65,
                    "nightlight_color": "off",
                    "sleep_mode": "screen_off",
                    "sleep_mode_on": True,
                    "sleep_sound": None,
                    "sleep_sound_volume": 70,
                    "current_album_id": -1,
                },
            }
        )
    ]


@pytest.fixture
def buddy() -> Device:
    """A Skylight Buddy, which a calendar display is not.

    `role` is the whole difference, and it is the one Skylight's own app uses:
    its `deviceUtils.isBuddy` is `device.attributes.role === 'buddy'`. The
    nightlight and sleep sound settings only mean anything on hardware that
    reports this.
    """
    return Device.from_resource(
        {
            "type": "device",
            "id": BUDDY_ID,
            "attributes": {
                "name": "Bedside Buddy",
                "activated": True,
                "role": "buddy",
                "timezone": "America/New_York",
                "brightness": 200,
                "sleeps_at": "20:00",
                "wakes_at": "07:00",
                "currently_sleeping": False,
                "nightlight": False,
                "nightlight_brightness": 65,
                "nightlight_color": "off",
                "sleep_mode": "screen_off",
                "sleep_mode_on": True,
                "sleep_sound": None,
                "sleep_sound_volume": 70,
            },
        }
    )


@pytest.fixture
def calendar_events() -> list[CalendarEvent]:
    """A timed event, an all-day event, and one already finished."""
    return [
        _event(
            "e1",
            "Dentist",
            "2026-08-07T14:00:00+00:00",
            "2026-08-07T15:00:00+00:00",
            location="Main St",
            description="Bring the form",
        ),
        _event(
            "e2", "Camping", "2026-08-09T00:00:00+00:00", "2026-08-11T00:00:00+00:00", all_day=True
        ),
        _event("e3", "Standup", "2026-08-07T09:00:00+00:00", "2026-08-07T09:15:00+00:00"),
    ]


@pytest.fixture
def lists() -> list[SkylightList]:
    """A grocery list with items, and an empty to-do list."""
    return [
        _list(
            LIST_ID,
            "Grocery List",
            "shopping",
            [
                _list_item("101", "Milk"),
                _list_item("102", "Eggs", completed=True),
                _list_item("103", "Bread"),
            ],
        ),
        _list(OTHER_LIST_ID, "To Do", "to_do", []),
    ]


def _recipe(recipe_id: str, summary: str, ingredients: str) -> Recipe:
    """Build a recipe.

    A recipe's name is `summary`, and its ingredients are free text inside
    `description` — there is no structured ingredient list on the resource.
    """
    return Recipe.from_resource(
        {
            "type": "meal_recipe",
            "id": recipe_id,
            "attributes": {
                "summary": summary,
                "description": f"Ingredients:\n{ingredients}\n\nInstructions:\n1. Cook.\n",
                "draft": False,
            },
            "relationships": {"meal_category": {"data": {"type": "meal_category", "id": "300"}}},
        }
    )


@pytest.fixture
def recipes() -> list[Recipe]:
    """Two recipes on the frame's meal planner."""
    return [
        _recipe("500", "Taco Night", "- Tortillas\n- Ground beef"),
        _recipe("501", "Pancakes", "- Flour\n- Eggs"),
    ]


@pytest.fixture
def unassigned_chores() -> ChoreGroups:
    """The "Up for Grabs" response: chores nobody owns, bucketed by urgency.

    `future` is deliberately populated too, so a test can prove it is left out.
    """
    return ChoreGroups(
        chores={
            "late": [_chore("10", "Put away laundry", None, completed=False, up_for_grabs=True)],
            "today": [_chore("11", "Vacuum", None, completed=False, up_for_grabs=True)],
            "today_timed": [
                _chore("12", "Unload dishwasher", None, completed=True, up_for_grabs=True)
            ],
            "any_day": [_chore("13", "Water plants", None, completed=False, up_for_grabs=True)],
            "future": [_chore("14", "Change sheets", None, completed=False, up_for_grabs=True)],
        },
        routines={},
    )


def _reward(
    reward_id: str,
    name: str,
    point_value: int,
    category_id: str,
    *,
    redeemed_at: str | None = None,
) -> Reward:
    return Reward.from_resource(
        {
            "type": "reward",
            "id": reward_id,
            "attributes": {
                "name": name,
                "point_value": point_value,
                "redeemed_at": redeemed_at,
                "respawn_on_redemption": True,
                "origin": "user",
            },
            "relationships": {"category": {"data": {"type": "category", "id": category_id}}},
        }
    )


@pytest.fixture
def rewards() -> list[Reward]:
    """Two rewards for Alex, one of them already redeemed, and one for Sam."""
    return [
        _reward("900", "Extra screen time", 5, CATEGORY_ID),
        _reward("901", "Pizza night", 25, CATEGORY_ID, redeemed_at="2026-08-07T18:00:00Z"),
        _reward("902", "New book", 10, OTHER_CATEGORY_ID),
    ]


@pytest.fixture
def mock_client(
    frames: list[Frame],
    categories: list[Category],
    chores: list[Chore],
    unassigned_chores: ChoreGroups,
    rewards: list[Reward],
    reward_points: list[RewardPoint],
    lists: list[SkylightList],
    recipes: list[Recipe],
    calendar_events: list[CalendarEvent],
    devices: list[Device],
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
        client.get_all_chores.return_value = unassigned_chores
        client.get_rewards.return_value = rewards
        client.get_reward_points.return_value = reward_points
        client.get_lists.return_value = lists
        client.get_meal_recipes.return_value = recipes
        client.get_calendar_events.return_value = calendar_events
        client.get_devices.return_value = devices
        # The real API echoes the updated device; entities rely on that.
        client.update_device.return_value = devices[0]
        # Only GET /api/frames/{id} carries hardware_model.
        client.get_frame.side_effect = lambda frame_id: next(
            (frame for frame in frames if frame.id == str(frame_id)), frames[0]
        )
        client.get_list.side_effect = lambda _frame, list_id: next(
            (item for item in lists if item.id == str(list_id)), lists[0]
        )
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


async def async_poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Advance to the next poll and let it finish.

    Draining twice is deliberate: the coordinator fetches frames concurrently,
    and `asyncio.gather` schedules its children outside Home Assistant's task
    tracking, so a single drain can return while they are still in flight.
    """
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    await hass.async_block_till_done()
