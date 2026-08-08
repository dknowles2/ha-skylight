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
than things that can be completed. See [docs/architecture.md](docs/architecture.md).

## Installation

### HACS

Add `https://github.com/dknowles2/ha-skylight` as a custom repository of type
*Integration*, install **Skylight**, and restart Home Assistant.

### Manual

Copy `custom_components/skylight` into your Home Assistant `config/custom_components/`
directory and restart.

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
| `button.<frame>_<profile>_<reward>` | Redeem one of that profile's rewards |
| `event.<frame>_reward_redeemed` | Fires whenever a reward is redeemed, wherever it happened |

Each physical display appears as its own device beneath its frame, with controls for
everything the hardware will let you change:

| Entity | Description |
| --- | --- |
| `switch.<device>_nightlight` | Turn the nightlight on and off |
| `switch.<device>_show_captions` / `_blur_effect` / `_side_by_side` / `_show_heart` | Slideshow display options |
| `number.<device>_brightness` | Screen brightness, 0–255 |
| `number.<device>_nightlight_brightness` | Nightlight brightness |
| `number.<device>_sleep_sound_volume` | Sleep sound volume |
| `number.<device>_slideshow_speed` | Seconds per photo |
| `time.<device>_sleeps_at` / `_wakes_at` | When the screen sleeps and wakes |
| `select.<device>_nightlight_color` | Nightlight colour |
| `sensor.<device>_sleep_mode` / `_sleep_sound` | Read-only; the API will not accept writes to these |

Every one of these was verified against real hardware — written, read back, and restored.

One calendar per frame:

| Entity | Description |
| --- | --- |
| `calendar.<frame>_calendar` | Every event across the calendars synced into that household — the same thing the frame displays. Events can be created and deleted from Home Assistant |

And one to-do entity per Skylight list:

| Entity | Description |
| --- | --- |
| `todo.<frame>_<list>` | A Skylight grocery or to-do list. Items can be added, renamed, checked off, reordered, and deleted from Home Assistant, and changes show up on the frame |
| `todo.<frame>_<profile>_chores` | That profile's chores for today, including anything overdue. Check one off here and the frame's chore chart updates |
| `todo.<frame>_up_for_grabs` | The frame's unclaimed chores — what the Skylight app calls *Up for Grabs*. Checking one off claims it for whoever ticked the box; see below |

Data refreshes every minute, and immediately after any change you make from Home
Assistant.

## Rewards

Each reward belongs to one family profile, so its button redeems for that profile — no
mapping needed, unlike Up for Grabs chores.

Skylight owns the rules and enforces them: it deducts the points itself, refuses a second
redemption, and refuses one the balance cannot cover. Home Assistant does not try to
predict any of that, because a balance can change between polls; a refusal comes back as
an error carrying Skylight's own wording, such as *Not enough points to redeem reward*.

Each button carries the cost and the last redemption as attributes. A button's own state
is when Home Assistant last pressed it, which says nothing about redemptions made on the
frame — `redeemed_at` is the honest answer.

Rewards are created and edited on the frame, which has proper UI for it. Home Assistant
only redeems.

### Reacting to a redemption

Most redemptions happen at the frame, not here, so there is one `event` entity per frame
that fires whenever a reward is redeemed — however it was redeemed. The details ride along
as attributes:

```yaml
automation:
  - triggers:
      - trigger: state
        entity_id: event.the_knowles_reward_redeemed
    conditions:
      - "{{ trigger.to_state.attributes.event_type == 'redeemed' }}"
    actions:
      - action: notify.mobile_app_phone
        data:
          title: Reward redeemed
          message: >-
            {{ trigger.to_state.attributes.profile }} redeemed
            {{ trigger.to_state.attributes.reward }}
            ({{ trigger.to_state.attributes.point_value }} points)
```

Available attributes: `reward`, `reward_id`, `point_value`, `profile`, `category_id`, and
`redeemed_at`. The entity's own state is when the event fired, which is when the poll
noticed — up to a minute after the fact, since Skylight offers nothing to push to.

Redemptions already in the data when Home Assistant starts do not fire. Rewards are
fetched with a week's lookback, and replaying that history at every restart would mean a
burst of notifications for things you already saw.

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

The version lives in `custom_components/skylight/manifest.json` — that is the one Home
Assistant and HACS read — and is mirrored in `pyproject.toml`. A pre-commit hook checks
that they agree and that the format is right, and the release workflow refuses a tag that
does not name the manifest version.

## License

Apache-2.0
