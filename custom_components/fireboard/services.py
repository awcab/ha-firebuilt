"""Service handlers for Fireboard."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.const import CONF_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .api import FireboardApiError, FireboardAuthError
from .const import DOMAIN
from .coordinator import FireboardCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_END_SESSION = "end_session"

END_SESSION_SCHEMA = vol.Schema(
    {
        vol.Exclusive(CONF_DEVICE_ID, "target"): cv.string,
        vol.Exclusive("session_id", "target"): vol.Coerce(int),
    },
    extra=vol.ALLOW_EXTRA,
)


def _coordinators(hass: HomeAssistant) -> list[FireboardCoordinator]:
    return [c for c in hass.data.get(DOMAIN, {}).values() if isinstance(c, FireboardCoordinator)]


def _resolve_uuid_from_device_id(hass: HomeAssistant, device_id: str) -> str | None:
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if not device:
        return None
    for ident in device.identifiers:
        if len(ident) == 2 and ident[0] == DOMAIN:
            return ident[1]
    return None


async def async_register_services(hass: HomeAssistant) -> None:
    """Register Fireboard services exactly once per HA instance."""
    if hass.services.has_service(DOMAIN, SERVICE_END_SESSION):
        return

    async def _handle_end_session(call: ServiceCall) -> None:
        session_id = call.data.get("session_id")
        device_id = call.data.get(CONF_DEVICE_ID)

        if not session_id and not device_id:
            raise HomeAssistantError(
                "Pass either session_id or device_id to end_session"
            )

        coordinators = _coordinators(hass)
        if not coordinators:
            raise HomeAssistantError("Fireboard integration not loaded")

        target_uuid = (
            _resolve_uuid_from_device_id(hass, device_id) if device_id else None
        )
        if device_id and not target_uuid:
            raise HomeAssistantError(
                f"Device {device_id} is not a Fireboard device"
            )

        # Find which coordinator owns this device + which session id to end.
        target_coord: FireboardCoordinator | None = None
        target_session_id: int | None = session_id
        for coord in coordinators:
            data = coord.data
            if not data:
                continue
            if target_uuid:
                if target_uuid in data.devices:
                    target_coord = coord
                    if not target_session_id:
                        active = data.active_session_by_device.get(target_uuid)
                        if not active:
                            raise HomeAssistantError(
                                "No active session for that device"
                            )
                        target_session_id = active["id"]
                    break
            elif target_session_id:
                # Pick the first coordinator that has this session active.
                for sess in data.active_session_by_device.values():
                    if sess.get("id") == target_session_id:
                        target_coord = coord
                        break
                if target_coord:
                    break

        if not target_coord or not target_session_id:
            raise HomeAssistantError(
                "Could not locate the requested Fireboard session"
            )

        try:
            await target_coord.client.async_delete_session(int(target_session_id))
        except FireboardAuthError as err:
            raise HomeAssistantError(f"Fireboard auth failed: {err}") from err
        except FireboardApiError as err:
            raise HomeAssistantError(str(err)) from err

        _LOGGER.info("Ended Fireboard session %s", target_session_id)
        await target_coord.async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        SERVICE_END_SESSION,
        _handle_end_session,
        schema=END_SESSION_SCHEMA,
    )


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Drop services when the last entry unloads."""
    if hass.data.get(DOMAIN):
        return  # other entries still loaded
    if hass.services.has_service(DOMAIN, SERVICE_END_SESSION):
        hass.services.async_remove(DOMAIN, SERVICE_END_SESSION)
