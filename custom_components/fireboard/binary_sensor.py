"""Binary sensor platform — alert state per channel."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FireboardCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """One alert binary sensor per channel that has alerts configured."""
    coordinator: FireboardCoordinator = hass.data[DOMAIN][entry.entry_id]
    seen: set[str] = set()

    @callback
    def _discover() -> None:
        new_entities: list[FireboardAlertBinarySensor] = []
        data = coordinator.data
        if not data:
            return
        for uuid, device in data.devices.items():
            for channel in device.get("channels", []):
                ch = channel.get("channel")
                if ch is None or channel.get("enabled") is False:
                    continue
                if not (channel.get("alerts") or []):
                    continue
                key = f"{uuid}::ch{ch}::alert"
                if key in seen:
                    continue
                seen.add(key)
                new_entities.append(
                    FireboardAlertBinarySensor(coordinator, uuid, int(ch))
                )
        if new_entities:
            async_add_entities(new_entities)

    _discover()
    entry.async_on_unload(coordinator.async_add_listener(_discover))


class FireboardAlertBinarySensor(
    CoordinatorEntity[FireboardCoordinator], BinarySensorEntity
):
    """True when the latest temperature is outside any enabled alert window."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "alert"

    def __init__(
        self, coordinator: FireboardCoordinator, uuid: str, channel: int
    ) -> None:
        super().__init__(coordinator)
        self._uuid = uuid
        self._channel = channel
        self._attr_unique_id = f"{uuid}_channel_{channel}_alert"

    @property
    def _device(self) -> dict[str, Any]:
        data = self.coordinator.data
        if not data:
            return {}
        return data.devices.get(self._uuid, {})

    def _channel_meta(self) -> dict[str, Any] | None:
        for c in self._device.get("channels", []):
            if c.get("channel") == self._channel:
                return c
        return None

    def _latest_temp(self) -> float | None:
        for t in self._device.get("latest_temps", []):
            if t.get("channel") == self._channel:
                return t.get("temp")
        return None

    @property
    def name(self) -> str:
        return "Alert"

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
    def is_on(self) -> bool | None:
        temp = self._latest_temp()
        if temp is None:
            return None
        meta = self._channel_meta() or {}
        for alert in meta.get("alerts") or []:
            if alert.get("enabled") is False:
                continue
            tmin = alert.get("temp_min")
            tmax = alert.get("temp_max")
            if tmin is not None and temp < tmin:
                return True
            if tmax is not None and temp > tmax:
                return True
        return False

    @property
    def available(self) -> bool:
        return super().available and self._latest_temp() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        meta = self._channel_meta() or {}
        # Surface the active alert thresholds so users don't have to dig.
        active: list[dict[str, Any]] = []
        for alert in meta.get("alerts") or []:
            if alert.get("enabled") is False:
                continue
            active.append(
                {
                    "temp_min": alert.get("temp_min"),
                    "temp_max": alert.get("temp_max"),
                    "minutes_repeat": alert.get("minutes_repeat"),
                    "notify_app": alert.get("notify_app"),
                    "notify_email": alert.get("notify_email"),
                    "notify_sms": alert.get("notify_sms"),
                }
            )
        return {
            "fireboard_kind": "alert",
            "channel": self._channel,
            "alerts": active,
        }
