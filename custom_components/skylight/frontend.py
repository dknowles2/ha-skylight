"""Serve the integration's Lovelace cards and tell the frontend to load them.

Cards shipped this way need nothing from the user: no HACS plugin entry, no
resource to add, no version to keep in step with the integration. They arrive in
the release zip because that is built from this directory, and they are
registered the first time a config entry is set up.

The alternative is a second repository installed separately as a Lovelace
plugin, which is how most custom cards are distributed and which puts the card's
version and the integration's on different clocks.

They are announced to the frontend **twice**, deliberately.

`add_extra_js_url` is the documented way, and it writes a `<script type=module>`
into the `index.html` Home Assistant serves. That makes a card's availability a
property of an HTML document the client decides how long to keep — and a display
holding one from before the upgrade never learns the card exists. Two of one
household's three devices hit this: a kiosk browser on a wall panel, and the
companion app on a phone. Neither is misconfigured, and neither is reachable
from here.

A Lovelace resource does not have that problem. The list is fetched over the
websocket when a dashboard initialises and each url is imported at runtime, so
nothing cached in front of it can hide a new card.

Doing both costs almost nothing: the script tag and the resource name the same
absolute url, and an ES module is keyed by resolved url, so it is fetched and
evaluated once whichever announcement the frontend acts on. Registering a
resource is also not public API, which is the other half of the reason the
documented mechanism stays: if it breaks, the cards still load the old way.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http.server import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

#: Every card served from `www/`. One static path covers the directory; each
#: file still needs its own module url, or the frontend never loads it.
CARDS = ("skylight-rewards.js", "skylight-chores.js")
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
    for card in CARDS:
        add_extra_js_url(hass, _url(card, integration.version))
    await async_register_resources(hass, str(integration.version))


def _url(card: str, version: object) -> str:
    """Return the url a card is served from, stamped with the version.

    The file name never changes, so without the stamp a browser holding last
    release's card has no reason to ask for this one.
    """
    return f"{URL_BASE}/{card}?v={version}"


def _resources(hass: HomeAssistant) -> Any | None:
    """Return the Lovelace resource list, if it is one this can write to.

    None when Lovelace has not set up yet, and None in YAML resource mode, where
    the list is whatever `configuration.yaml` says and adding to it is not this
    integration's business. Both fall back to `add_extra_js_url` alone, which is
    what every install had before this existed.
    """
    try:
        # Imported here rather than at the top on purpose. This is the one thing
        # in the integration reaching for something Home Assistant does not
        # promise, and a module-level import that stopped resolving would take
        # the whole integration down with it — chore charts and all — rather
        # than costing a card registration nobody strictly needs.
        from homeassistant.components.lovelace.const import (  # noqa: PLC0415
            LOVELACE_DATA,
        )
    except ImportError:  # pragma: no cover - only if core moves it
        _LOGGER.debug("Lovelace resources are not available in this Home Assistant")
        return None

    if (lovelace := hass.data.get(LOVELACE_DATA)) is None:
        return None
    resources = lovelace.resources
    # Asked by capability rather than by class. The YAML-mode collection has no
    # `async_create_item` at all, which is exactly the distinction that matters,
    # and none of this is public API — a check that does not name an internal
    # class is one less thing to break when core moves one.
    if not hasattr(resources, "async_create_item"):
        return None
    return resources


async def async_register_resources(hass: HomeAssistant, version: str) -> None:
    """List the cards as Lovelace resources, adopting any already there.

    Adopting rather than adding is the whole trick. A resource is matched on its
    path with the version stripped, so the entry this wrote last release is
    updated to point at this one, and so is one a user added by hand — which the
    documentation told people to do, at exactly this path, as the workaround for
    the problem this method exists to remove. Matching on the full url instead
    would leave a household collecting a dead resource per upgrade.
    """
    if (resources := _resources(hass)) is None:
        return

    try:
        # Nothing else here reads the collection, and `async_items` returns an
        # empty list rather than loading it. Asking for the count is the way to
        # be sure it has been read off disk first.
        await resources.async_get_info()
        existing = {
            item["url"].split("?")[0]: item
            for item in resources.async_items()
            if isinstance(item.get("url"), str)
        }
        for card in CARDS:
            url = _url(card, version)
            item = existing.get(f"{URL_BASE}/{card}")
            if item is None:
                await resources.async_create_item({"res_type": "module", "url": url})
            elif item["url"] != url:
                await resources.async_update_item(item["id"], {"url": url})
    except (HomeAssistantError, AttributeError, KeyError, TypeError) as err:
        # Not fatal, and deliberately not raised: the cards are already
        # registered the documented way by the time this runs, so a resource
        # list that cannot be written costs the wall panels and nothing else.
        _LOGGER.warning("Could not list the Skylight cards as Lovelace resources: %s", err)


async def async_remove_resources(hass: HomeAssistant) -> None:
    """Take the cards out of the resource list when the integration is removed.

    Only on removal, never on unload — unload also happens on every restart, and
    a resource that came and went with each one would be worse than none.

    Left behind, these point at a path nothing serves any more, and the frontend
    reports a failed import on every dashboard load.
    """
    if (resources := _resources(hass)) is None:
        return

    try:
        await resources.async_get_info()
        ours = [
            item
            for item in resources.async_items()
            if isinstance(item.get("url"), str)
            and item["url"].split("?")[0] in {f"{URL_BASE}/{card}" for card in CARDS}
        ]
        for item in ours:
            await resources.async_delete_item(item["id"])
    except (HomeAssistantError, AttributeError, KeyError, TypeError) as err:
        _LOGGER.warning("Could not remove the Skylight cards from the resources: %s", err)
