"""The Fireboard integration."""
from __future__ import annotations

import logging
import os

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FireboardClient
from .const import (
    CARD_BASE_URL,
    CARD_URL_PATH,
    CARD_URL_VERSIONED,
    CONF_TOKEN,
    DOMAIN,
)
from .coordinator import FireboardCoordinator
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Fireboard from a config entry."""
    session = async_get_clientsession(hass)
    client = FireboardClient(session, token=entry.data[CONF_TOKEN])

    coordinator = FireboardCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    await _async_register_frontend(hass)
    await async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        await async_unregister_services(hass)
    return unload_ok


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the www/ folder and register fireboard-card.js as a frontend resource.

    Idempotent — safe to call on every entry setup. We do this from the
    integration so users don't have to add a Lovelace resource by hand.
    Uses the new ``async_register_static_paths`` on HA 2024.7+ and falls
    back to ``register_static_path`` on older installs.
    """
    flag_key = "_frontend_registered"
    if hass.data[DOMAIN].get(flag_key):
        return

    www_dir = os.path.join(os.path.dirname(__file__), "www")
    card_file = os.path.join(www_dir, "fireboard-card.js")
    if not os.path.isfile(card_file):
        _LOGGER.error(
            "Fireboard card file not found at %s — the custom card "
            "will not be available. Reinstall the integration files.",
            card_file,
        )
        return

    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_BASE_URL, www_dir, True)]
        )
        _LOGGER.debug(
            "Registered Fireboard static path %s -> %s (modern API)",
            CARD_BASE_URL,
            www_dir,
        )
    except (AttributeError, ImportError):
        # Pre-HA-2024.7 fallback. register_static_path is synchronous and
        # was removed in HA 2025.6, so we only reach this branch on the
        # narrow 2024.4–2024.6 range where the new API doesn't exist yet.
        try:
            hass.http.register_static_path(CARD_BASE_URL, www_dir, True)
            _LOGGER.debug(
                "Registered Fireboard static path %s -> %s (legacy API)",
                CARD_BASE_URL,
                www_dir,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to register Fireboard static path: %s", err)
            return

    from homeassistant.components.frontend import add_extra_js_url

    add_extra_js_url(hass, CARD_URL_VERSIONED)
    # Some Lovelace dashboards (notably "strict" / module-only ones) require
    # ES module URLs. Adding both is harmless and maximises compatibility.
    try:
        from homeassistant.components.frontend import add_extra_module_url

        add_extra_module_url(hass, CARD_URL_VERSIONED)
    except ImportError:
        # add_extra_module_url isn't present on very old HA versions.
        pass

    hass.data[DOMAIN][flag_key] = True
    _LOGGER.info(
        "Fireboard Lovelace card registered at %s. "
        "If 'Custom element doesn't exist: fireboard-card' shows up, "
        "hard-refresh the dashboard (Ctrl+Shift+R / Cmd+Shift+R).",
        CARD_URL_VERSIONED,
    )
