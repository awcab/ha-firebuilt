"""Unit-style check: confirm the channel sensor stays available when
Fireboard's top-level latest_temps drops a channel for a cycle but the
channel's nested last_templog still carries a recent reading. Also
confirms the staleness gate fires after DEVICE_STALE_AFTER seconds.
"""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "custom_components" / "fireboard"

# Build a fake homeassistant tree so we can import sensor.py without HA.
ha_pkg = types.ModuleType("homeassistant")
ha_pkg.__path__ = []
sys.modules["homeassistant"] = ha_pkg

# Submodules referenced by sensor.py
def _ns(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


_ns("homeassistant.components")
_ns(
    "homeassistant.components.sensor",
    SensorDeviceClass=type("SensorDeviceClass", (), {"TEMPERATURE": "temperature", "BATTERY": "battery", "TIMESTAMP": "timestamp", "VOLTAGE": "voltage"}),
    SensorEntity=type("SensorEntity", (), {}),
    SensorStateClass=type("SensorStateClass", (), {"MEASUREMENT": "measurement"}),
)
_ns("homeassistant.config_entries", ConfigEntry=type("ConfigEntry", (), {}))
_ns(
    "homeassistant.const",
    PERCENTAGE="%",
    EntityCategory=type("EntityCategory", (), {"DIAGNOSTIC": "diagnostic", "CONFIG": "config"}),
    UnitOfElectricPotential=type("U", (), {"VOLT": "V"}),
    UnitOfTemperature=type("UnitOfTemperature", (), {"CELSIUS": "°C", "FAHRENHEIT": "°F"}),
)

class _FakeHass:
    pass

def _callback(f):
    return f

_ns(
    "homeassistant.core",
    HomeAssistant=_FakeHass,
    callback=_callback,
)
_ns("homeassistant.helpers")
_ns(
    "homeassistant.helpers.device_registry",
    DeviceInfo=lambda **kw: kw,
)
_ns(
    "homeassistant.helpers.entity_platform",
    AddEntitiesCallback=callable,
)


class _FakeCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    def __class_getitem__(cls, _item):
        return cls

    @property
    def available(self) -> bool:
        return getattr(self.coordinator, "last_update_success", True)


_ns(
    "homeassistant.helpers.update_coordinator",
    CoordinatorEntity=_FakeCoordinatorEntity,
)

# const module
pkg = types.ModuleType("custom_components")
pkg.__path__ = [str(ROOT / "custom_components")]
sys.modules["custom_components"] = pkg
fbpkg = types.ModuleType("custom_components.fireboard")
fbpkg.__path__ = [str(PKG_DIR)]
sys.modules["custom_components.fireboard"] = fbpkg


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# coordinator imports homeassistant.exceptions
_ns(
    "homeassistant.exceptions",
    ConfigEntryAuthFailed=type("ConfigEntryAuthFailed", (Exception,), {}),
)
_ns(
    "homeassistant.helpers.update_coordinator",
    CoordinatorEntity=_FakeCoordinatorEntity,
    DataUpdateCoordinator=type("DataUpdateCoordinator", (), {"__init_subclass__": lambda cls, **kw: None, "__class_getitem__": lambda c, *a: c}),
    UpdateFailed=type("UpdateFailed", (Exception,), {}),
)

_load("custom_components.fireboard.const", PKG_DIR / "const.py")
_load("custom_components.fireboard.api", PKG_DIR / "api.py")
_load("custom_components.fireboard.coordinator", PKG_DIR / "coordinator.py")
sensor = _load("custom_components.fireboard.sensor", PKG_DIR / "sensor.py")


def _device_with_pit(latest_temps, channel_meta, last_templog):
    """Synthesize a Fireboard device dict."""
    return {
        "uuid": "abc",
        "title": "Smokey",
        "last_templog": last_templog,
        "latest_temps": latest_temps,
        "channels": [channel_meta],
        "degreetype": 2,
    }


def _make_sensor(device):
    coord = MagicMock()
    coord.data = MagicMock()
    coord.data.devices = {"abc": device}
    coord.data.active_session_by_device = {}
    coord.last_update_success = True
    coord.last_exception = None
    s = sensor.FireboardChannelSensor(coord, "abc", 3)
    return s


class TestAvailability(unittest.TestCase):
    def test_latest_temps_has_channel(self):
        """Happy path: live reading in latest_temps."""
        d = _device_with_pit(
            latest_temps=[{"channel": 3, "temp": 250.4, "degreetype": 2}],
            channel_meta={"channel": 3, "enabled": True, "channel_label": "Pit"},
            last_templog=datetime.now(timezone.utc).isoformat(),
        )
        s = _make_sensor(d)
        self.assertTrue(s.available)
        self.assertEqual(s.native_value, 250.4)

    def test_latest_temps_drops_but_channel_last_templog_present(self):
        """The fix: latest_temps empty but channel has cached reading."""
        d = _device_with_pit(
            latest_temps=[],  # Fireboard dropped it this cycle
            channel_meta={
                "channel": 3,
                "enabled": True,
                "channel_label": "Pit",
                "last_templog": {
                    "channel": 3,
                    "temp": 251.2,
                    "degreetype": 2,
                    "created": datetime.now(timezone.utc).isoformat(),
                },
                "current_temp": 251.2,
            },
            last_templog=datetime.now(timezone.utc).isoformat(),
        )
        s = _make_sensor(d)
        self.assertTrue(s.available, "Should stay available via channel.last_templog fallback")
        self.assertEqual(s.native_value, 251.2)

    def test_current_temp_only(self):
        """Fallback to current_temp when last_templog dict is missing."""
        d = _device_with_pit(
            latest_temps=[],
            channel_meta={
                "channel": 3,
                "enabled": True,
                "channel_label": "Pit",
                "current_temp": 248.7,
            },
            last_templog=datetime.now(timezone.utc).isoformat(),
        )
        s = _make_sensor(d)
        self.assertTrue(s.available)
        self.assertEqual(s.native_value, 248.7)

    def test_staleness_gate(self):
        """Old device.last_templog → mark unavailable even if channel has data."""
        old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        d = _device_with_pit(
            latest_temps=[],
            channel_meta={
                "channel": 3,
                "enabled": True,
                "channel_label": "Pit",
                "last_templog": {
                    "channel": 3,
                    "temp": 250.0,
                    "degreetype": 2,
                    "created": old,
                },
                "current_temp": 250.0,
            },
            last_templog=old,
        )
        s = _make_sensor(d)
        self.assertFalse(s.available, "Device hasn't reported in 10 min — should be unavailable")

    def test_no_data_anywhere(self):
        """No reading available, even in channel meta — channel is unavailable."""
        d = _device_with_pit(
            latest_temps=[],
            channel_meta={"channel": 3, "enabled": True, "channel_label": "Pit"},
            last_templog=datetime.now(timezone.utc).isoformat(),
        )
        s = _make_sensor(d)
        self.assertFalse(s.available)

    def test_availability_survives_failed_refresh(self):
        """Coordinator update fails (rate limit) — entity stays available with cached data."""
        d = _device_with_pit(
            latest_temps=[{"channel": 3, "temp": 250.4, "degreetype": 2}],
            channel_meta={"channel": 3, "enabled": True, "channel_label": "Pit"},
            last_templog=datetime.now(timezone.utc).isoformat(),
        )
        s = _make_sensor(d)
        s.coordinator.last_update_success = False  # simulate rate limit
        s.coordinator.last_exception = Exception("Fireboard API rate limit hit (17 / 5 min)")
        self.assertTrue(s.available, "Stale-but-cached should remain available")


if __name__ == "__main__":
    unittest.main(verbosity=2)
