"""Serve the integration's Lovelace card and tell the frontend to load it.

A card shipped this way needs nothing from the user: no HACS plugin entry, no
resource to add, no version to keep in step with the integration. It arrives in
the release zip because that is built from this directory, and it is registered
the first time a config entry is set up.

The alternative is a second repository installed separately as a Lovelace
plugin, which is how most custom cards are distributed and which puts the card's
version and the integration's on different clocks.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http.server import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD = "skylight-rewards.js"
URL_BASE = f"/{DOMAIN}/frontend"
#: Set once the card has been registered, so a second config entry does not try
#: to register the same path again.
DATA_REGISTERED = f"{DOMAIN}_frontend_registered"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card and add it to the frontend's module list.

    Nothing here is allowed to stop the integration loading. `frontend` and
    `http` are `after_dependencies` rather than `dependencies` for the same
    reason: every real Home Assistant has both, but a chore chart that refuses
    to start because a card could not be served would be a poor trade.
    """
    if hass.data.get(DATA_REGISTERED):
        return
    if "frontend" not in hass.config.components or not hasattr(hass, "http"):
        _LOGGER.debug("Frontend not available; the Skylight card was not registered")
        return
    hass.data[DATA_REGISTERED] = True

    try:
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    URL_BASE,
                    str(Path(__file__).parent / "www"),
                    # Cache: the URL carries the version, so a stale file cannot
                    # survive an upgrade, and an unchanged one need not be
                    # re-fetched.
                    cache_headers=True,
                )
            ]
        )
    except (RuntimeError, ValueError) as err:
        # Registering the same path twice raises; so does a path that has gone
        # missing. Neither should stop the integration loading.
        _LOGGER.warning("Could not serve the Skylight card: %s", err)
        hass.data[DATA_REGISTERED] = False
        return

    integration = await async_get_integration(hass, DOMAIN)
    add_extra_js_url(hass, f"{URL_BASE}/{CARD}?v={integration.version}")
