"""The Skylight integration.

Skylight sells a wall-mounted family calendar and chore chart. This integration
talks to the same cloud API the Skylight apps use, via the `pyskylight` library.
"""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pyskylight import PasswordAuth, Skylight

from .coordinator import SkylightConfigEntry, SkylightDataUpdateCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.TODO]


async def async_setup_entry(hass: HomeAssistant, entry: SkylightConfigEntry) -> bool:
    """Set up Skylight from a config entry."""
    session = async_get_clientsession(hass)
    auth = PasswordAuth(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        session=session,
    )
    client = Skylight(auth, session=session)

    coordinator = SkylightDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SkylightConfigEntry) -> bool:
    """Unload a config entry."""
    # The aiohttp session belongs to Home Assistant, so there is nothing of ours
    # to close here; the client and coordinator go out of scope with the entry.
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
