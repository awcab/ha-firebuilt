"""Sensor platform for Fireboard."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEGREETYPE_CELSIUS, DEGREETYPE_FAHRENHEIT, DOMAIN
from .coordinator import FireboardCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create entities and add new ones as devices/channels appear."""
    coordinator: FireboardCoordinator = hass.data[DOMAIN][entry.entry_id]
    seen: set[str] = set()

    @callback
    def _discover() -> None:
        new_entities: list[SensorEntity] = []
        data = coordinator.data
        if not data:
            return
        for uuid, device in data.devices.items():
            for channel in device.get("channels", []):
                ch = channel.get("channel")
                if ch is None or channel.get("enabled") is False:
                    continue
                key = f"{uuid}::ch{ch}"
                if key in seen:
                    continue
                seen.add(key)
                new_entities.append(
                    FireboardChannelSensor(coordinator, uuid, int(ch))
                )

            if (
                f"{uuid}::battery" not in seen
                and _battery_value(device) is not None
            ):
                seen.add(f"{uuid}::battery")
                new_entities.append(FireboardBatterySensor(coordinator, uuid))

            if f"{uuid}::last_seen" not in seen:
                seen.add(f"{uuid}::last_seen")
                new_entities.append(FireboardLastSeenSensor(coordinator, uuid))

            # Drive (FireBoard Drive fan controller) entities — only for
            # devices that report a last_drivelog object.
            if (
                f"{uuid}::setpoint" not in seen
                and (device.get("last_drivelog") or {}).get("setpoint") is not None
            ):
                seen.add(f"{uuid}::setpoint")
                seen.add(f"{uuid}::drive_pct")
                seen.add(f"{uuid}::drive_vbatt")
                new_entities.extend(
                    [
                        FireboardSetpointSensor(coordinator, uuid),
                        FireboardDrivePercentSensor(coordinator, uuid),
                        FireboardDriveBatterySensor(coordinator, uuid),
                    ]
                )

            # Cook timer entities — added for every device; they go
            # unavailable when no session is active.
            if f"{uuid}::cook_started" not in seen:
                seen.add(f"{uuid}::cook_started")
                seen.add(f"{uuid}::cook_session")
                new_entities.extend(
                    [
                        FireboardCookStartedSensor(coordinator, uuid),
                        FireboardCookSessionSensor(coordinator, uuid),
                    ]
                )

        if new_entities:
            async_add_entities(new_entities)

    _discover()
    entry.async_on_unload(coordinator.async_add_listener(_discover))


def _battery_value(device: dict[str, Any]) -> float | None:
    """Best-effort battery percentage.

    Prefer the top-level ``last_battery_reading`` (always 0..1 when reporting).
    Pulse-class hardware reports ``device_log: null``, so we can't rely on
    that path alone. Fall back to ``device_log.vBattPer`` for older fields.
    """
    raw = device.get("last_battery_reading")
    if raw is None:
        log = device.get("device_log") or {}
        raw = log.get("vBattPer")
    if raw is None:
        return None
    try:
        pct = float(raw)
    except (TypeError, ValueError):
        return None
    if 0 <= pct <= 1:
        pct *= 100
    return round(pct, 1)


def _clean_label(raw: str | None) -> str | None:
    """Fireboard returns ``Channel (null)`` for unlabeled probes."""
    if not raw:
        return None
    if "(null)" in raw.lower():
        return None
    return raw.strip() or None


def _device_unit(device: dict[str, Any]) -> str:
    return (
        UnitOfTemperature.CELSIUS
        if device.get("degreetype") == DEGREETYPE_CELSIUS
        else UnitOfTemperature.FAHRENHEIT
    )


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S.%f").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None


DEVICE_STALE_AFTER = 300  # seconds — drop entities if device hasn't reported in 5 minutes


class _FireboardEntity(CoordinatorEntity[FireboardCoordinator]):
    """Base — pulls the live device dict out of the coordinator snapshot."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FireboardCoordinator, uuid: str) -> None:
        super().__init__(coordinator)
        self._uuid = uuid

    @property
    def _device(self) -> dict[str, Any]:
        data = self.coordinator.data
        if not data:
            return {}
        return data.devices.get(self._uuid, {})

    @property
    def _active_session(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if not data:
            return None
        return data.active_session_by_device.get(self._uuid)

    @property
    def _device_recently_seen(self) -> bool:
        """True if the device reported any temp within DEVICE_STALE_AFTER."""
        raw = self._device.get("last_templog")
        if not raw:
            return False
        ts = _parse_iso(raw)
        if not ts:
            return False
        delta = (datetime.now(timezone.utc) - ts).total_seconds()
        return delta < DEVICE_STALE_AFTER

    @property
    def available(self) -> bool:
        # Decouple availability from coordinator.last_update_success: a
        # single rate-limit / network blip shouldn't flicker every entity
        # to unavailable when we still have cached device data. The card
        # surfaces staleness through the `update_state` attribute and the
        # rate-limit banner.
        data = self.coordinator.data
        return data is not None and self._uuid in data.devices

    @property
    def device_info(self) -> DeviceInfo:
        d = self._device
        return DeviceInfo(
            identifiers={(DOMAIN, self._uuid)},
            name=d.get("title") or "Fireboard",
            manufacturer="Fireboard Labs",
            model=d.get("model_name") or d.get("model") or "Fireboard",
            sw_version=str(d.get("version") or "") or None,
        )


class FireboardChannelSensor(_FireboardEntity, SensorEntity):
    """One temperature sensor per probe channel."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(
        self, coordinator: FireboardCoordinator, uuid: str, channel: int
    ) -> None:
        super().__init__(coordinator, uuid)
        self._channel = channel
        self._attr_unique_id = f"{uuid}_channel_{channel}"

    def _channel_meta(self) -> dict[str, Any] | None:
        for c in self._device.get("channels", []):
            if c.get("channel") == self._channel:
                return c
        return None

    def _latest_temp(self) -> dict[str, Any] | None:
        # Primary: the top-level latest_temps array. Fireboard only
        # populates these when the value is < 1 minute old, so on a steady
        # cook with infrequent device updates the array can be empty for
        # a cycle even though we have a perfectly valid reading.
        for t in self._device.get("latest_temps", []):
            if t.get("channel") == self._channel:
                return t

        # Fallback: the channel's own embedded last_templog object, which
        # carries the most recent reading the channel has ever produced.
        # We only trust it if the device itself has reported anything in
        # the last DEVICE_STALE_AFTER seconds — otherwise we're showing a
        # stale reading from a device that's actually offline.
        if not self._device_recently_seen:
            return None
        meta = self._channel_meta() or {}
        last = meta.get("last_templog")
        if isinstance(last, dict) and last.get("temp") is not None:
            return last
        if meta.get("current_temp") is not None:
            return {
                "channel": self._channel,
                "temp": meta["current_temp"],
                "degreetype": meta.get("degreetype"),
            }
        return None

    @property
    def name(self) -> str:
        meta = self._channel_meta() or {}
        label = _clean_label(meta.get("channel_label")) or _clean_label(meta.get("name"))
        return label or f"Channel {self._channel}"

    @property
    def native_value(self) -> float | None:
        t = self._latest_temp()
        return t.get("temp") if t else None

    @property
    def native_unit_of_measurement(self) -> str:
        t = self._latest_temp()
        if t:
            dt = t.get("degreetype")
            if dt == DEGREETYPE_CELSIUS:
                return UnitOfTemperature.CELSIUS
            if dt == DEGREETYPE_FAHRENHEIT:
                return UnitOfTemperature.FAHRENHEIT
        return _device_unit(self._device)

    @property
    def available(self) -> bool:
        return super().available and self._latest_temp() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        meta = self._channel_meta() or {}
        attrs: dict[str, Any] = {
            "fireboard_kind": "channel",
            "channel": self._channel,
        }
        if (color := meta.get("color_hex")) is not None:
            attrs["color_hex"] = color
        if (alerts := meta.get("alerts")) is not None:
            attrs["alerts"] = alerts
        t = self._latest_temp()
        if t and (created := t.get("created")):
            attrs["measured_at"] = created
        return attrs


class FireboardBatterySensor(_FireboardEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "battery"

    def __init__(self, coordinator: FireboardCoordinator, uuid: str) -> None:
        super().__init__(coordinator, uuid)
        self._attr_unique_id = f"{uuid}_battery"

    @property
    def name(self) -> str:
        return "Battery"

    @property
    def native_value(self) -> float | None:
        return _battery_value(self._device)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"fireboard_kind": "battery"}


class FireboardLastSeenSensor(_FireboardEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "last_seen"

    def __init__(self, coordinator: FireboardCoordinator, uuid: str) -> None:
        super().__init__(coordinator, uuid)
        self._attr_unique_id = f"{uuid}_last_seen"

    @property
    def name(self) -> str:
        return "Last seen"

    @property
    def native_value(self) -> datetime | None:
        return _parse_iso(self._device.get("last_templog"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"fireboard_kind": "last_seen"}
        # Surface the coordinator's last error so the card can show a
        # rate-limit / connection banner. We tag rate limiting separately
        # because that's the only "transient and self-healing" failure mode.
        if not self.coordinator.last_update_success:
            err = self.coordinator.last_exception
            if err is not None:
                msg = str(err)
                low = msg.lower()
                if "rate" in low or "429" in low or "5 min" in low:
                    attrs["update_state"] = "rate_limited"
                else:
                    attrs["update_state"] = "error"
                attrs["update_error"] = msg[:200]
        return attrs


class FireboardSetpointSensor(_FireboardEntity, SensorEntity):
    """Drive setpoint — the target temperature the fan controller chases."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "setpoint"

    def __init__(self, coordinator: FireboardCoordinator, uuid: str) -> None:
        super().__init__(coordinator, uuid)
        self._attr_unique_id = f"{uuid}_setpoint"

    @property
    def name(self) -> str:
        return "Setpoint"

    @property
    def native_value(self) -> float | None:
        log = self._device.get("last_drivelog") or {}
        return log.get("setpoint")

    @property
    def native_unit_of_measurement(self) -> str:
        log = self._device.get("last_drivelog") or {}
        if log.get("degreetype") == DEGREETYPE_CELSIUS:
            return UnitOfTemperature.CELSIUS
        if log.get("degreetype") == DEGREETYPE_FAHRENHEIT:
            return UnitOfTemperature.FAHRENHEIT
        return _device_unit(self._device)

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"fireboard_kind": "setpoint"}


class FireboardDrivePercentSensor(_FireboardEntity, SensorEntity):
    """Fan/drive output as a percentage."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:fan"
    _attr_translation_key = "drive_output"
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: FireboardCoordinator, uuid: str) -> None:
        super().__init__(coordinator, uuid)
        self._attr_unique_id = f"{uuid}_drive_output"

    @property
    def name(self) -> str:
        return "Drive output"

    @property
    def native_value(self) -> float | None:
        log = self._device.get("last_drivelog") or {}
        raw = log.get("driveper")
        if raw is None:
            return None
        try:
            return round(float(raw) * 100, 1)
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        log = self._device.get("last_drivelog") or {}
        attrs: dict[str, Any] = {"fireboard_kind": "drive_output"}
        if (mode := log.get("modetype")) is not None:
            attrs["mode_type"] = mode
        if (power := log.get("powermode")) is not None:
            attrs["power_mode"] = power
        if (channel := log.get("tiedchannel")) is not None:
            attrs["tied_channel"] = channel
        return attrs

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None


class FireboardDriveBatterySensor(_FireboardEntity, SensorEntity):
    """Drive (fan controller) battery voltage — diagnostic."""

    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "drive_battery"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: FireboardCoordinator, uuid: str) -> None:
        super().__init__(coordinator, uuid)
        self._attr_unique_id = f"{uuid}_drive_battery"

    @property
    def name(self) -> str:
        return "Drive battery"

    @property
    def native_value(self) -> float | None:
        log = self._device.get("last_drivelog") or {}
        return log.get("vbatt")

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"fireboard_kind": "drive_battery"}


class FireboardCookStartedSensor(_FireboardEntity, SensorEntity):
    """Timestamp the active cook session started, or unavailable when idle."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "cook_started"
    _attr_icon = "mdi:timer-play-outline"

    def __init__(self, coordinator: FireboardCoordinator, uuid: str) -> None:
        super().__init__(coordinator, uuid)
        self._attr_unique_id = f"{uuid}_cook_started"

    @property
    def name(self) -> str:
        return "Cook started"

    @property
    def native_value(self) -> datetime | None:
        s = self._active_session
        if not s:
            return None
        return _parse_iso(s.get("start_time"))

    @property
    def available(self) -> bool:
        return super().available and self._active_session is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self._active_session or {}
        attrs: dict[str, Any] = {"fireboard_kind": "cook_started"}
        if (sid := s.get("id")) is not None:
            attrs["session_id"] = sid
        if (duration := s.get("duration")) is not None:
            attrs["duration"] = duration
        return attrs


class FireboardCookSessionSensor(_FireboardEntity, SensorEntity):
    """Title of the active cook session — handy for templates and UI."""

    _attr_translation_key = "cook_session"
    _attr_icon = "mdi:fire"

    def __init__(self, coordinator: FireboardCoordinator, uuid: str) -> None:
        super().__init__(coordinator, uuid)
        self._attr_unique_id = f"{uuid}_cook_session"

    @property
    def name(self) -> str:
        return "Cook session"

    @property
    def native_value(self) -> str | None:
        s = self._active_session
        if not s:
            return None
        return s.get("title") or s.get("description") or "Active cook"

    @property
    def available(self) -> bool:
        return super().available and self._active_session is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"fireboard_kind": "cook_session"}
