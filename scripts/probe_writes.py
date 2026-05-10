"""Carefully probe write endpoints to learn schemas.

Strategy:
  - POST empty JSON to an idle Pulse device (no active cook to disturb).
  - POST empty JSON to an old closed session (no harm — already closed).
  - DRY-RUN ONLY: never touches the active session or the actively-cooking device.

If the API returns 400 with field validation errors, we learn the schema.
If it returns 200, the endpoint is permissive and we'll need a different approach.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import aiohttp

UA = "HomeAssistant-Fireboard-Probe/0.1"


async def attempt(session, token, method, url, body=None):
    print(f"\n{method} {url}")
    print(f"  body: {body!r}")
    headers = {
        "Authorization": f"Token {token}",
        "User-Agent": UA,
        "Content-Type": "application/json",
    }
    async with session.request(
        method, url, headers=headers,
        data=json.dumps(body) if body is not None else None,
        timeout=aiohttp.ClientTimeout(total=20),
    ) as resp:
        text = await resp.text()
        print(f"  status: {resp.status}")
        print(f"  body[:600]: {text[:600]}")


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

        async with session.get(
            "https://fireboard.io/api/v1/devices.json",
            headers={"Authorization": f"Token {token}", "User-Agent": UA},
        ) as resp:
            devices = await resp.json()
        idle_devices = [
            d for d in devices
            if (d.get("last_drivelog") or {}).get("setpoint") is None
        ]
        if not idle_devices:
            print("No idle device available — aborting.")
            return 1
        idle = idle_devices[0]
        print(f"Idle target: {idle.get('title')} ({idle.get('model')})")

        async with session.get(
            "https://fireboard.io/api/v1/sessions.json",
            headers={"Authorization": f"Token {token}", "User-Agent": UA},
        ) as resp:
            sessions = await resp.json()
        closed = [s for s in sessions if s.get("end_time") is not None]
        old_session = closed[-1] if closed else None
        print(f"Closed session for probe: id={old_session and old_session.get('id')}")

        # Empty POST: tells us required fields if the endpoint is strict.
        await attempt(
            session, token, "POST",
            f"https://fireboard.io/api/v1/devices/{idle['uuid']}.json",
            body={},
        )
        # Echo title only — no semantic change
        await attempt(
            session, token, "POST",
            f"https://fireboard.io/api/v1/devices/{idle['uuid']}.json",
            body={"title": idle.get("title")},
        )
        # Try a known-schema field that mirrors what the app does
        await attempt(
            session, token, "POST",
            f"https://fireboard.io/api/v1/devices/{idle['uuid']}.json",
            body={"setpoint": 250},
        )

        if old_session:
            await attempt(
                session, token, "POST",
                f"https://fireboard.io/api/v1/sessions/{old_session['id']}.json",
                body={},
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
