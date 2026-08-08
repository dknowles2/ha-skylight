# Architecture

How this integration is put together, and why. The target is Home Assistant's
core-integration standard, so the shape here should be recognisable to anyone who has read
a core integration — and anything that departs from that is explained below.

## Layers

```
pyskylight            HTTP, auth, JSON:API decoding, typed models
  └── coordinator.py  one poll → a snapshot of every frame
        └── entity.py device identity shared by all platforms
              └── calendar.py, sensor.py, todo.py, ... per platform
```

The integration contains no HTTP code. Anything about the Skylight API — endpoints,
request shapes, quirks — belongs in `pyskylight`, which is versioned and pinned in
`manifest.json`. If a fix is needed at the protocol level, it happens there and this repo
bumps the requirement.

## Config entry

`async_setup_entry` builds an authenticated client on Home Assistant's shared aiohttp
session, does a first refresh, and stores the coordinator on `entry.runtime_data`. The
entry is typed:

```python
type SkylightConfigEntry = ConfigEntry[SkylightDataUpdateCoordinator]
```

so platforms get a checked `entry.runtime_data` with no casting and no `hass.data` bucket
to keep in sync.

There is nothing to close on unload: the session belongs to Home Assistant, and the client
and coordinator die with the entry.

## Coordinator

One `DataUpdateCoordinator` polls every frame on the account and returns
`dict[frame_id, FrameData]`. Entities read from that snapshot synchronously — no entity
ever awaits.

`FrameData` carries the frame plus its profiles, today's chores, and reward balances,
with small accessors (`chores_for`, `points_for`) so platform code does not re-implement
the same filtering.

Two failure modes are deliberately distinct:

- `AuthenticationError` / `NotAuthorizedError` → `ConfigEntryAuthFailed`, which starts the
  reauth flow. Retrying with credentials that no longer work would never succeed.
- Any other `SkylightError` → `UpdateFailed`, which retries with backoff and marks
  entities unavailable.

The poll interval is one minute. Chores and calendar events change on human timescales,
and this is an API we do not own; a minute is responsive without being rude.

## Entities

`SkylightEntity` maps **one frame to one Home Assistant device**. Family profiles are not
devices — they are people, and a person is not a thing Home Assistant manages — so
profile-scoped entities attach to the frame's device and carry the profile in their name
via a translation placeholder:

```json
"chores_due": { "name": "{profile} chores due" }
```

That keeps names translatable, which prefixing the string in Python would not.

Unique ids are `{frame_id}_{category_id}_{key}`, scoped by all three so that adding a
platform or a profile can never collide with an existing entity.

Availability is layered: the coordinator's own success, then whether the frame is still
present, then whether the profile is. A profile removed from the frame leaves its entities
`unavailable` rather than silently reporting a stale number.

## Writes

Read paths go through the coordinator; write paths call the client directly and then
`async_request_refresh()`, so the UI reflects the change without waiting out the poll
interval. `SkylightEntity.async_write()` does both, and turns a `SkylightError` into a
`HomeAssistantError` carrying a translated message. A failed write must be visible —
silently doing nothing is the worst outcome for something the user just clicked.

Where the API offers both a bulk and a single-item endpoint, this integration uses
whichever was verified against the live API. Deleting to-do items goes one at a time for
that reason.

Home Assistant and Skylight disagree about how ordering works: Home Assistant moves an
item "after this other one", Skylight takes a position index. `todo.py` translates between
them using the ordering from the last poll.

## Calendar

One calendar entity per frame, matching what the frame itself displays: every event across
the household's synced calendars, rather than one entity per source calendar.

The coordinator polls only a two-week window, purely so the entity can answer "what is on
now, or next". Home Assistant asks for arbitrary ranges when someone opens the calendar
panel, and `async_get_events` queries the API directly for those — caching a window would
only be wrong at the edges.

Skylight's events need normalizing before Home Assistant will take them: all-day events
become plain dates with an exclusive end, and any event whose end is at or before its start
is widened, because Home Assistant rejects a non-positive range. An event with no usable
times is skipped rather than allowed to break the whole calendar.

## Diagnostics

`diagnostics.py` dumps the raw API payloads, because the models keep their raw resource.
Names, emails, birthdays, chore summaries, and `share_token` (a working invite to the
household) are redacted. A test asserts the password and email never appear in the output —
redaction is the sort of thing that quietly breaks when a field is added upstream.

## Testing

Tests patch `pyskylight`'s `Skylight` class with an autospec'd mock, so the fake client
cannot drift from the real signature without a test failure. Fixtures build real model
objects from real API-shaped payloads rather than mocks, so the decoding logic is exercised
too.

Snapshot tests pin the entity registry and the diagnostics output. They exist to make
unintended changes visible in review — regenerate them deliberately, and read the diff.

Coverage floor is 95%, currently 100%.

## Conventions worth keeping

- No `hass.data`; use `entry.runtime_data`.
- No blocking or network I/O outside the coordinator.
- Every user-visible string lives in `strings.json`, mirrored to `translations/en.json`
  (a pre-commit hook enforces the mirror).
- Entity names come from `translation_key`. The exception is an entity named after
  something the user named — a to-do list — where the name is data, not a string to
  translate.
- New platforms subclass `SkylightEntity` so device identity and availability stay in one
  place.
