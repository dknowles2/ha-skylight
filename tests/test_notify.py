"""Tests for nudges: making the frame speak to one family member."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.notify import ATTR_MESSAGE, ATTR_TITLE, SERVICE_SEND_MESSAGE
from homeassistant.components.notify import DOMAIN as NOTIFY_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pyskylight.exceptions import ApiError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import CATEGORY_ID, FRAME_ID, async_poll, setup_integration

ALEX = "notify.kitchen_alex_nudge"
SAM = "notify.kitchen_sam_nudge"


async def send(hass: HomeAssistant, entity_id: str, message: str, **extra: str) -> None:
    """Call notify.send_message on one of the frame's profiles."""
    await hass.services.async_call(
        NOTIFY_DOMAIN,
        SERVICE_SEND_MESSAGE,
        {ATTR_ENTITY_ID: entity_id, ATTR_MESSAGE: message, **extra},
        blocking=True,
    )


async def test_one_target_per_profile(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A nudge is addressed to a person, so the calendar buckets get nothing."""
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(ALEX) is not None
    assert hass.states.get(SAM) is not None
    assert hass.states.get("notify.kitchen_family_birthdays_nudge") is None


async def test_sending_speaks_to_that_profile(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Delivery is now: Home Assistant already knows how to schedule later."""
    freezer.move_to("2026-08-08 22:00:00+00:00")
    await setup_integration(hass, mock_config_entry)

    await send(hass, ALEX, "The bus is in five minutes")

    mock_client.create_nudge.assert_awaited_once_with(
        FRAME_ID,
        body="The bus is in five minutes",
        deliver_at=datetime(2026, 8, 8, 22, 0, tzinfo=UTC),
        category_ids=[CATEGORY_ID],
    )


async def test_a_title_is_dropped(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The frame speaks the body; a heading has nowhere to go."""
    await setup_integration(hass, mock_config_entry)

    await send(hass, ALEX, "Dinner", **{ATTR_TITLE: "Kitchen"})

    assert mock_client.create_nudge.await_args.kwargs["body"] == "Dinner"


async def test_a_refused_nudge_surfaces(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A write that failed must not look like one that worked."""
    await setup_integration(hass, mock_config_entry)
    mock_client.create_nudge.side_effect = ApiError(422, "Body can't be blank")

    with pytest.raises(HomeAssistantError, match="Could not send the Skylight nudge"):
        await send(hass, ALEX, "")


async def test_sending_does_not_force_a_poll(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Nudges are not in the snapshot, so refreshing after one is wasted work."""
    await setup_integration(hass, mock_config_entry)
    mock_client.get_frames.reset_mock()

    await send(hass, ALEX, "Bedtime")

    mock_client.get_frames.assert_not_awaited()


async def test_a_profile_that_left_the_frame(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    categories: list,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Deleting a profile leaves nobody to speak to."""
    await setup_integration(hass, mock_config_entry)

    mock_client.get_categories.return_value = categories[1:]
    await async_poll(hass, freezer)

    assert hass.states.get(ALEX).state == STATE_UNAVAILABLE


async def test_a_frame_that_dropped_out(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The whole frame failing takes its nudge targets with it."""
    await setup_integration(hass, mock_config_entry)

    mock_client.get_frames.return_value = []
    await async_poll(hass, freezer)

    assert hass.states.get(ALEX).state == STATE_UNAVAILABLE
