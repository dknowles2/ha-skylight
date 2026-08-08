"""Config flow for the Skylight integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.components.person.const import DOMAIN as PERSON_DOMAIN
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from pyskylight import PasswordAuth, Skylight
from pyskylight.exceptions import AuthenticationError, SkylightError

from .const import CONF_PROFILE_MAP, DOMAIN
from .coordinator import SkylightConfigEntry
from .profiles import profile_map

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
        ),
    }
)


class SkylightConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Skylight."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: SkylightConfigEntry) -> SkylightOptionsFlow:
        """Return the options flow, which maps people to Skylight profiles."""
        return SkylightOptionsFlow()

    async def _async_validate(self, data: Mapping[str, Any]) -> tuple[str | None, dict[str, str]]:
        """Try the credentials.

        Returns the account's user id and a dict of form errors; exactly one of
        the two is meaningful.
        """
        session = async_get_clientsession(self.hass)
        auth = PasswordAuth(data[CONF_USERNAME], data[CONF_PASSWORD], session=session)
        client = Skylight(auth, session=session)
        try:
            user = await client.get_user()
        except AuthenticationError:
            return None, {"base": "invalid_auth"}
        except SkylightError:
            return None, {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unexpected error validating Skylight credentials")
            return None, {"base": "unknown"}
        return user.id, {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            unique_id, errors = await self._async_validate(user_input)
            if not errors:
                # The account id is stable across email changes, unlike the
                # address the user typed.
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=user_input[CONF_USERNAME], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(STEP_USER_DATA_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Handle a token or password that stopped working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again and confirm it works."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            data = {**reauth_entry.data, **user_input}
            unique_id, errors = await self._async_validate(data)
            if not errors:
                await self.async_set_unique_id(unique_id)
                # Re-authenticating into a different account would silently
                # repoint every entity, so require the same one.
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(reauth_entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD, autocomplete="current-password"
                        )
                    )
                }
            ),
            description_placeholders={CONF_USERNAME: reauth_entry.data[CONF_USERNAME]},
            errors=errors,
        )


class SkylightOptionsFlow(OptionsFlow):
    """Pair each Skylight family profile with a Home Assistant person.

    Only needed for "Up for Grabs" chores: those belong to nobody, and the API
    refuses a completion that does not say who claimed it. Assigned chores are
    credited automatically and need no mapping.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show one person picker per profile."""
        entry: SkylightConfigEntry = self.config_entry
        profiles = {
            category.id: category.label or category.id
            for frame_data in entry.runtime_data.data.values()
            for category in frame_data.profiles
        }
        if not profiles:
            return self.async_abort(reason="no_profiles")

        if user_input is not None:
            # Cleared pickers arrive as absent keys; store only real answers so
            # a profile someone deliberately unmapped does not linger.
            return self.async_create_entry(
                data={
                    CONF_PROFILE_MAP: {
                        category_id: user_input[category_id]
                        for category_id in profiles
                        if user_input.get(category_id)
                    }
                }
            )

        current = profile_map(entry)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        category_id,
                        description={"suggested_value": current.get(category_id)},
                    ): EntitySelector(EntitySelectorConfig(domain=PERSON_DOMAIN))
                    for category_id in profiles
                }
            ),
            description_placeholders={"profiles": ", ".join(profiles.values())},
        )
