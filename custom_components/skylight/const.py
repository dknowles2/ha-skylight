"""Constants for the Skylight integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "skylight"

MANUFACTURER: Final = "Skylight"

# Options key holding {skylight_category_id: home_assistant_person_entity_id}.
# Completing an "Up for Grabs" chore has to name who claimed it, and Home
# Assistant only knows which of its own people clicked the box.
CONF_PROFILE_MAP: Final = "profile_map"

# Buckets from GET /chores/all that make up "now": overdue, due today, and
# undated. `future` is left out so the Up for Grabs list matches the scope of
# the per-profile chore lists.
CURRENT_CHORE_BUCKETS: Final = ("late", "today", "today_timed", "any_day")

# Skylight is a cloud service with no push channel we know of, so we poll.
# Chores and calendar events change on human timescales; a minute is plenty
# responsive without being rude to an API we do not own.
SCAN_INTERVAL: Final = timedelta(minutes=1)

# How far ahead the coordinator pulls events, purely so a calendar entity can
# report the current or next event. The calendar panel queries its own ranges.
CALENDAR_LOOKAHEAD: Final = timedelta(days=14)
