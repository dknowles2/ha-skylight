"""Diagnostics support for the Skylight integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import SkylightConfigEntry

# Redacted from the config entry: the credentials themselves.
TO_REDACT_CONFIG = {CONF_USERNAME, CONF_PASSWORD}

# Redacted from API payloads: anything identifying a real person or granting
# access to the account. `share_token` in particular is a working invite.
TO_REDACT_DATA = {
    "email",
    "household_name",
    "invited_emails",
    "label",
    "notification_email",
    "owner_birthday",
    "owner_email",
    "owner_name",
    "profile_picture_urls",
    "share_token",
    "summary",
    "uid",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SkylightConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT_CONFIG),
        "frames": [
            {
                "frame": async_redact_data(data.frame.raw, TO_REDACT_DATA),
                "categories": [
                    async_redact_data(category.raw, TO_REDACT_DATA) for category in data.categories
                ],
                "chores": [async_redact_data(chore.raw, TO_REDACT_DATA) for chore in data.chores],
                "reward_points": [point.raw for point in data.reward_points],
                "lists": [
                    {
                        **async_redact_data(skylight_list.raw, TO_REDACT_DATA),
                        "items": [
                            async_redact_data(item.raw, TO_REDACT_DATA)
                            for item in skylight_list.items
                        ],
                    }
                    for skylight_list in data.lists
                ],
            }
            for data in coordinator.data.values()
        ],
    }
