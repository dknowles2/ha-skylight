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
  reauth flow. Retrying with credentials that no longer work would never succeed. This is
  account-level, so it propagates even when only one frame reported it.
- Any other `SkylightError` → `UpdateFailed`, which retries with backoff and marks
  entities unavailable.

Frames are fetched **independently**. An account can hold several — a household frame and
a test frame, say — and one of them erroring must not blank the others. A frame that fails
is dropped from the snapshot, which makes its entities unavailable rather than leaving them
showing stale numbers, and the failure is logged. Only when *every* frame fails does the
refresh raise `UpdateFailed`, so a wholly broken account still backs off properly.

Fetching is sequential rather than concurrent. Concurrency would save a little latency on a
once-a-minute poll, at the cost of scheduling work outside Home Assistant's task tracking —
which also makes tests racy. Not a trade worth making at this poll rate.

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

## Frames and devices

A **frame** is the household — the calendar, chores, lists, and profiles. A **device** is
a physical display. Skylight models these separately, and a frame can hold more than one
device: a kitchen display and a bedroom one, each with its own name, alarms, and
nightlight.

Both become Home Assistant devices, with the hardware linked to its frame by `via_device`,
so it appears beneath it. Modelling this up front matters even for the common
one-device household: moving entities between devices later would relocate them in the
registry and break any dashboard card or automation that referenced the old device.

Every display setting lives on the **device**, including the ones the frame also reports.
`PUT /api/frames/{id}` accepts `brightness`, `sleeps_at`, `slideshow_speed` and the rest,
returns `200`, and applies **none** of them — verified against real hardware. That is why
there are no frame-level controls and why `pyskylight`'s `update_frame()` carries a
warning. A live test guards the finding, so if Skylight ever fixes that endpoint we will
hear about it rather than discover it by accident.

`sleep_mode` and `sleep_sound` stay read-only sensors: the API accepts only the current
value for `sleep_mode`, returning a 500 for anything else. Everything else the display
exposes is a control, and a test pins the exact set so a new attribute cannot quietly land
in the wrong place.

One wrinkle: `hardware_model` is returned only by `GET /api/frames/{id}`, not by the
collection endpoint the coordinator polls. It is static, so it is fetched once per frame
and cached rather than on every refresh.

## Chores as to-do lists

Checking off a chore is exactly a to-do interaction, so each family profile's chore chart
is a to-do entity rather than a pile of buttons. Home Assistant's to-do model covers the
whole useful surface: complete, reopen, rename, reschedule, add, and delete.

Two API rules shape the implementation, both learned by testing against a live frame:

- A **recurring** chore is completed per occurrence and needs `instance_date`; a one-off
  chore is rejected if you send one. The chore's `recurring` flag decides.
- Deleting a **recurring** chore requires `apply_to`; deleting a one-off chore is rejected
  if you send it.

Status changes go through the completions endpoint and everything else through the update
endpoint, so a rename does not re-send an unchanged status — Home Assistant populates every
field of a `TodoItem` on update, and sending the lot back would mean spurious writes.

Chore creation from Home Assistant is deliberately limited: a summary and a due date, on
the profile whose list it was added to. Recurrence is set up on the frame, which has the UI
for it.

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

`tests/test_live.py` drives the real account and display end to end, and is skipped unless
`SKYLIGHT_LIVE=1` is set, so CI and ordinary runs stay offline:

```bash
SKYLIGHT_LIVE=1 uv run pytest tests/test_live.py -v -s
```

It calls each control through Home Assistant's own services and verifies the result with an
**independent** pyskylight client, so a pass cannot be an artefact of the integration
believing its own writes. Everything is restored afterwards, and the restoration is
asserted field by field.

It is deliberately one long test rather than a tidy parametrized suite: **Skylight rate
limits logins hard**. An earlier version logged in twice per test — about twenty logins in
seconds — and the account began refusing logins for several minutes. A whole run now
performs exactly two.

The rest of the suite is offline. Tests patch `pyskylight`'s `Skylight` class with an autospec'd mock, so the fake client
cannot drift from the real signature without a test failure. Fixtures build real model
objects from real API-shaped payloads rather than mocks, so the decoding logic is exercised
too.

Snapshot tests pin the entity registry and the diagnostics output. They exist to make
unintended changes visible in review — regenerate them deliberately, and read the diff.

Coverage floor is 95%, currently 100%.

## Deliberately not exposed

**The task box.** Skylight's inbox holds unscheduled task *templates* — things you drag
onto the chore chart. It has no completion concept, so it does not fit Home Assistant's
to-do model: a checkbox would have to either destroy the item or always fail. Everything
actionable is already covered by the per-profile chore lists, so the task box is left
alone. Revisit if someone asks for it with a concrete use.

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
