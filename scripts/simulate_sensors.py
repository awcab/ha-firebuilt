"""Hit the live Fireboard API and dry-run the integration's value derivation.

Doesn't require Home Assistant — only loads api.py + const.py and replicates
the same logic the sensors use, so we can verify what each entity would
report. Useful as a pre-deploy smoke test.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "custom_components" / "fireboard"

_pkg = types.ModuleType("fireboard")
_pkg.__path__ = [str(PKG_DIR)]
sys.modules["fireboard"] = _pkg


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("fireboard.const", PKG_DIR / "const.py")
api = _load("fireboard.api", PKG_DIR / "api.py")


# ---- Replicate sensor logic without importing homeassistant -----------------


def _battery(device):
    raw = device.get("last_battery_reading")
    if raw is None:
        log = device.get("device_log") or {}
        raw = log.get("vBattPer")
    if raw is None:
        return None
    pct = float(raw)
    if 0 <= pct <= 1:
        pct *= 100
    return round(pct, 1)


def _clean_label(raw):
    if not raw:
        return None
    if "(null)" in raw.lower():
        return None
    return raw.strip() or None


def _parse_iso(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _alert_state(channel, latest_temp):
    if latest_temp is None or not channel.get("alerts"):
        return None
    for alert in channel["alerts"]:
        if alert.get("enabled") is False:
            continue
        tmin = alert.get("temp_min")
        tmax = alert.get("temp_max")
        if tmin is not None and latest_temp < tmin:
            return True
        if tmax is not None and latest_temp > tmax:
            return True
    return False


def _build_active_session_map(sessions):
    out = {}
    for s in sessions:
        if s.get("end_time") is not None:
            continue
        for device_uuid in s.get("device_ids") or []:
            existing = out.get(device_uuid)
            if not existing or (s.get("start_time") or "") > (
                existing.get("start_time") or ""
            ):
                out[device_uuid] = s
    return out


# ---- Driver ----------------------------------------------------------------


async def main() -> int:
    user = os.environ.get("FIREBOARD_USERNAME")
    pw = os.environ.get("FIREBOARD_PASSWORD")
    if not user or not pw:
        print("env vars not set", file=sys.stderr)
        return 2

    async with aiohttp.ClientSession() as session:
        client = api.FireboardClient(session)
        await client.login(user, pw)
        devices = await client.async_get_devices()
        sessions = await client.async_get_sessions()

    active_map = _build_active_session_map(sessions)
    now = datetime.now(timezone.utc)

    for d in devices:
        uuid = d["uuid"]
        title = d.get("title")
        print(f"\n=== {title} ({d.get('model_name') or d.get('model')}) ===")

        # Channel sensors
        latest_by_ch = {t["channel"]: t for t in d.get("latest_temps") or []}
        for ch in d.get("channels") or []:
            if ch.get("enabled") is False:
                print(f"  [skip] channel {ch.get('channel')} disabled")
                continue
            num = ch["channel"]
            label = _clean_label(ch.get("channel_label")) or f"Channel {num}"
            t = latest_by_ch.get(num)
            temp = t.get("temp") if t else None
            unit = (
                "°C" if (t or {}).get("degreetype") == 1
                else "°C" if d.get("degreetype") == 1
                else "°F"
            )
            available = temp is not None
            val = f"{temp}{unit}" if available else "unavailable"
            print(f"  sensor.{title}_{label}: {val}  (channel {num})")

            if ch.get("alerts"):
                a = _alert_state(ch, temp)
                a_str = "n/a" if a is None else ("ON" if a else "off")
                print(f"  binary_sensor.{title}_{label}_alert: {a_str}")

        # Battery
        bat = _battery(d)
        print(f"  sensor.{title}_battery: {bat if bat is not None else 'n/a'}%")

        # Last seen
        ls = _parse_iso(d.get("last_templog"))
        print(f"  sensor.{title}_last_seen: {ls}")

        # Drive
        log = d.get("last_drivelog") or {}
        if log.get("setpoint") is not None:
            unit = "°C" if log.get("degreetype") == 1 else "°F"
            print(f"  sensor.{title}_setpoint: {log['setpoint']}{unit}")
            if (dp := log.get("driveper")) is not None:
                print(f"  sensor.{title}_drive_output: {round(float(dp) * 100, 1)}%")
            if (vb := log.get("vbatt")) is not None:
                print(f"  sensor.{title}_drive_battery: {vb}V")
        else:
            print("  (no drive sensors — last_drivelog absent)")

        # Cook session
        s = active_map.get(uuid)
        if s:
            started = _parse_iso(s.get("start_time"))
            elapsed = (
                (now - started).total_seconds() if started else None
            )
            print(
                f"  sensor.{title}_cook_started: {started} "
                f"({elapsed/3600:.2f}h ago)" if elapsed else f"  cook_started: {started}"
            )
            print(f"  sensor.{title}_cook_session: {s.get('title')!r}  (id={s.get('id')})")
        else:
            print("  (no active session — cook sensors unavailable)")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
