"""Live smoke test for the Fireboard API.

Reads FIREBOARD_USERNAME / FIREBOARD_PASSWORD from env (or prompts), logs in,
fetches /api/v1/devices.json, and prints the parts the integration relies on
plus a redacted dump of the raw payload.

Run:
    set FIREBOARD_USERNAME=you@example.com
    set FIREBOARD_PASSWORD=...
    python scripts\test_api.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from getpass import getpass
from pathlib import Path

# Load just api.py + const.py from the integration without triggering
# fireboard/__init__.py (which depends on homeassistant). We synthesize a
# bare 'fireboard' package so the api module's `from .const import ...`
# resolves against const.py loaded in isolation.
ROOT = Path(__file__).resolve().parents[1]

import importlib.util  # noqa: E402
import types  # noqa: E402

import aiohttp  # noqa: E402

_pkg_dir = ROOT / "custom_components" / "fireboard"
_pkg = types.ModuleType("fireboard")
_pkg.__path__ = [str(_pkg_dir)]
sys.modules["fireboard"] = _pkg


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("fireboard.const", _pkg_dir / "const.py")
_api = _load("fireboard.api", _pkg_dir / "api.py")

FireboardApiError = _api.FireboardApiError
FireboardAuthError = _api.FireboardAuthError
FireboardClient = _api.FireboardClient
FireboardRateLimitError = _api.FireboardRateLimitError

REDACT_KEYS = {"uuid", "hardware_id", "id", "owner", "user", "email"}


def redact(value, depth: int = 0):
    """Redact identifiers but keep field names + value types intact."""
    if isinstance(value, dict):
        return {
            k: ("<redacted>" if k.lower() in REDACT_KEYS else redact(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v, depth + 1) for v in value]
    return value


def _load_creds_file() -> dict[str, str]:
    """Read scripts/.creds (KEY=VALUE per line) if present. Gitignored."""
    path = ROOT / "scripts" / ".creds"
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


async def main() -> int:
    file_creds = _load_creds_file()
    username = (
        os.environ.get("FIREBOARD_USERNAME")
        or file_creds.get("FIREBOARD_USERNAME")
        or input("Fireboard username: ").strip()
    )
    password = (
        os.environ.get("FIREBOARD_PASSWORD")
        or file_creds.get("FIREBOARD_PASSWORD")
        or getpass("Fireboard password: ")
    )

    if not username or not password:
        print("Username and password are required.", file=sys.stderr)
        return 2

    async with aiohttp.ClientSession() as session:
        client = FireboardClient(session)

        print(f"\n[1] POST /api/rest-auth/login/  user={username}")
        try:
            token = await client.login(username, password)
        except FireboardAuthError as err:
            print(f"    AUTH FAILED: {err}")
            return 1
        except FireboardApiError as err:
            print(f"    NETWORK FAILED: {err}")
            return 1
        print(f"    OK — token length={len(token)} (prefix={token[:6]}...)")

        print("\n[2] GET  /api/v1/devices.json")
        try:
            devices = await client.async_get_devices()
        except FireboardAuthError as err:
            print(f"    AUTH FAILED: {err}")
            return 1
        except FireboardRateLimitError as err:
            print(f"    RATE LIMITED: {err}")
            return 1
        except FireboardApiError as err:
            print(f"    NETWORK FAILED: {err}")
            return 1

        print(f"    OK — {len(devices)} device(s)")

        for i, dev in enumerate(devices):
            print(f"\n--- Device {i} ---")
            print(f"  title         : {dev.get('title')!r}")
            print(f"  model         : {dev.get('model')!r}")
            print(f"  degreetype    : {dev.get('degreetype')!r}")
            print(f"  last_templog  : {dev.get('last_templog')!r}")
            channels = dev.get("channels") or []
            print(f"  channels      : {len(channels)}")
            for c in channels:
                label = c.get("channel_label") or c.get("name")
                print(f"    ch {c.get('channel')}: label={label!r} keys={sorted(c.keys())}")
            temps = dev.get("latest_temps") or []
            print(f"  latest_temps  : {len(temps)}")
            for t in temps:
                print(
                    f"    ch {t.get('channel')}: temp={t.get('temp')!r} "
                    f"degreetype={t.get('degreetype')!r} keys={sorted(t.keys())}"
                )
            log = dev.get("device_log") or {}
            if log:
                print(f"  device_log keys: {sorted(log.keys())}")
                for k in ("vBatt", "vBattPer", "battery"):
                    if k in log:
                        print(f"    {k} = {log[k]!r}")

        # Dump the redacted payload so we can compare against integration assumptions.
        out = ROOT / "scripts" / "devices_sample.redacted.json"
        out.write_text(json.dumps(redact(devices), indent=2, default=str))
        print(f"\nWrote redacted sample to {out}")

        # Double-check fields the integration actually reads.
        print("\n[3] Field check vs integration expectations")
        problems = []
        for dev in devices:
            if "uuid" not in dev:
                problems.append("device.uuid missing")
            for c in dev.get("channels") or []:
                if "channel" not in c:
                    problems.append("channels[].channel missing")
                if "channel_label" not in c and "name" not in c:
                    problems.append(f"channel {c.get('channel')}: no channel_label/name")
            for t in dev.get("latest_temps") or []:
                if "channel" not in t:
                    problems.append("latest_temps[].channel missing")
                if "temp" not in t:
                    problems.append("latest_temps[].temp missing")
        if problems:
            print("    PROBLEMS:")
            for p in sorted(set(problems)):
                print(f"      - {p}")
            return 1
        print("    OK — every field the integration reads is present")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
