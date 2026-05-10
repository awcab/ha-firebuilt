"""Discover the Fireboard sessions endpoint and dump its shape."""
from __future__ import annotations

import asyncio
import json
import os
import sys

import aiohttp

API_BASE = "https://fireboard.io/api/v1"
UA = "HomeAssistant-Fireboard-Probe/0.1"

CANDIDATES = [
    "/sessions.json",
    "/sessions/active.json",
    "/sessions/?active=true",
]


async def probe(session: aiohttp.ClientSession, token: str, path: str) -> None:
    url = f"{API_BASE}{path}"
    print(f"\nGET {path}")
    async with session.get(
        url,
        headers={"Authorization": f"Token {token}", "User-Agent": UA},
        timeout=aiohttp.ClientTimeout(total=20),
    ) as resp:
        body = await resp.text()
        print(f"  status: {resp.status}")
        print(f"  bytes:  {len(body)}")
        if resp.status == 200 and body:
            try:
                data = json.loads(body)
                if isinstance(data, list):
                    print(f"  list of {len(data)}")
                    if data:
                        first = data[0]
                        print(f"  first keys: {sorted(first.keys()) if isinstance(first, dict) else type(first).__name__}")
                        print(f"  first    : {json.dumps(first, indent=2, default=str)[:1500]}")
                elif isinstance(data, dict):
                    print(f"  dict keys: {sorted(data.keys())}")
                    print(json.dumps(data, indent=2, default=str)[:1500])
            except Exception as err:
                print(f"  not JSON: {err}; first 200 chars: {body[:200]}")
        else:
            print(f"  body[:300]: {body[:300]}")


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
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            data = await resp.json()
            token = data["key"]
        print(f"Token acquired (length {len(token)})")

        for path in CANDIDATES:
            await probe(session, token, path)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
