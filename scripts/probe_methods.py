"""Discover allowed HTTP methods on Fireboard endpoints (read-only OPTIONS)."""
from __future__ import annotations

import asyncio
import json
import os
import sys

import aiohttp

UA = "HomeAssistant-Fireboard-Probe/0.1"


async def probe(session, token, path):
    url = f"https://fireboard.io/api/v1{path}"
    print(f"\nOPTIONS {path}")
    async with session.options(
        url,
        headers={"Authorization": f"Token {token}", "User-Agent": UA},
        timeout=aiohttp.ClientTimeout(total=20),
    ) as resp:
        print(f"  status: {resp.status}")
        print(f"  Allow: {resp.headers.get('Allow')}")
        # Some Django REST endpoints return method/schema info in the body
        body = await resp.text()
        if body:
            try:
                data = json.loads(body)
                if isinstance(data, dict):
                    keys = sorted(data.keys())
                    print(f"  body keys: {keys}")
                    if "actions" in data:
                        for action, fields in data["actions"].items():
                            print(f"  ACTION {action}: fields={list(fields.keys()) if isinstance(fields, dict) else fields}")
                    if "renders" in data:
                        print(f"  renders: {data['renders']}")
                    if "parses" in data:
                        print(f"  parses: {data['parses']}")
            except Exception:
                print(f"  body[:200]: {body[:200]}")


async def main() -> int:
    user = os.environ.get("FIREBOARD_USERNAME")
    pw = os.environ.get("FIREBOARD_PASSWORD")
    if not user or not pw:
        print("env vars not set", file=sys.stderr)
        return 2

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://fireboard.io/api/rest-auth/login/",
            json={"username": user, "password": pw},
            headers={"User-Agent": UA},
        ) as resp:
            token = (await resp.json())["key"]

        # First grab one active session id and one device uuid
        async with session.get(
            "https://fireboard.io/api/v1/sessions.json",
            headers={"Authorization": f"Token {token}", "User-Agent": UA},
        ) as resp:
            sessions = await resp.json()
        active = next((s for s in sessions if s.get("end_time") is None), None)
        async with session.get(
            "https://fireboard.io/api/v1/devices.json",
            headers={"Authorization": f"Token {token}", "User-Agent": UA},
        ) as resp:
            devices = await resp.json()
        dev_with_drive = next(
            (d for d in devices if (d.get("last_drivelog") or {}).get("setpoint") is not None),
            None,
        )

        print(f"Active session id: {active.get('id') if active else None}")
        print(f"Drive device uuid: {dev_with_drive.get('uuid') if dev_with_drive else None}")

        paths = [
            "/sessions.json",
            "/devices.json",
        ]
        if active:
            paths.append(f"/sessions/{active['id']}.json")
        if dev_with_drive:
            uuid = dev_with_drive["uuid"]
            paths.extend(
                [
                    f"/devices/{uuid}.json",
                    f"/devices/{uuid}/drivelog.json",
                    f"/devices/{uuid}/drive.json",
                    f"/devices/{uuid}/drive/",
                    f"/devices/{uuid}/temps.json",
                ]
            )

        for path in paths:
            await probe(session, token, path)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
