"""Tests for the Skylight calendar platform."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.calendar import DOMAIN as CALENDAR_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pyskylight.exceptions import ApiError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.skylight.calendar import SkylightCalendarEntity

from .conftest import FRAME_ID, setup_integration

CALENDAR = "calendar.kitchen_calendar"

# Between the finished standup and the dentist appointment.
NOW = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)


def _entity(hass: HomeAssistant) -> SkylightCalendarEntity:
    component = hass.data["entity_components"][CALENDAR_DOMAIN]
    entity = component.get_entity(CALENDAR)
    assert isinstance(entity, SkylightCalendarEntity)
    return entity


@pytest.fixture(autouse=True)
def _fixed_now(freezer: FrozenDateTimeFactory) -> None:
    """Pin the clock so "current or next event" is deterministic."""
    freezer.move_to(NOW)


async def test_all_entities(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """One calendar per frame, pinned to a snapshot."""
    with patch("custom_components.skylight.PLATFORMS", [Platform.CALENDAR]):
        await setup_integration(hass, mock_config_entry)

    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


async def test_next_event_is_reported(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """State is the next event that has not finished, ignoring past ones."""
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(CALENDAR)
    assert state.state == "off"  # nothing in progress at noon
    assert state.attributes["message"] == "Dentist"
    assert state.attributes["location"] == "Main St"
    assert state.attributes["description"] == "Bring the form"


async def test_event_in_progress_turns_on(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A calendar is "on" while an event is running."""
    freezer.move_to(datetime(2026, 8, 7, 14, 30, tzinfo=timezone.utc))
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(CALENDAR)
    assert state.state == "on"
    assert state.attributes["message"] == "Dentist"


async def test_no_upcoming_events(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """An empty calendar is off with no event attributes."""
    mock_client.get_calendar_events.return_value = []
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(CALENDAR)
    assert state.state == "off"
    assert "message" not in state.attributes


async def test_get_events_queries_the_api(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The calendar panel's range goes to the API, not the polled window."""
    await setup_integration(hass, mock_config_entry)
    entity = _entity(hass)
    mock_client.get_calendar_events.reset_mock()

    events = await entity.async_get_events(
        hass,
        datetime(2026, 9, 1, tzinfo=timezone.utc),
        datetime(2026, 9, 30, tzinfo=timezone.utc),
    )

    mock_client.get_calendar_events.assert_awaited_once_with(
        FRAME_ID, date(2026, 9, 1), date(2026, 9, 30), timezone="America/New_York"
    )
    assert [event.summary for event in events] == ["Dentist", "Camping", "Standup"]


async def test_all_day_events_use_dates(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """All-day events become plain dates with an exclusive end."""
    await setup_integration(hass, mock_config_entry)
    events = await _entity(hass).async_get_events(
        hass,
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 30, tzinfo=timezone.utc),
    )

    camping = next(event for event in events if event.summary == "Camping")
    assert camping.start == date(2026, 8, 9)
    assert camping.end == date(2026, 8, 11)
    assert not isinstance(camping.start, datetime)

    dentist = next(event for event in events if event.summary == "Dentist")
    assert isinstance(dentist.start, datetime)


@pytest.mark.parametrize(
    ("attributes", "expected_start", "expected_end"),
    [
        # An all-day event that ends the day it starts still has to span a day.
        (
            {
                "starts_at": "2026-08-09T00:00:00+00:00",
                "ends_at": "2026-08-09T00:00:00+00:00",
                "all_day": True,
            },
            date(2026, 8, 9),
            date(2026, 8, 10),
        ),
        # A timed event with a zero duration gets a minute so it is orderable.
        (
            {
                "starts_at": "2026-08-09T10:00:00+00:00",
                "ends_at": "2026-08-09T10:00:00+00:00",
                "all_day": False,
            },
            datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 9, 10, 1, tzinfo=timezone.utc),
        ),
    ],
)
async def test_degenerate_ranges_are_normalized(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    attributes: dict,
    expected_start: object,
    expected_end: object,
) -> None:
    """An end at or before the start would be rejected by Home Assistant."""
    from pyskylight.models import CalendarEvent as SkylightEvent

    mock_client.get_calendar_events.return_value = [
        SkylightEvent.from_resource(
            {"type": "calendar_event", "id": "x", "attributes": {"summary": "Odd", **attributes}}
        )
    ]
    await setup_integration(hass, mock_config_entry)

    (event,) = await _entity(hass).async_get_events(
        hass,
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 30, tzinfo=timezone.utc),
    )
    assert event.start == expected_start
    assert event.end == expected_end


async def test_events_without_times_are_skipped(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """One malformed event must not take out the whole calendar."""
    from pyskylight.models import CalendarEvent as SkylightEvent

    mock_client.get_calendar_events.return_value = [
        SkylightEvent.from_resource(
            {"type": "calendar_event", "id": "bad", "attributes": {"summary": "No times"}}
        ),
        SkylightEvent.from_resource(
            {
                "type": "calendar_event",
                "id": "good",
                "attributes": {
                    "summary": "Fine",
                    "starts_at": "2026-08-09T10:00:00+00:00",
                    "ends_at": "2026-08-09T11:00:00+00:00",
                },
            }
        ),
    ]
    await setup_integration(hass, mock_config_entry)

    events = await _entity(hass).async_get_events(
        hass,
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 30, tzinfo=timezone.utc),
    )
    assert [event.summary for event in events] == ["Fine"]
    assert hass.states.get(CALENDAR).attributes["message"] == "Fine"


async def test_create_event(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Creating an event sends the frame's timezone along with the times."""
    await setup_integration(hass, mock_config_entry)
    mock_client.get_calendar_events.reset_mock()

    await hass.services.async_call(
        CALENDAR_DOMAIN,
        "create_event",
        {
            ATTR_ENTITY_ID: CALENDAR,
            "summary": "Piano lesson",
            "start_date_time": "2026-08-12 16:00:00",
            "end_date_time": "2026-08-12 17:00:00",
        },
        blocking=True,
    )

    kwargs = mock_client.create_calendar_event.await_args.kwargs
    assert kwargs["summary"] == "Piano lesson"
    assert kwargs["all_day"] is False
    assert kwargs["timezone"] == "America/New_York"
    assert kwargs["starts_at"].startswith("2026-08-12T16:00:00")
    # The write is followed by a refresh.
    assert mock_client.get_calendar_events.await_count == 1


async def test_create_all_day_event(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A date-only event is flagged all-day."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        CALENDAR_DOMAIN,
        "create_event",
        {
            ATTR_ENTITY_ID: CALENDAR,
            "summary": "Holiday",
            "start_date": "2026-08-12",
            "end_date": "2026-08-14",
        },
        blocking=True,
    )

    kwargs = mock_client.create_calendar_event.await_args.kwargs
    assert kwargs["all_day"] is True
    assert kwargs["starts_at"] == "2026-08-12"


async def test_delete_event(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Deleting an event calls the API with its uid."""
    await setup_integration(hass, mock_config_entry)

    await _entity(hass).async_delete_event("e1")

    mock_client.delete_calendar_event.assert_awaited_once_with(FRAME_ID, "e1")


async def test_write_errors_surface(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A failed write raises rather than silently doing nothing."""
    await setup_integration(hass, mock_config_entry)
    mock_client.delete_calendar_event.side_effect = ApiError(500, "boom")

    with pytest.raises(HomeAssistantError, match="Could not delete the Skylight event"):
        await _entity(hass).async_delete_event("e1")


async def test_get_events_error_surfaces(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A failed range query is an error, not an empty calendar."""
    await setup_integration(hass, mock_config_entry)
    mock_client.get_calendar_events.side_effect = ApiError(500, "boom")

    with pytest.raises(HomeAssistantError, match="Could not load Skylight calendar"):
        await _entity(hass).async_get_events(hass, NOW, NOW + timedelta(days=1))


async def test_falls_back_to_ha_timezone(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    frames: list,
) -> None:
    """A frame with no timezone uses Home Assistant's, since the API demands one."""
    from dataclasses import replace

    mock_client.get_frames.return_value = [replace(frames[0], timezone=None)]
    await setup_integration(hass, mock_config_entry)
    mock_client.get_calendar_events.reset_mock()

    await _entity(hass).async_get_events(hass, NOW, NOW + timedelta(days=1))

    assert mock_client.get_calendar_events.await_args.kwargs["timezone"] == "US/Pacific"
