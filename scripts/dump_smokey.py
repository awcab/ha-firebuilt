"""Dump Smokey's current device dict so we can see why channels are
showing unavailable and verify the fallback path will find data.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "custom_components" / "fireboard"

_pkg = types.ModuleType("fireboard")
_pkg.__path__ = [str(PKG_DIR)]
sys.modules["fireboard"] = _pkg


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("fireboard.const", PKG_DIR / "const.py")
api = _load("fireboard.api", PKG_DIR / "api.py")

REDACT_KEYS = {"uuid", "hardware_id", "id", "owner", "user", "email", "publicIP", "internalIP", "macAP", "macNIC", "bleClientMAC", "ssid", "deviceID", "boardID", "share_key"}


def redact(value):
    if isinstance(value, dict):
        return {k: ("<redacted>" if k.lower() in {x.lower() for x in REDACT_KEYS} else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


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

    for d in devices:
        title = d.get("title")
        if title != "Smokey":
            continue
        print(f"=== {title} ===")
        print(f"  last_templog: {d.get('last_templog')!r}")
        print(f"  latest_temps: {d.get('latest_temps')}")
        print(f"  last_drivelog: {bool(d.get('last_drivelog'))}")
        for ch in d.get("channels") or []:
            print(f"\n  ch{ch.get('channel')} ({ch.get('channel_label')}):")
            print(f"    enabled: {ch.get('enabled')}")
            print(f"    keys: {sorted(ch.keys())}")
            if "current_temp" in ch:
                print(f"    current_temp: {ch.get('current_temp')!r}")
            if "last_templog" in ch:
                print(f"    last_templog: {ch.get('last_templog')!r}")
        out = ROOT / "scripts" / "smokey_now.redacted.json"
        out.write_text(json.dumps(redact(d), indent=2, default=str))
        print(f"\nFull redacted dump -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
