"""Number platform — adjustable setpoint for FireBoard Drive devices.

Disabled by default in the entity registry because the Fireboard write
endpoint isn't publicly documented; the body schema below (``{"setpoint":
<value>}``) is a best-effort guess from the ``last_drivelog.setpoint``
field. Users who enable this entity should test it during a non-critical
cook and open an issue if the format is wrong.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import FireboardApiError, FireboardAuthError
from .const import DEGREETYPE_CELSIUS, DOMAIN
from .coordinator import FireboardCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """One setpoint number per device that has FireBoard Drive."""
    coordinator: FireboardCoordinator = hass.data[DOMAIN][entry.entry_id]
    seen: set[str] = set()

    @callback
    def _discover() -> None:
        new_entities: list[FireboardSetpointNumber] = []
        data = coordinator.data
        if not data:
            return
        for uuid, device in data.devices.items():
            if (device.get("last_drivelog") or {}).get("setpoint") is None:
                continue
            key = f"{uuid}::setpoint_number"
            if key in seen:
                continue
            seen.add(key)
            new_entities.append(FireboardSetpointNumber(coordinator, uuid))
        if new_entities:
            async_add_entities(new_entities)

    _discover()
    entry.async_on_unload(coordinator.async_add_listener(_discover))


class FireboardSetpointNumber(
    CoordinatorEntity[FireboardCoordinator], NumberEntity
):
    """Adjustable setpoint for a Drive-equipped Fireboard."""

    _attr_has_entity_name = True
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_mode = NumberMode.BOX
    _attr_translation_key = "setpoint_target"
    _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: FireboardCoordinator, uuid: str) -> None:
        super().__init__(coordinator)
        self._uuid = uuid
        self._attr_unique_id = f"{uuid}_setpoint_target"

    @property
    def name(self) -> str:
        return "Setpoint target"

    @property
    def _device(self) -> dict[str, Any]:
        data = self.coordinator.data
        if not data:
            return {}
        return data.devices.get(self._uuid, {})

    @property
    def device_info(self) -> DeviceInfo:
        d = self._device
        return DeviceInfo(
            identifiers={(DOMAIN, self._uuid)},
            name=d.get("title") or "Fireboard",
            manufacturer="Fireboard Labs",
            model=d.get("model_name") or d.get("model") or "Fireboard",
        )

    @property
    def native_unit_of_measurement(self) -> str:
        log = self._device.get("last_drivelog") or {}
        if log.get("degreetype") == DEGREETYPE_CELSIUS:
            return UnitOfTemperature.CELSIUS
        if self._device.get("degreetype") == DEGREETYPE_CELSIUS:
            return UnitOfTemperature.CELSIUS
        return UnitOfTemperature.FAHRENHEIT

    @property
    def native_min_value(self) -> float:
        return 0 if self.native_unit_of_measurement == UnitOfTemperature.CELSIUS else 32

    @property
    def native_max_value(self) -> float:
        return 260 if self.native_unit_of_measurement == UnitOfTemperature.CELSIUS else 500

    @property
    def native_step(self) -> float:
        return 1

    @property
    def native_value(self) -> float | None:
        log = self._device.get("last_drivelog") or {}
        return log.get("setpoint")

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"fireboard_kind": "setpoint_target"}

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.coordinator.client.async_post_device(
                self._uuid, {"setpoint": value}
            )
        except FireboardAuthError as err:
            raise HomeAssistantError(f"Fireboard auth failed: {err}") from err
        except FireboardApiError as err:
            raise HomeAssistantError(
                f"Fireboard rejected setpoint update: {err}. "
                "The API write schema for setpoint isn't documented; "
                "if this consistently fails, please open an issue."
            ) from err
        await self.coordinator.async_request_refresh()
