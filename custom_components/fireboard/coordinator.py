"""DataUpdateCoordinator for the Fireboard integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import (
    FireboardApiError,
    FireboardAuthError,
    FireboardClient,
    FireboardRateLimitError,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


@dataclass
class FireboardData:
    """Snapshot of one poll cycle: devices keyed by UUID + active session per device.

    Active session lookup happens once per cycle and is shared across every
    entity that asks for it, so per-entity work stays in O(1).
    """

    devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    active_session_by_device: dict[str, dict[str, Any]] = field(default_factory=dict)


class FireboardCoordinator(DataUpdateCoordinator[FireboardData]):
    """Polls /devices.json + /sessions.json once per cycle.

    Two requests per minute = 10 per five minutes, comfortably under the
    documented 17-per-5-min ceiling.
    """

    def __init__(self, hass: HomeAssistant, client: FireboardClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client

    async def _async_update_data(self) -> FireboardData:
        try:
            devices = await self.client.async_get_devices()
            sessions = await self.client.async_get_sessions()
        except FireboardAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except FireboardRateLimitError as err:
            raise UpdateFailed(str(err)) from err
        except FireboardApiError as err:
            raise UpdateFailed(str(err)) from err

        device_map = {
            dev["uuid"]: dev
            for dev in devices
            if isinstance(dev, dict) and dev.get("uuid")
        }

        # Newest active session wins if multiple devices share an account.
        active_by_device: dict[str, dict[str, Any]] = {}
        for session in sessions:
            if session.get("end_time") is not None:
                continue
            for device_uuid in session.get("device_ids") or []:
                existing = active_by_device.get(device_uuid)
                if not existing or _start_of(session) > _start_of(existing):
                    active_by_device[device_uuid] = session

        return FireboardData(
            devices=device_map, active_session_by_device=active_by_device
        )


def _start_of(session: dict[str, Any]) -> str:
    """Comparable string for picking the most recent start_time."""
    return session.get("start_time") or session.get("created") or ""
