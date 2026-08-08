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

One **device per frame**, and per family profile on that frame:

| Entity | Description |
| --- | --- |
| `sensor.<frame>_<profile>_chores_due` | Chores assigned to that profile that are due today or overdue and not yet done |
| `sensor.<frame>_<profile>_chores_completed` | Chores that profile has completed today |
| `sensor.<frame>_<profile>_reward_points` | Current reward point balance, or unknown if the profile has no balance recorded |

Each physical display appears as its own device beneath its frame, with the settings only
the hardware reports:

| Entity | Description |
| --- | --- |
| `binary_sensor.<device>_nightlight` | Whether the nightlight is on |
| `sensor.<device>_nightlight_brightness` | Nightlight brightness |
| `sensor.<device>_nightlight_color` | Nightlight colour |
| `sensor.<device>_sleep_mode` | What the display does when asleep |
| `sensor.<device>_sleep_sound` / `_sleep_sound_volume` | Sleep sound and its volume |

Settings the frame also reports — brightness, sleep schedule, slideshow — stay on the frame
so they are not duplicated.

One calendar per frame:

| Entity | Description |
| --- | --- |
| `calendar.<frame>_calendar` | Every event across the calendars synced into that household — the same thing the frame displays. Events can be created and deleted from Home Assistant |

And one to-do entity per Skylight list:

| Entity | Description |
| --- | --- |
| `todo.<frame>_<list>` | A Skylight grocery or to-do list. Items can be added, renamed, checked off, reordered, and deleted from Home Assistant, and changes show up on the frame |
| `todo.<frame>_<profile>_chores` | That profile's chores for today, including anything overdue. Check one off here and the frame's chore chart updates |

Data refreshes every minute, and immediately after any change you make from Home
Assistant.

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
[docs/architecture.md](docs/architecture.md).

## License

Apache-2.0
