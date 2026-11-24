"""Calendar platform for the Skylight integration.

One calendar per frame, showing what the frame itself shows: every event across
the calendars synced into that household.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.components.calendar.const import CalendarEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
from pyskylight.models import CalendarEvent as SkylightCalendarEvent

from .const import DOMAIN
from .coordinator import SkylightConfigEntry, SkylightDataUpdateCoordinator
from .entity import SkylightEntity
from .errors import SKYLIGHT_CONNECTION_ERRORS, describe


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


def _all_day_date(value: datetime, zone: tzinfo) -> date:
    """Return the calendar day an all-day boundary stands for.

    An all-day event is a date, but Skylight sends an instant, and resolves it
    with a timezone (the events endpoint takes one "to resolve all-day
    boundaries"). How that instant is written has never been captured, and the
    obvious `.date()` is only right for some of the ways it could be:

    - midnight UTC as a floating date (`2026-08-12T00:00:00Z`);
    - local midnight with its offset (`2026-08-12T00:00:00+02:00`);
    - local midnight converted to UTC (`2026-08-11T22:00:00Z` for Berlin).

    `.date()` handles the first two and puts the third on the day before: every
    all-day event a day early for a frame east of UTC. So a value that is
    already midnight in its own offset is read as it stands, and anything else
    is put in the timezone it was resolved with. If that turns it into local midnight,
    the conversion has explained it and its date is the one meant. If it does
    not, the encoding is something else again, and the value is read as it
    stands rather than moved on a guess. A naive value has no offset to
    convert from, and is read as it stands too.
    """
    if value.tzinfo is None or value.time() == time.min:
        return value.date()
    local = value.astimezone(zone)
    return local.date() if local.time() == time.min else value.date()


def _resolving_zone(frame_timezone: str | None, event_timezone: str | None) -> tzinfo:
    """Return the timezone Skylight resolved an all-day event's boundaries in.

    The frame's comes first, because it is the one sent with every events
    request, and that parameter is what the endpoint uses "to resolve all-day
    boundaries". The event's own `timezone` is undocumented and may be wherever
    a synced event was created, so it is only a fallback, for a frame whose
    zone is missing or not one Home Assistant recognises. Home Assistant's own
    zone is the last resort, and the same one sent for a frame without a zone.
    """
    for name in (frame_timezone, event_timezone):
        if name and (zone := dt_util.get_time_zone(name)) is not None:
            return zone
    return dt_util.DEFAULT_TIME_ZONE


def _to_calendar_event(
    event: SkylightCalendarEvent, timezone: str | None = None
) -> CalendarEvent | None:
    """Convert a Skylight event to a Home Assistant one.

    `timezone` is the frame's, the one sent with the events request, which
    Skylight resolves all-day boundaries in.

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
        zone = _resolving_zone(timezone, event.timezone)
        start_date, end_date = _all_day_date(start, zone), _all_day_date(end, zone)
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
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
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
            event = _to_calendar_event(skylight_event, self._timezone)
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
        except SKYLIGHT_CONNECTION_ERRORS as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="get_events_failed",
                translation_placeholders={"error": describe(err)},
            ) from err

        return [
            event
            for event in (
                _to_calendar_event(skylight_event, self._timezone) for skylight_event in events
            )
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

    async def async_update_event(
        self,
        uid: str,
        event: dict[str, Any],
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """Change an event on the frame.

        A single occurrence cannot be changed on its own. Skylight's `PUT`
        always rewrites the whole series — `apply_to: "this"` is a 422, and
        `"all"` is the only value it takes — so editing next Tuesday would
        quietly change every Tuesday. That is refused instead.

        The `PUT` is a partial update — fields left out keep their values,
        verified on a test frame — but Home Assistant hands over the whole
        event, so everything it knows about is sent.
        """
        if _is_recurring(uid, recurrence_id, recurrence_range):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="recurring_event_not_editable",
            )

        start: date | datetime = event["dtstart"]
        end: date | datetime = event["dtend"]
        fields: dict[str, Any] = {
            "summary": event.get("summary"),
            "description": event.get("description"),
            "location": event.get("location"),
            "starts_at": start.isoformat(),
            "ends_at": end.isoformat(),
            "all_day": not isinstance(start, datetime),
            "timezone": self._timezone,
        }
        await self.async_write(
            "update_event_failed",
            self.coordinator.client.update_calendar_event(
                self._frame_id, uid, **{k: v for k, v in fields.items() if v is not None}
            ),
        )

    async def async_delete_event(
        self,
        uid: str,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """Delete an event.

        A repeating event is refused, for the same reason an edit to one is.
        Home Assistant only ever asks to delete one occurrence, or that one and
        every later one. Skylight's `DELETE` takes no way to say which, and
        what it does with an occurrence's id has never been verified. If it
        behaves like the `PUT`, deleting one Tuesday would wipe every Tuesday,
        months of a family's calendar gone from a click meant for one day.
        Refusing costs a trip to the frame; guessing wrong costs the series.
        """
        if _is_recurring(uid, recurrence_id, recurrence_range):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="recurring_event_not_deletable",
            )
        await self.async_write(
            "delete_event_failed",
            self.coordinator.client.delete_calendar_event(self._frame_id, uid),
        )


def _is_recurring(uid: str, recurrence_id: str | None, recurrence_range: str | None) -> bool:
    """Whether a write targets an occurrence of a repeating event.

    Recognised by the id: a one-off event's id is a plain number, while an
    occurrence carries a `-<timestamp>` suffix. Home Assistant's own
    `recurrence_id` and `recurrence_range` count too when it sends them, but
    the id is what catches the common case, since each occurrence reaches
    Home Assistant as its own event.
    """
    return bool(recurrence_id or recurrence_range or "-" in uid)


def _as_datetime(value: date | datetime) -> datetime:
    """Coerce a date or datetime to a comparable local datetime."""
    if isinstance(value, datetime):
        return value
    return dt_util.start_of_local_day(value)
