"""The Skylight integration.

Skylight sells a wall-mounted family calendar and chore chart. This integration
talks to the same cloud API the Skylight apps use, via the `pyskylight` library.
"""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pyskylight import PasswordAuth, Skylight

from .coordinator import SkylightConfigEntry, SkylightDataUpdateCoordinator

PLATFORMS: list[Platform] = [
    Platform.CALENDAR,
    Platform.EVENT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
    Platform.TODO,
]


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
    _async_remove_non_profile_entities(hass, entry, coordinator)
    _async_remove_reward_buttons(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


@callback
def _async_remove_reward_buttons(hass: HomeAssistant, entry: SkylightConfigEntry) -> None:
    """Delete the reward buttons an earlier version created.

    Rewards are numbers now, redeemed through `skylight.redeem_reward`. The old
    buttons would otherwise sit in the registry unavailable — and there were
    several per reward, because a respawning reward mints a new resource on each
    redemption and every one of them got a button.
    """
    registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if registry_entry.domain == Platform.BUTTON:
            registry.async_remove(registry_entry.entity_id)


@callback
def _async_remove_non_profile_entities(
    hass: HomeAssistant,
    entry: SkylightConfigEntry,
    coordinator: SkylightDataUpdateCoordinator,
) -> None:
    """Delete entities left over for categories that are not people.

    Earlier versions built a chore list and sensors for every Skylight
    category, including calendar buckets like `Family Birthdays` and the
    `(unused)` placeholder. Those can never hold a chore, so they are dropped
    rather than left in the registry as unavailable forever.

    Only categories the API reported in this refresh are considered, so a frame
    that failed to poll cannot cause a deletion.
    """
    prefixes = tuple(
        f"{frame_id}_{category.id}_"
        for frame_id, frame_data in coordinator.data.items()
        for category in frame_data.categories
        if not category.linked_to_profile
    )
    if not prefixes:
        return

    registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if registry_entry.unique_id.startswith(prefixes):
            registry.async_remove(registry_entry.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: SkylightConfigEntry) -> bool:
    """Unload a config entry."""
    # The aiohttp session belongs to Home Assistant, so there is nothing of ours
    # to close here; the client and coordinator go out of scope with the entry.
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
