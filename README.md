# Skylight for Home Assistant

A custom integration for [Skylight](https://www.skylight.com/) family calendars and chore
charts, built on [pyskylight](https://github.com/dknowles2/pyskylight).

> **Unofficial.** Not affiliated with or endorsed by Skylight. It talks to the same cloud
> API the Skylight apps use, which is reverse-engineered and can change without notice.

## Status

Early. The integration is deliberately narrow right now — it authenticates, polls, and
exposes chore and reward-point sensors per family profile. It is built to core-integration
standards from the start (config flow, coordinator, reauth, diagnostics, full test
coverage) so that widening it is a matter of adding platforms rather than retrofitting
quality.

Not exposed on purpose: Skylight's task box, which holds unscheduled task templates rather
than things that can be completed, and nudges — the spoken reminders, which a calendar
display accepts and never plays. See [docs/architecture.md](docs/architecture.md).

## Installation

### HACS

Add `https://github.com/dknowles2/ha-skylight` as a custom repository of type
*Integration*, install **Skylight**, and restart Home Assistant.

### Manual

Download `skylight.zip` from the [latest
release](https://github.com/dknowles2/ha-skylight/releases/latest), unpack it into
`config/custom_components/skylight/`, and restart.

Copying the folder out of a git checkout also works, but its `manifest.json` carries a
`0000.0.0` placeholder — the real version is stamped in when a release is built — so Home
Assistant would report a version that tells you nothing.

## Configuration

**Settings → Devices & Services → Add Integration → Skylight**, then sign in with the
email and password you use for the Skylight app.

The integration signs in through Skylight's OAuth flow and stores your credentials in the
config entry so it can renew the session unattended. If the password changes, Home
Assistant raises a re-authentication prompt rather than failing quietly.

## What you get

One **device per frame**, and per family profile on that frame. Skylight categories that
are calendar buckets rather than people — a shared `Family` calendar, `Family Birthdays`,
an `(unused)` leftover — are skipped, since they cannot hold chores:

| Entity | Description |
| --- | --- |
| `sensor.<frame>_<profile>_chores_due` | Chores assigned to that profile that are due today or overdue and not yet done |
| `sensor.<frame>_<profile>_chores_completed` | Chores that profile has completed today |
| `sensor.<frame>_<profile>_reward_points` | Current reward point balance, or unknown if the profile has no balance recorded |
| `sensor.<frame>_<profile>_lifetime_points` | Points earned all-time. Disabled by default |
| `number.<frame>_<profile>_<reward>` | What one of that profile's rewards costs, in points. Editable, and the target of the redeem action |
| `event.<frame>_reward_redeemed` | Fires whenever a reward is redeemed, wherever it happened |

Each physical display appears as its own device beneath its frame, with controls for
everything the hardware will let you change:

| Entity | Description |
| --- | --- |
| `switch.<device>_show_captions` / `_blur_effect` / `_side_by_side` / `_show_heart` | Slideshow display options |
| `number.<device>_brightness` | Screen brightness, 0–255 |
| `number.<device>_slideshow_speed` | Seconds per photo |
| `time.<device>_sleeps_at` / `_wakes_at` | When the screen sleeps and wakes |
| `sensor.<device>_sleep_mode` | Read-only; the API will not accept writes to this |

Every one of these was verified against real hardware — written, read back, and restored.

A **Skylight Buddy** gets these as well:

| Entity | Description |
| --- | --- |
| `switch.<device>_nightlight` | Turn the nightlight on and off |
| `number.<device>_nightlight_brightness` | Nightlight brightness |
| `select.<device>_nightlight_color` | Nightlight colour |
| `number.<device>_sleep_sound_volume` | Sleep sound volume |
| `sensor.<device>_sleep_sound` | Read-only |

A calendar display does not, even though the API happily stores all five for one. See
[Buddy-only settings](docs/architecture.md#buddy-only-settings).

One calendar per frame:

| Entity | Description |
| --- | --- |
| `calendar.<frame>_calendar` | Every event across the calendars synced into that household — the same thing the frame displays. Events can be created, edited, and deleted from Home Assistant, except that one occurrence of a repeating event cannot be changed on its own |

And one to-do entity per Skylight list:

| Entity | Description |
| --- | --- |
| `todo.<frame>_<list>` | A Skylight grocery or to-do list. Items can be added, renamed, checked off, reordered, and deleted from Home Assistant, and changes show up on the frame. The grocery list also takes recipe ingredients; see [Recipes and the grocery list](#recipes-and-the-grocery-list) |
| `todo.<frame>_<profile>_chores` | That profile's chores for today, including anything overdue. Check one off here and the frame's chore chart updates, and reorder them to change the order on the chart |
| `event.<frame>_chore_completed` | Fires whenever a chore is completed, wherever it happened |
| `todo.<frame>_up_for_grabs` | The frame's unclaimed chores — what the Skylight app calls *Up for Grabs*. Checking one off claims it for whoever ticked the box; see below |

Data refreshes every minute, and immediately after any change you make from Home
Assistant. A failed poll keeps showing the last known state for a few minutes rather than
blanking everything — Skylight returns the occasional 500 — and only reports entities as
unavailable once the failures persist.

## Reacting to what happens on the frame

Chores get ticked off and rewards get redeemed at the frame, not here. Two `event` entities
per frame turn that into something automations can trigger on:

| Entity | Fires when | Attributes |
| --- | --- | --- |
| `event.<frame>_chore_completed` | Any chore is completed | `chore`, `chore_id`, `occurrence_id`, `reward_points`, `profile`, `category_id`, `up_for_grabs`, `completed_at` |
| `event.<frame>_reward_redeemed` | Any reward is redeemed | `reward`, `reward_id`, `point_value`, `profile`, `category_id`, `redeemed_at` |

```yaml
automation:
  - triggers:
      - trigger: state
        entity_id: event.the_knowles_chore_completed
    conditions:
      - "{{ trigger.to_state.attributes.event_type == 'completed' }}"
    actions:
      - action: notify.mobile_app_phone
        data:
          message: >-
            {{ trigger.to_state.attributes.profile }} finished
            {{ trigger.to_state.attributes.chore }}
```

For an Up for Grabs chore, `profile` is whoever claimed it — the only record of who to
credit.

Each entity's own state is when the event fired, which is when the poll noticed: up to a
minute after the fact, since Skylight offers nothing to push with. `completed_at` and
`redeemed_at` carry the real times.

Nothing already in the data when Home Assistant starts fires. Today's finished chores and
a week of redemptions are in the first snapshot, and replaying that at every restart would
mean a burst of notifications for things you already saw.

## Rewards

Each reward is a `number` entity whose value is its point cost — editable, since Skylight
accepts a new price. `balance` and `affordable` attributes say whether the profile can
currently reach it.

Redeeming is an action rather than a button, because it spends points irreversibly and a
dashboard tap is too easy:

```yaml
actions:
  - action: skylight.redeem_reward
    target:
      entity_id: number.the_knowles_jacob_10_robux
```

To put it on a dashboard, use a button card with that as its `tap_action`.

A reward belongs to one family profile, so nothing names a recipient — that profile is the
one credited. Skylight owns the rules and enforces them: it deducts the points, refuses a
second redemption, and refuses one the balance cannot cover. Home Assistant does not
predict any of that, because a balance changes between polls; a refusal comes back
carrying Skylight's own wording, such as *Not enough points to redeem reward*.

**Only rewards that can still be redeemed get entities.** `respawn_on_redemption` does not
reset a reward: Skylight mints a new resource and keeps the old one as a record of the
redemption. The entity is keyed on the profile and the reward's name rather than that id,
so it survives a respawn instead of being replaced.

Rewards are created and deleted on the frame, which has proper UI for it.

### Awarding and deducting points

Points — the stars on the chore chart — can be moved from Home Assistant, for chores done
away from the frame or privileges withdrawn:

```yaml
actions:
  - action: skylight.award_points
    target:
      entity_id: sensor.the_knowles_jacob_reward_points
    data:
      points: 3
```

`skylight.deduct_points` is the same shape. Both take a positive number; deduction sends
the negative, because Skylight rejects a change of zero.

Skylight does **not** stop at zero: deducting more than the balance leaves it negative, and
lowers the lifetime figure with it. That is the frame's behaviour, so the lifetime sensor
is a `total` rather than a `total_increasing` one.

Redemptions fire `event.<frame>_reward_redeemed`, whether they happened here or at the
frame — see [Reacting to what happens on the frame](#reacting-to-what-happens-on-the-frame).

## Recipes and the grocery list

Skylight's meal planner holds recipes, each one free text with its ingredients written
into it. `skylight.add_recipe` pushes a recipe's ingredients onto the grocery list:

```yaml
actions:
  - action: skylight.add_recipe
    target:
      entity_id: todo.the_knowles_grocery_list
    data:
      recipe: Taco Night
```

The recipe is named rather than picked by id, exactly as it reads in the Skylight app;
capitals and stray spaces do not matter. A name that matches nothing, or matches two
recipes, is refused rather than guessed at.

Skylight parses the ingredients out of the recipe itself, on its own servers, so **the
items appear a few seconds after the action runs** — around ten in practice. Home
Assistant polls again on a short delay to pick them up, so the list fills in on its own.

**The destination is not a choice.** Ingredients always land on the frame's default
grocery list, whatever list you target — verified against a frame with two shopping
lists, where the second stayed empty. Targeting anything else is refused with a message
naming the list Skylight will actually use, rather than quietly filling the wrong one.

## Up for Grabs chores

Skylight lets a chore sit unassigned until somebody claims it. Those live on their own
to-do entity per frame, because they belong to no profile.

Completing one is different from completing an ordinary chore: Skylight records *who*
claimed it, and the API refuses a completion that does not say. So Home Assistant has to
know which of its people you are.

Open **Configure** on the Skylight integration and pair each Skylight profile with a Home
Assistant person. Checking off an Up for Grabs chore then credits whoever pressed the
button — the person linked to that Home Assistant user account.

If the person acting has no mapping, the completion is refused with an explanatory error
rather than credited to somebody else. That includes automations and voice assistants,
which carry no user at all. Everything else — renaming, rescheduling, deleting, reopening
— works without a mapping, and ordinary assigned chores never need one.

## Choosing which frames to show

An account can hold frames you would rather Home Assistant left alone — a test frame, or
one shared by a relative. **Configure → Choose which frames to show** picks them.

Leave every frame ticked and the integration follows the account, so a frame added later
appears on its own. Untick one and everything on it — calendar, chores, lists, displays —
is removed rather than left behind unavailable; tick it again and it comes back.

## Building a dashboard

See [docs/dashboard.md](docs/dashboard.md) for worked examples: a family view with a column
per person, a child's chore screen for a small wall display, progress meters, and a rewards
card that finds a person's rewards rather than listing them.

## The rewards card

The integration ships a Lovelace card for a profile's rewards — every one of them, how
close they are, and a redeem button on the ones in reach:

```yaml
type: custom:skylight-rewards
profile: Jacob
```

Nothing to install: it is served by the integration and registered with the frontend, so
there is no HACS plugin entry, no resource to configure, and no separate version to track.
It also appears in the card picker as **Skylight rewards**, with a visual editor — the
profile is a dropdown of whoever has rewards, so there is no YAML to write.

Rewards are discovered rather than listed, because they are created and renamed on the
frame — see [docs/dashboard.md](docs/dashboard.md) for the generic-card equivalents.

If the card appears on some devices and not others, see
[If the card does not appear on one device](docs/dashboard.md#if-the-card-does-not-appear-on-one-device);
a kiosk display holding a cached page is the usual reason.

## Blueprints

Two automation blueprints live in
[`blueprints/automation/skylight/`](blueprints/automation/skylight):

- **[Reward within reach](blueprints/automation/skylight/reward_within_reach.yaml)** —
  someone has earned enough points to redeem a reward.
- **[Reward redeemed](blueprints/automation/skylight/reward_redeemed.yaml)** — someone
  redeemed one, whether on the frame, in the Skylight app, or from Home Assistant.

Import either by pasting its URL into **Settings → Automations & scenes → Blueprints →
Import blueprint**. They are not installed with the integration: Home Assistant discovers
blueprints only under your own `blueprints/` folder, and the release zip contains just the
component.

## Troubleshooting

Enable debug logging:

```yaml
logger:
  default: info
  logs:
    custom_components.skylight: debug
    pyskylight: debug
```

The integration supports **diagnostics** — from the device or config entry page, *Download
diagnostics* gives the raw API payloads with names, emails, and share tokens redacted.
That output is the most useful thing to attach to a bug report.

## Brand assets

`custom_components/skylight/brand/` holds a deliberately generic calendar mark, not a
copy of Skylight's logo — it exists so the integration has an icon in the UI without
borrowing anyone's trademark. The long-term home for these is the
[home-assistant/brands](https://github.com/home-assistant/brands) repository, which needs
a separate submission and, for real Skylight artwork, their permission.

## Development

```bash
uv sync
```

```bash
uv run pytest
```

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy custom_components/skylight
```

Enable the git hooks (ruff, mypy, pytest, lockfile freshness, translation sync):

```bash
uv run pre-commit install
```

Tests run against a fake `pyskylight` client, so no account or network is needed. Snapshot
tests pin the entity registry and diagnostics output; update them deliberately with
`uv run pytest --snapshot-update` and read the diff before committing.

CI runs the test suite with coverage (floor: 95%), the linters, the pre-commit hooks, and
the two validators an integration has to satisfy: **hassfest**, the same one Home Assistant
core uses, and the **HACS** action.

`main` is protected: changes land through a pull request, and all four jobs have to pass
before one can be merged. There is no bypass, including for the repository owner.

Architecture and conventions are documented in
[docs/architecture.md](docs/architecture.md); releases in
[docs/releasing.md](docs/releasing.md).

## Versioning

Calendar versioning, `YYYY.M.N` — year, unpadded month, and a point release counting
from 0 within that month. `2026.8.0` is the first release of August 2026, `2026.8.1` the
next. This matches how Home Assistant itself is versioned, so a Skylight version reads
against a Home Assistant version without translation.

Deliberately not semantic versioning: this integration has no API for anyone to depend
on. What people care about is how current it is. Breaking changes are called out in the
release notes instead of being encoded in the number.

The version is not committed. `custom_components/skylight/manifest.json` carries a
`0000.0.0` placeholder in the repository, and the release workflow stamps the real version
in when it builds the zip that HACS installs. Cutting a release is publishing a draft that
Release Drafter has been assembling as pull requests merged — see
[docs/releasing.md](docs/releasing.md).

## License

Apache-2.0
