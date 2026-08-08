"""Calendar platform for the Skylight integration.

One calendar per frame, showing what the frame itself shows: every event across
the calendars synced into that household.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.components.calendar.const import CalendarEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
from pyskylight.exceptions import SkylightError
from pyskylight.models import CalendarEvent as SkylightCalendarEvent

from .const import DOMAIN
from .coordinator import SkylightConfigEntry, SkylightDataUpdateCoordinator
from .entity import SkylightEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkylightConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Skylight calendars from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        SkylightCalendarEntity(coordinator, frame_id) for frame_id in coordinator.data
    )


def _to_calendar_event(event: SkylightCalendarEvent) -> CalendarEvent | None:
    """Convert a Skylight event to a Home Assistant one.

    Returns None for an event with no usable start or end, rather than letting a
    malformed event break the whole calendar.
    """
    start, end = event.starts_at, event.ends_at
    if start is None or end is None:
        return None

    if event.all_day:
        # Home Assistant wants plain dates for all-day events, with an exclusive
        # end. Skylight is not consistent about whether the end is the last day
        # or the day after, so normalize anything degenerate to one day.
        start_date, end_date = start.date(), end.date()
        if end_date <= start_date:
            end_date = start_date + timedelta(days=1)
        return CalendarEvent(
            start=start_date,
            end=end_date,
            summary=event.summary or "",
            description=event.description,
            location=event.location,
            uid=event.id,
        )

    return CalendarEvent(
        start=start,
        end=end if end > start else start + timedelta(minutes=1),
        summary=event.summary or "",
        description=event.description,
        location=event.location,
        uid=event.id,
    )


class SkylightCalendarEntity(SkylightEntity, CalendarEntity):
    """The combined calendar for one Skylight frame."""

    _attr_translation_key = "calendar"
    _attr_supported_features = (
        CalendarEntityFeature.CREATE_EVENT | CalendarEntityFeature.DELETE_EVENT
    )

    def __init__(self, coordinator: SkylightDataUpdateCoordinator, frame_id: str) -> None:
        """Initialize the calendar."""
        super().__init__(coordinator, frame_id)
        self._attr_unique_id = f"{frame_id}_calendar"

    @property
    def _timezone(self) -> str:
        """The frame's timezone, which the events endpoint requires."""
        frame_data = self.frame_data_or_none
        timezone = frame_data.frame.timezone if frame_data else None
        return timezone or str(dt_util.DEFAULT_TIME_ZONE)

    @property
    def event(self) -> CalendarEvent | None:
        """Return the event in progress, or the next one to start.

        Guarded because Home Assistant reads this while writing state, before it
        checks whether the entity is available — so a frame that dropped out of
        a refresh reached it and raised.
        """
        frame_data = self.frame_data_or_none
        if frame_data is None:
            return None

        now = dt_util.now()
        upcoming: list[CalendarEvent] = []
        for skylight_event in frame_data.calendar_events:
            event = _to_calendar_event(skylight_event)
            if event is None:
                continue
            # All-day events compare as dates; normalize before sorting.
            end = (
                dt_util.start_of_local_day(event.end)
                if not isinstance(event.end, datetime)
                else event.end
            )
            if end > now:
                upcoming.append(event)

        if not upcoming:
            return None
        return min(upcoming, key=lambda item: dt_util.as_utc(_as_datetime(item.start)))

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return events in a range.

        Home Assistant asks for arbitrary ranges when someone opens the calendar
        panel, so this queries the API directly rather than using the polled
        window.
        """
        try:
            events = await self.coordinator.client.get_calendar_events(
                self._frame_id,
                start_date.date(),
                end_date.date(),
                timezone=self._timezone,
            )
        except SkylightError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="get_events_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        return [
            event
            for event in (_to_calendar_event(skylight_event) for skylight_event in events)
            if event is not None
        ]

    async def async_create_event(self, **kwargs: Any) -> None:
        """Create an event on the frame's default calendar."""
        start: date | datetime = kwargs["dtstart"]
        end: date | datetime = kwargs["dtend"]
        fields: dict[str, Any] = {
            "summary": kwargs.get("summary"),
            "description": kwargs.get("description"),
            "location": kwargs.get("location"),
            "starts_at": start.isoformat(),
            "ends_at": end.isoformat(),
            "all_day": not isinstance(start, datetime),
            "timezone": self._timezone,
        }
        await self.async_write(
            "create_event_failed",
            self.coordinator.client.create_calendar_event(
                self._frame_id, **{k: v for k, v in fields.items() if v is not None}
            ),
        )

    async def async_delete_event(
        self,
        uid: str,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """Delete an event."""
        await self.async_write(
            "delete_event_failed",
            self.coordinator.client.delete_calendar_event(self._frame_id, uid),
        )


def _as_datetime(value: date | datetime) -> datetime:
    """Coerce a date or datetime to a comparable local datetime."""
    if isinstance(value, datetime):
        return value
    return dt_util.start_of_local_day(value)
