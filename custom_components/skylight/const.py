"""Constants for the Skylight integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "skylight"

MANUFACTURER: Final = "Skylight"

# Skylight is a cloud service with no push channel we know of, so we poll.
# Chores and calendar events change on human timescales; a minute is plenty
# responsive without being rude to an API we do not own.
SCAN_INTERVAL: Final = timedelta(minutes=1)
