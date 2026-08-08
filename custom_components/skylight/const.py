"""Constants for the Skylight integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "skylight"

MANUFACTURER: Final = "Skylight"

# Redeeming spends points and cannot be undone by a stray dashboard tap, so it
# is an action targeted at a reward rather than a button.
SERVICE_REDEEM_REWARD: Final = "redeem_reward"

# Awarding and deducting reward points — "stars" on the frame. Skylight rejects
# a change of zero, so both take a positive number and deduction negates it.
SERVICE_AWARD_POINTS: Final = "award_points"
SERVICE_DEDUCT_POINTS: Final = "deduct_points"
ATTR_POINTS: Final = "points"

# Pushing a recipe's ingredients onto the grocery list. Skylight parses them out
# of the recipe's free text, so this is an action rather than anything readable
# as entity state.
SERVICE_ADD_RECIPE: Final = "add_recipe"
ATTR_RECIPE: Final = "recipe"

# The ingredients do not exist when the call returns: Skylight parses them
# server-side and the items appear a few seconds later — about ten, measured.
# So the usual write-then-refresh shows an unchanged list, and a second refresh
# is scheduled far enough out to catch them.
RECIPE_INGREDIENT_DELAY: Final = timedelta(seconds=20)

# Options key holding {skylight_category_id: home_assistant_person_entity_id}.
# Completing an "Up for Grabs" chore has to name who claimed it, and Home
# Assistant only knows which of its own people clicked the box.
CONF_PROFILE_MAP: Final = "profile_map"

# Buckets from GET /chores/all that make up "now": overdue, due today, and
# undated. `future` is left out so the Up for Grabs list matches the scope of
# the per-profile chore lists.
CURRENT_CHORE_BUCKETS: Final = ("late", "today", "today_timed", "any_day")

# How far back to keep redeemed rewards in view. A redeemed reward drops out of
# the default listing, so without a lookback its button would vanish from the
# registry on the press and reappear whenever it respawned.
REWARD_LOOKBACK: Final = timedelta(days=7)

# Skylight is a cloud service with no push channel we know of, so we poll.
# Chores and calendar events change on human timescales; a minute is plenty
# responsive without being rude to an API we do not own.
SCAN_INTERVAL: Final = timedelta(minutes=1)

# How many consecutive failed polls to ride out before entities go unavailable.
# Skylight returns the occasional 500, and at a one-minute interval a single bad
# response should not blank a wall calendar's worth of entities. Three minutes of
# stale data is a better answer than three minutes of nothing; past that,
# something is actually wrong and saying so is the honest thing.
TOLERATED_FAILURES: Final = 3

# How far ahead the coordinator pulls events, purely so a calendar entity can
# report the current or next event. The calendar panel queries its own ranges.
CALENDAR_LOOKAHEAD: Final = timedelta(days=14)
