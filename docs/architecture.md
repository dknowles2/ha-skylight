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
a test frame, say — and one of them erroring must not blank the others. Only when *every*
frame fails does the refresh raise `UpdateFailed`, so a wholly broken account still backs
off properly.

**A failure does not blank anything immediately.** Skylight returns the occasional 500, and
at a one-minute interval the default behaviour — every entity unavailable until the next
good poll — meant a chore list disappearing off a dashboard because one request went wrong.
The previous snapshot is served instead for up to `TOLERATED_FAILURES` consecutive polls,
counted per frame and once for the account, and reset by any success. Past that the failure
is reported properly: three minutes of stale data beats three minutes of nothing, but stale
data that never resolves is a lie.

Authentication failures are exempt. They will not fix themselves, and holding stale data
over one would only delay the reauth prompt the user needs to see.

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

Not every category is a person. Skylight uses one type for both family members and
calendar buckets — a shared `Family` calendar, a `Family Birthdays` feed, an `(unused)`
leftover. Only a category with `linked_to_profile` set is someone who can hold chores or
reward points; the rest would get a chore list that can never be filled.
`FrameData.profiles` is the filter, and every profile-scoped platform builds from it
rather than from `categories`.

`selected_for_chore_chart` looks like the same signal and is not: it marks who currently
appears on the frame's chore chart, so filtering on it would drop a real family member
the moment they were taken off the chart.

Entities created for a bucket by an earlier version are removed from the registry at
setup, rather than left unavailable forever. That deletion keys on categories the API
reported in *this* refresh, never on absence — a frame that failed to poll drops out of
the snapshot entirely, and treating that as "not a person" would wipe a household's
entities over one bad request.

Availability is layered: the coordinator's own success, then whether the frame is still
present, then whether the profile is. A profile removed from the frame leaves its entities
`unavailable` rather than silently reporting a stale number.

`frame_data` indexes the snapshot and raises if the frame is gone, which is fine for
everything Home Assistant reads *after* checking `available` — the short-circuit in each
`available` override is what keeps it safe. Calendars are the exception:
`CalendarEntity._async_write_ha_state` reads `self.event` before consulting availability, so
that path uses `frame_data_or_none`. A frame dropping out of a refresh raised a `KeyError`
there in production, and nothing failed in the test suite, because Home Assistant catches
exceptions from listener updates and logs them. The regression test asserts on the log for
that reason.

## Writes

Read paths go through the coordinator; write paths call the client directly and then
`async_request_refresh()`, so the UI reflects the change without waiting out the poll
interval. `SkylightEntity.async_write()` does both, and turns a `SkylightError` into a
`HomeAssistantError` carrying a translated message. A failed write must be visible —
silently doing nothing is the worst outcome for something the user just clicked.

Where the API offers both a bulk and a single-item endpoint, this integration uses
whichever was verified against the live API. Deleting to-do items goes one at a time for
that reason.

Home Assistant and Skylight disagree about how ordering works, and Skylight does not even
agree with itself. Home Assistant always moves an item "after this other one". A **list
item** goes to a position index, so `todo.py` derives one from the ordering at the last
poll; a **chore** goes to a neighbour, which the same instruction maps onto directly. Both
translations live in `todo.py`, next to the entity that needs them.

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
exposes is a control, and a test pins the exact set — per hardware kind, since some of it
is Buddy-only — so a new attribute cannot quietly land in the wrong place.

One wrinkle: `hardware_model` is returned only by `GET /api/frames/{id}`, not by the
collection endpoint the coordinator polls. It is static, so it is fetched once per frame
and cached rather than on every refresh. It is *not* what capability decisions are made
on, though — see below.

### Buddy-only settings

The nightlight (on/off, brightness, colour) and the sleep sound (volume, and the read-only
sound sensor) are only built for a **Skylight Buddy**. A calendar display gets none of
them, and `is_buddy()` in `entity.py` is the gate: `device.role == "buddy"`.

This one cannot be settled from the API, which is worth spelling out because every signal
it gives points the wrong way. On a real `15-CAL-2.0`:

- `GET .../devices/{id}` returns `nightlight`, `nightlight_brightness` and
  `nightlight_color`, with stored non-default values.
- `PUT` to each of them returns `200`, and an independent re-read confirms the new value.
- `nightlight_color: "purple"` is refused with `422 Nightlight color is not included in
  the list`, so the field is validated, not ignored.
- Alarms on the same device, minutes apart, fail with `422 Device must be a buddy device`
  — so the server does have a Buddy check. It just does not cover these fields.

What settles it is Skylight's own client. Its `deviceUtils.isBuddy` is exactly
`device.attributes.role === 'buddy'`; it renders the nightlight toggle and brightness
slider in one place only, its Buddy sleep screen, under the label key
`buddy:label.nightlight`; and it never reads or writes `nightlight_color` anywhere, on any
device. Sleep sounds live on the same screen, with a fixed set of five.

So these are columns the server will store for any device, not features it has. Building
controls from "the write succeeded" would give a calendar display a switch that flips,
persists, and does nothing — which is worse than not offering it, because the dashboard
then lies. The live test asserts the gate rather than driving the controls, since driving
them would pass either way.

## Chores as to-do lists

Checking off a chore is exactly a to-do interaction, so each family profile's chore chart
is a to-do entity rather than a pile of buttons. Home Assistant's to-do model covers the
whole useful surface: complete, reopen, rename, reschedule, add, and delete.

**A profile's chores come from two endpoints, because neither is complete.**

| Source | Covers | Misses |
| --- | --- | --- |
| `GET /chores` | chores of profiles with `selected_for_chore_chart` set, completed ones included | everyone taken off the chart |
| `/chores/all` | every profile, chart or not | anything already completed |

Both were established on a test frame: a new profile's chores stayed invisible to
`GET /chores` until the flag was set, then appeared at once; and completing a chore removed
it from `/chores/all`. This was not theoretical — on a real household two of three people
were off the chart, so two of the three chore lists could never fill.

`_merge_chores()` takes the charted chores and adds anything `/chores/all` knows about that
they missed, keyed on the occurrence id. `/chores/all` was already being fetched for Up for
Grabs, so this costs no extra request. One gap survives and cannot be closed: a chore
completed today by someone off the chore chart is in neither response.

**Reordering takes a neighbour, not an index.** `POST /chores/{id}/move` wants
`{"position": {"before": id}}` or `{"after": id}`; every scalar form of `position` is
rejected with `422 Position is required`. Home Assistant's move is "put this after that
one", which is `after` directly, and a move to the top has no previous item, so it becomes
`before` whatever is currently first.

The move changes each chore's `position` and leaves the response order alone, so the list
is sorted rather than rendered as it arrives — otherwise a reorder would appear to do
nothing until something else redrew the list.

**`position` alone is not the order.** Skylight numbers chores from 1 within each group
rather than across a profile's list, so a chart with ten daily chores and four one-off
assignments carries two sequences that both start at 1. Sorting on the number alone
interleaves them, and a real chart read out as "Brush Teeth", "Finish Summer Math Packet",
"Shower". `position` also runs across the whole day, so morning and bedtime chores
alternated.

`_chore_order()` sorts by time of day first, in clock order, then by `position` within
that group, with untimed chores last — an open-ended assignment belongs after everything
due at a particular moment, not in the middle of getting ready for bed. A chore with no
`position` sorts last within its group rather than crashing the comparison.

Up for Grabs uses the same key. Before, it was rendered in `/chores/all` bucket order —
`late`, `today`, `today_timed`, `any_day` — which ignores `position` outright and puts the
one chore with a time of day after everything without one.

One consequence worth knowing: a move is still sent as "after this chore", so moving
across time-of-day groups sets a `position` whose effect is not what the drag looked like.
Within a group it behaves.

Two API rules shape the implementation, both learned by testing against a live frame:

- A **recurring** chore is completed per occurrence and needs `instance_date`; a one-off
  chore is rejected if you send one. The chore's `recurring` flag decides.
- A chore with a **time of day** needs `instance_time` as well — `start_time` passed back
  unchanged, as `"HH:MM"`. Omitting it is `422 instance_time can't be blank`.
- Deleting a **recurring** chore requires `apply_to`; deleting a one-off chore is rejected
  if you send it.

`_instance()` produces both fields together, because they are one fact: which occurrence.
The occurrence id says the same thing — `"<chore_id>-<date>"` for an untimed chore,
`"<chore_id>-<date>-<HHMM>"` for a timed one.

**A chore's time of day is part of its due value, not decoration.** A chart with "Brush
Teeth" in the morning and again at bedtime produces two occurrences with the same summary
on the same date; a bare date makes them indistinguishable rows in Home Assistant. So
`_chore_due()` combines `start` and `start_time` into a local datetime, which also means
the two lists declare `SET_DUE_DATETIME_ON_ITEM` — the frontend sends a due containing a
time as `due_datetime`, and Home Assistant rejects a field the entity has not declared
before the call arrives here. `SET_DESCRIPTION_ON_ITEM` is declared for the same reason:
the frontend returns the whole item, so a chore carrying notes sends `description` back on
every edit.

That in turn is why the due comparison is against `_chore_due()` rather than `start`. A
datetime never equals a date, so comparing against `start` would read every tap on a timed
chore as a reschedule and write it back.

Status changes go through the completions endpoint and everything else through the update
endpoint, so a rename does not re-send an unchanged status — Home Assistant populates every
field of a `TodoItem` on update, and sending the lot back would mean spurious writes.

Chore creation from Home Assistant is deliberately limited: a summary and a due date, on
the profile whose list it was added to. Recurrence is set up on the frame, which has the UI
for it.

**Reward points ride in the description, because there is nowhere else.** `TodoItem` is a
closed six-field dataclass — `summary`, `uid`, `status`, `due`, `description`, `completed`
— and the websocket serializes exactly that tuple, so an entity cannot attach anything of
its own to an item. Points matter enough to show: on one real chart they are on eleven of
twenty chores and on *every* Up for Grabs one, which is the whole reason to claim a chore
nobody owns.

`description` is the safer of the two fields it could occupy. The other is `summary`,
which is also the chore's name on the frame itself, and generated text has no business
there.

The render is `⭐ 2`, above any notes the chore already carries. No words, so there is
nothing to translate, and it costs almost no width on the wall displays these lists end up
on.

The hazard is the round trip. The frontend returns the whole item on every tap, points
line included, so treating that as an edit would write the star into the chore's notes on
Skylight, re-prefix it on the next poll, and write it again. Two things prevent it:
`_changed_fields()` compares against the *rendered* description rather than the stored
one, and `_user_description()` strips the line back off before anything is written. There
are tests for both, including one asserting that checking a chore off writes no
description at all — eyeballing this is not enough, because the damage accumulates
silently on someone's real chore chart.

## Progress sensors

Three percentages per profile — the whole chart, the routine part, and everything else —
alongside the existing `chores_due` and `chores_completed` counts.

They are percentages because of a card limitation rather than a preference: `gauge` takes
one entity and a static maximum, so nothing on a dashboard can divide one sensor by
another. The counts survive as attributes, since a percentage on its own does not say out
of how many.

The routine split uses `chore.routine`, which the API sets itself. Skylight's own UI shows
morning and evening sections, but no endpoint names them — the labels are derived from the
hour there too, and a `BYHOUR=6` rule is all the data says. Bucketing by clock time would
mean inventing a threshold and would make the entity set depend on the data, so the flag
is used and the naming is left to whoever writes the dashboard heading.

An empty set reports `None`, not zero. A profile with nothing on their chart has no ratio,
and both 0% and 100% are assertions about a chart that does not exist — on one real
household two of three profiles had no chores at all, so this is the ordinary case.

There is no equivalent for Up for Grabs. A chore leaves `/chores/all` when it is
completed, so the open count is observable and the total is not; a percentage over an
unobservable denominator would fall as chores were claimed and reset when the day rolled.

## Rewards

A reward belongs to exactly one profile, so redeeming needs no say in who is claiming it —
the problem that shapes Up for Grabs does not arise.

A reward is a `number` whose value is its point cost, which Skylight accepts changes to.
Redemption is `skylight.redeem_reward`, an entity service targeting that number, rather
than a button: it spends points irreversibly, a dashboard tap is too easy, and an
automation may want to choose which reward to redeem.

Nothing here checks affordability. Skylight enforces both the balance
(`400 Not enough points to redeem reward.`) and double redemption
(`400 Reward has already been redeemed.`), and it deducts the points itself. Predicting
either locally would race the balance — refusing a press moments after a chore was
completed, when the points have already been earned. Both refusals carry usable wording,
so they surface as-is.

**`respawn_on_redemption` does not reset a reward.** Skylight mints a new resource and
keeps the old one as a record of the redemption, so the listing is part catalogue, part
history. Only `available_rewards` — the unredeemed part — becomes entities. Building from
the whole list produced several identically named rewards, most already spent; that was a
real bug on a real account, three `$10 Robux` entities deep.

The history is still fetched, with `redeemed_at_min` a week back, because it is the only
way a redemption is noticed at all: without it a redemption looks like a reward
disappearing rather than a redemption appearing.

Since a respawn changes the resource id, entities are keyed on the profile and the reward's
name instead. Keying on the id would hand out a fresh entity after every redemption and
break anything pointing at the old one. The cost of that choice is that renaming a reward
on the frame produces a new entity.

Points are moved with `skylight.award_points` and `skylight.deduct_points`, registered
against the profile's reward point sensor — the only entity that already knows both the
frame and the profile being credited. Aiming either at another sensor is refused rather
than quietly doing nothing.

Both take a positive number and deduction negates it, because Skylight answers `422` to a
change of zero. It does not clamp at the bottom either: deducting 100 from a balance of 5
leaves `-95`, and lowers `lifetime_points_earned` to match. That is why the lifetime sensor
is `TOTAL` and not `TOTAL_INCREASING` — Home Assistant reads a fall in the latter as a
counter reset and would corrupt the statistics.

Creating, editing and deleting rewards is left to the frame. `create_rewards` wants
explicit `category_ids`, and this is a parent-configures, child-redeems feature; redemption
is the part worth having here.

## Events

The things worth automating on — a chore finished, points cashed in — happen at the frame,
and a poll alone leaves only a changed attribute behind, which is awkward to trigger on.
`SkylightPollingEvent` turns a refresh into event entities: subclasses report what has
currently happened, and the base fires for whatever is new.

A subclass returns `{key: (marker, payload)}` covering only things that *have* happened. A
reversal — a chore reopened, a reward respawned — drops out of that mapping, so doing it
again counts as new and fires once more. The marker is the completion or redemption time,
so a second occurrence of the same key still fires.

One entity per frame per kind, not one per chore or reward. Both churn — rewards respawn,
chores fall out of today's window — and entities keyed to individual ones would come and
go in the registry, breaking anything referencing them.

Two details keep it honest:

- **The seen-set is seeded in `__init__`**, from the snapshot the entity is built on.
  `_handle_coordinator_update` only runs on *later* refreshes, and that first snapshot
  already holds today's completed chores and a week of redemptions. Seeding there instead
  would replay all of it at every restart.
- **State is written per event**, not once at the end, so two things happening inside one
  poll are two state changes rather than one that swallows the first.

For chores, `completed_category` is the credited profile: for an assigned chore that is its
owner, and for an up-for-grabs one it is whoever claimed it — the only record of who did it.

There is no push channel, so an event surfaces within one poll interval.

## Up for Grabs

A chore can belong to nobody until somebody claims it. Two things make this awkward
enough to be worth writing down.

**They are invisible to the endpoint we poll.** `GET /chores` never returns an unassigned
chore, whatever the date range, and rejects `up_for_grabs` and `filter` as query
parameters. `GET /chores/all` is the only source, so the coordinator makes a second
request per frame and keeps the buckets that match the per-profile lists — `late`,
`today`, `today_timed`, `any_day`. `future` is left out so every chore entity covers the
same span.

Detection needs both halves: `up_for_grabs` set **and** no category. `PUT` with the flag
alone returns 200 and changes nothing, so a chore can carry it while still belonging to
someone.

**Completing one has to name the claimant.** The completions endpoint takes `category_id`
for an unassigned chore and rejects it for an assigned one, where the credit is automatic.
Omitting it on an unassigned chore is a 422 — there is no anonymous completion.

Home Assistant supplies the name. `entity_service_call` sets the entity's context before
invoking the method, so `self._context.user_id` is whoever pressed the button; that user's
person entity is looked up in the state machine, and the options flow maps person entities
onto Skylight profiles. The mapping is keyed on the Skylight category id, since profiles
get renamed far more often than recreated.

When the acting user cannot be resolved — an automation, a voice assistant, an unmapped
person — the write is refused. A default profile would quietly credit one child for
another's chore, and a chore chart nobody trusts is worse than one that occasionally says
no.

Creating an Up for Grabs chore from Home Assistant is not offered: `POST /chores` answers
`422 Category is required.` whatever you send, so a chore cannot be born unowned.

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

### Editing events

`PUT /calendar_events/{id}` is a partial update — fields left out keep their values — but
Home Assistant hands over the whole event, so everything it knows about is sent.

**One occurrence of a repeating event cannot be changed on its own.** Skylight's update
always rewrites the series: `apply_to: "this"` is a `422`, and `"all"` is the only value it
takes. Editing next Tuesday would quietly change every Tuesday, so that is refused with an
explanation rather than performed.

Recurring events are recognised by their id. A one-off event's id is a plain number
(`11353806728`); an occurrence carries a `-<timestamp>` suffix
(`11353811507-1786321733`). Home Assistant's own `recurrence_id` is honoured too when it
sends one, but the id is what catches the common case, since each occurrence reaches Home
Assistant as its own event.

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

### How the cards reach a browser

`frontend.py` serves `www/` with `async_register_static_paths` and then announces each card
twice, which is worth explaining because the duplication looks like an accident.

`add_extra_js_url` is the documented way, and it is what HACS uses for its own frontend. It
writes a `<script type="module">` into the `index.html` Home Assistant renders — which
makes a card's availability a property of a document the client decides how long to keep. A
kiosk browser and a phone app in one household both went on serving a page from before the
card existed, and neither was misconfigured.

So the cards are also listed in the user's Lovelace resources. That list is fetched over
the websocket when a dashboard initialises, so nothing cached in front of it can hide a new
card. Both announcements name the same versioned url, and an ES module is keyed by resolved
url, so the file is fetched and evaluated once either way.

Nothing about writing resources is public API — [the developer
documentation](https://developers.home-assistant.io/docs/frontend/custom-ui/registering-resources/)
covers `/local` and manual registration and does not describe this at all. That is the
reason the documented mechanism stays rather than being replaced: if the unsupported half
breaks, the cards still load the old way. It is also why the import is deferred inside a
`try`, why every failure is a warning rather than a raise, and why the check for a writable
list asks the collection whether it has `async_create_item` instead of naming an internal
class.

That last choice matters more than it looks. The obvious test is the Lovelace `mode` string
— and an instance with no `lovelace:` block at all reports `auto-gen` while behaving as
storage, which is exactly why [HACS fails to register resources on a default
install](https://github.com/hacs/integration/issues/1659). Asking the object what it can do
sidesteps a bug that a mode check walks straight into.

Two consequences are easy to miss and both have tests. An entry is matched on its path with
the version stripped, so upgrading repoints the existing resource instead of leaving a dead
one per release — and a resource a user added by hand at that path, which the documentation
used to recommend, is adopted rather than duplicated. And removal happens in
`async_remove_entry`, never on unload: unload also runs on every restart, and only the last
config entry takes the cards away, since two accounts share one set.

### The cards

The two Lovelace cards in `custom_components/skylight/www/` are plain JavaScript with no
build step, and their rendering is not covered — that needs a browser. What is covered is
the seams, where the card has to agree with something outside itself and neither end
notices when the other moves:

- A chore's reward points travel inside its `description` as `⭐ 2`, because `TodoItem` has
  six fields and no room for a seventh. `tests/test_frontend.py` lifts the card's own regex
  out of the JavaScript file and feeds it real `_chore_description()` output, so it cannot
  become a stale copy of the pattern.
- The options each card documents are checked against the config keys it actually reads. An
  option that exists only in a table is worse than an undocumented one: it gets written
  into a dashboard and silently ignored.
- The YAML examples in the documentation are parsed and checked against what `setConfig`
  will accept.

Screenshots live in `docs/images/` and are generated, not captured:

```bash
uv run python scripts/shoot.py
```

That serves the repository, loads the cards from `custom_components/` so a screenshot is
never of a stale copy, photographs each one headlessly in both themes, and trims and
quantizes the result. Everything that decides what they look like is fixed — the fixture
data, the date, the window sizes — so re-running it against an unchanged card produces
byte-identical files and no diff. It needs Chrome and Pillow, and CI does not run it; a
test does check that every documented image exists, that none is an orphan, that each has
both themes, and that `scripts/shoot.py` still makes exactly the committed set.

### Entities that arrive later

Every platform builds its entities once, during `async_setup_entry`, from the coordinator's
first refresh. That is the usual shape and it is fine for anything whose existence is fixed
at setup — a frame, a display, the controls on it.

Rewards are not like that. A parent adds, renames and redeems them in the Skylight app
while Home Assistant is running, so `number.py` reconciles on every coordinator update
rather than building once. `_RewardReconciler` carries the reasoning; the short version is
that **the reward id does not name a reward**.

Verified against the live API: redeeming a reward with `respawn_on_redemption` set does not
mark it spent. Skylight consumes the resource and mints a replacement with a new id and the
same name — a probe went in as `12809772` and came back as `12809773`. So the id names one
instance of an offer. Keyed on it, the entity a child can act on would be a new entity after
every redemption, and since a disabled or removed entity keeps its entity id reserved, the
live one would slide to `..._2`, `..._3`, `..._4`, breaking anything aimed at it. That was
measured in the registry, not assumed.

What a parent means by a reward survives all of that, and it is `(profile, name)`. That is
the unique id.

The id is still worth having, because the two events move opposite fields — a rename keeps
the id and changes the name, a respawn changes the id and keeps the name. So a rename is
detectable, and the reconciler migrates the registry entry's unique id onto the new name and
rebuilds the entity onto it, keeping the entity id. Rebuilding beats reaching into a live
entity to change the name it identifies itself by.

Three things there are easy to get wrong, and all three have tests that fail without them:
the entity has to be torn down before the registry is repointed, or the rebuilt one lands on
a suffixed entity id; a rename onto a name another reward already has must be refused rather
than taking over that entity; and the handle to the old entity must not be dropped on that
refusal, or a later rename has nothing to migrate.

**The other platforms still build once.** A profile, list or display added on the frame
after Home Assistant started does not appear until the config entry is reloaded. That is a
real limitation rather than a decision — it simply has not bitten anyone yet, and rewards
did. The same pattern would fix them.

## Deliberately not exposed

**Device alarms.** Skylight exposes alarm endpoints on a device, but they are a
Skylight Buddy feature: creating one on a calendar display is rejected with `422 Device
must be a buddy device`, and that check runs before the body is validated, so the field
names cannot be discovered without Buddy hardware. There is nothing to build against.
Revisit if a Buddy turns up — the shape would be Home Assistant's `time` platform plus a
switch, one per alarm.

**Nudges.** A nudge is a message the frame is supposed to speak out loud to one or more
profiles, and it looked like a natural fit for the `notify` platform: one entity per
profile, so an automation could talk to the room. It was built, and then removed, because
the frame never says anything.

The API accepts it all. `POST /nudges` returns a created resource, Skylight renders the
speech in the cloud — `audio_url` fills in with a presigned MP3 within about ten seconds —
and the nudge appears in the listing. Two were sent to a real `15-CAL-2.0` with a family
member as the target, one with `deliver_at` set to now and one scheduled two minutes out.
Neither was heard, and neither showed on the frame.

The shape of it matches alarms, which are a Skylight Buddy feature and are rejected with
`422 Device must be a buddy device`. The difference is where the resource lives: alarms
hang off a *device*, so there is a device to check and reject against, while nudges hang off
the *frame*, where nothing knows what hardware will have to play them. So the write is
accepted, the audio is generated, and on a calendar display there is nothing to play it.

Revisit if a Buddy turns up. The Home Assistant shape is already known to be `notify`, one
entity per profile, and the API work is a single `create_nudge` call.

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
