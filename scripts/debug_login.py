"""Print the raw response from the Fireboard login endpoint to diagnose 400s."""
from __future__ import annotations

import asyncio
import json
import os
import sys

import aiohttp

URL = "https://fireboard.io/api/rest-auth/login/"
UA = "HomeAssistant-Fireboard-Debug/0.1"


async def try_payload(session: aiohttp.ClientSession, label: str, payload: dict) -> None:
    print(f"\n[{label}] POST keys={sorted(payload.keys())}")
    async with session.post(
        URL,
        json=payload,
        headers={"User-Agent": UA, "Accept": "application/json"},
        timeout=aiohttp.ClientTimeout(total=20),
    ) as resp:
        body = await resp.text()
        print(f"  status: {resp.status}")
        print(f"  body  : {body[:800]}")
        try:
            data = json.loads(body)
            if isinstance(data, dict) and "key" in data:
                print(f"  --> token received (length {len(data['key'])})")
        except Exception:
            pass


async def main() -> int:
    user = os.environ.get("FIREBOARD_USERNAME")
    pw = os.environ.get("FIREBOARD_PASSWORD")
    if not user or not pw:
        print("env vars not set", file=sys.stderr)
        return 2

    print(f"username field value: {user!r} (length {len(user)})")
    print(f"password length     : {len(pw)}")

    async with aiohttp.ClientSession() as session:
        await try_payload(session, "username+password", {"username": user, "password": pw})
        await try_payload(session, "email+password",    {"email":    user, "password": pw})
        await try_payload(
            session,
            "username+email+password",
            {"username": user, "email": user, "password": pw},
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
