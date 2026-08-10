"""The Skylight integration.

Skylight sells a wall-mounted family calendar and chore chart. This integration
talks to the same cloud API the Skylight apps use, via the `pyskylight` library.
"""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pyskylight import PasswordAuth, Skylight

from .const import CONF_FRAMES, DOMAIN
from .coordinator import SkylightConfigEntry, SkylightDataUpdateCoordinator
from .frontend import async_register_card, async_remove_resources

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
    await async_register_card(hass)
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
    _async_remove_excluded_frames(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: SkylightConfigEntry) -> None:
    """Reload when the options change.

    Changing which frames are exposed adds or removes whole devices, which only
    a reload can do. The profile mapping is read live and would not need this,
    but a reload is cheap and one listener is easier to reason about than two
    paths.
    """
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _async_remove_excluded_frames(hass: HomeAssistant, entry: SkylightConfigEntry) -> None:
    """Delete the devices of frames the user has excluded.

    Unlike the other cleanups here, this keys on an explicit choice rather than
    on absence from a refresh, so there is no risk of a failed poll deleting
    anything: a frame is removed only because someone unticked it.

    Removing a frame's device takes its entities with it, and the displays
    beneath it are removed too — they are linked by `via_device`, and a display
    whose frame is gone belongs to nothing.
    """
    chosen = set(entry.options.get(CONF_FRAMES) or ())
    if not chosen:
        return

    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, entry.entry_id)
    doomed = {
        device.id
        for device in devices
        if (
            frame_ids := {
                identifier
                for domain, identifier in device.identifiers
                if domain == DOMAIN and not identifier.startswith("device_")
            }
        )
        and not frame_ids & chosen
    }
    # Collected before anything is removed: the registry clears `via_device_id`
    # on the children as the parent goes, so a second pass afterwards would find
    # orphans that no longer admit what they hung off.
    doomed |= {device.id for device in devices if device.via_device_id in doomed}
    for device_id in doomed:
        registry.async_remove_device(device_id)


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


async def async_remove_entry(hass: HomeAssistant, entry: SkylightConfigEntry) -> None:
    """Clean up what outlives the entry.

    The cards are listed in the user's Lovelace resources, which is storage
    rather than anything the entry owns, so removing the integration has to take
    them out — otherwise the frontend goes on importing a path nothing serves.

    Only the last entry does it. Two accounts share one set of cards, and
    removing one account must not take the other's cards away.
    """
    if not [other for other in hass.config_entries.async_entries(DOMAIN) if other is not entry]:
        await async_remove_resources(hass)
