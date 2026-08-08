"""Mapping between Home Assistant people and Skylight family profiles.

Completing an "Up for Grabs" chore has to name the profile that claimed it —
the API refuses the completion otherwise. Home Assistant knows who pressed the
button, as a user id on the service call's context, so the missing link is a
mapping from its people to Skylight's.

The mapping is keyed on Skylight's category id, because that is the stable side:
profiles are renamed far more often than they are recreated.
"""

from __future__ import annotations

from homeassistant.components.person.const import DOMAIN as PERSON_DOMAIN
from homeassistant.components.person.const import PersonEntityStateAttribute
from homeassistant.core import HomeAssistant

from .const import CONF_PROFILE_MAP
from .coordinator import SkylightConfigEntry


def profile_map(entry: SkylightConfigEntry) -> dict[str, str]:
    """Return {skylight_category_id: person_entity_id} from the entry options."""
    stored = entry.options.get(CONF_PROFILE_MAP, {})
    return {str(key): str(value) for key, value in stored.items() if value}


def person_for_user(hass: HomeAssistant, user_id: str) -> str | None:
    """Return the person entity linked to a Home Assistant user account."""
    for state in hass.states.async_all(PERSON_DOMAIN):
        if state.attributes.get(PersonEntityStateAttribute.USER_ID) == user_id:
            return state.entity_id
    return None


def category_for_user(
    hass: HomeAssistant, entry: SkylightConfigEntry, user_id: str | None
) -> str | None:
    """Return the Skylight profile that a Home Assistant user maps to.

    `None` covers every way this can come up empty: a call with no user at all
    (an automation, or a voice assistant), a user with no person, or a person
    nobody has mapped yet. The caller decides what to do about it — for chore
    completion that means refusing, since guessing would credit the wrong
    child's chore chart.
    """
    if user_id is None:
        return None
    if (person := person_for_user(hass, user_id)) is None:
        return None
    for category_id, entity_id in profile_map(entry).items():
        if entity_id == person:
            return category_id
    return None
