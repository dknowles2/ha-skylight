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

**Device alarms.** Skylight exposes alarm endpoints on a device, but they are a
Skylight Buddy feature: creating one on a calendar display is rejected with `422 Device
must be a buddy device`, and that check runs before the body is validated, so the field
names cannot be discovered without Buddy hardware. There is nothing to build against.
Revisit if a Buddy turns up — the shape would be Home Assistant's `time` platform plus a
switch, one per alarm.

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
