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
#
# `name` is redacted globally for this: it is the frame's user-set name (e.g.
# "Jacob's Room"), a device's name, and a person's display name on `User`.
# The trade-off is that a few non-personal `name` fields (e.g. `Reward.name`)
# get swept up too, which is an acceptable price for not missing a personal
# one — none of today's diagnostics sections dump a `Reward`, so it has no
# effect in practice yet.
TO_REDACT_DATA = {
    "assistant_household_id",
    "description",
    "email",
    "gift_recipient_name",
    "household_name",
    "invited_emails",
    "label",
    "name",
    "notification_email",
    "owner_birthday",
    "owner_email",
    "owner_name",
    "phone",
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
                "reward_points": [
                    async_redact_data(point.raw, TO_REDACT_DATA) for point in data.reward_points
                ],
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
