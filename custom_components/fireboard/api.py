"""Thin async client for the Fireboard Cloud REST API."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .const import AUTH_URL, DEVICES_URL, SESSIONS_URL, USER_AGENT

_LOGGER = logging.getLogger(__name__)

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class FireboardAuthError(Exception):
    """Raised when credentials or the saved token are rejected."""


class FireboardRateLimitError(Exception):
    """Raised when the API has rate-limited us (>17 calls / 5 min)."""


class FireboardApiError(Exception):
    """Raised for any other transport or server error."""


class FireboardClient:
    """Minimal Fireboard API client.

    The Fireboard cloud limits callers to 17 requests per 5-minute window,
    so the coordinator is responsible for keeping the call rate down.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token: str | None = None,
    ) -> None:
        self._session = session
        self._token = token

    @property
    def token(self) -> str | None:
        return self._token

    async def login(self, username: str, password: str) -> str:
        """Exchange username/password for a long-lived API token."""
        try:
            async with self._session.post(
                AUTH_URL,
                json={"username": username, "password": password},
                headers={"User-Agent": USER_AGENT},
                timeout=_REQUEST_TIMEOUT,
            ) as resp:
                if resp.status in (400, 401, 403):
                    raise FireboardAuthError(
                        f"Login rejected by Fireboard ({resp.status})"
                    )
                resp.raise_for_status()
                data = await resp.json()
        except asyncio.TimeoutError as err:
            raise FireboardApiError("Timed out talking to Fireboard") from err
        except aiohttp.ClientError as err:
            raise FireboardApiError(f"Login request failed: {err}") from err

        token = data.get("key")
        if not token:
            raise FireboardAuthError("Fireboard returned no token")
        self._token = token
        return token

    async def _get_list(self, url: str, what: str) -> list[dict[str, Any]]:
        if not self._token:
            raise FireboardAuthError("No Fireboard token configured")

        headers = {
            "Authorization": f"Token {self._token}",
            "User-Agent": USER_AGENT,
        }
        try:
            async with self._session.get(
                url, headers=headers, timeout=_REQUEST_TIMEOUT
            ) as resp:
                if resp.status == 401:
                    raise FireboardAuthError("Fireboard token rejected")
                if resp.status == 429:
                    raise FireboardRateLimitError(
                        "Fireboard API rate limit hit (17 / 5 min)"
                    )
                resp.raise_for_status()
                payload = await resp.json()
        except asyncio.TimeoutError as err:
            raise FireboardApiError(f"Timed out fetching {what}") from err
        except aiohttp.ClientError as err:
            raise FireboardApiError(f"{what} request failed: {err}") from err

        if not isinstance(payload, list):
            raise FireboardApiError(
                f"Unexpected {what} payload: {type(payload).__name__}"
            )
        return payload

    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Return the full device list with inline latest_temps."""
        return await self._get_list(DEVICES_URL, "devices")

    async def async_get_sessions(self) -> list[dict[str, Any]]:
        """Return all sessions; active ones have end_time == null."""
        return await self._get_list(SESSIONS_URL, "sessions")

    async def async_delete_session(self, session_id: int) -> None:
        """End an active cook session.

        OPTIONS confirms /sessions/<id>.json allows DELETE. The Fireboard app
        uses the same call to stop a session in progress.
        """
        if not self._token:
            raise FireboardAuthError("No Fireboard token configured")
        url = f"{API_BASE}/v1/sessions/{session_id}.json"
        headers = {
            "Authorization": f"Token {self._token}",
            "User-Agent": USER_AGENT,
        }
        try:
            async with self._session.delete(
                url, headers=headers, timeout=_REQUEST_TIMEOUT
            ) as resp:
                if resp.status == 401:
                    raise FireboardAuthError("Fireboard token rejected")
                if resp.status == 429:
                    raise FireboardRateLimitError(
                        "Fireboard API rate limit hit (17 / 5 min)"
                    )
                if resp.status >= 400:
                    body = await resp.text()
                    raise FireboardApiError(
                        f"DELETE session {session_id} → {resp.status}: {body[:200]}"
                    )
        except asyncio.TimeoutError as err:
            raise FireboardApiError("Timed out ending session") from err
        except aiohttp.ClientError as err:
            raise FireboardApiError(f"End-session request failed: {err}") from err

    async def async_post_device(
        self, uuid: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """POST a partial device update.

        OPTIONS confirms /devices/<uuid>.json allows POST. The body schema
        isn't documented; the Fireboard app uses POST with the field(s) it
        wants to change. Used for setpoint and similar adjustments.
        """
        if not self._token:
            raise FireboardAuthError("No Fireboard token configured")
        url = f"{API_BASE}/v1/devices/{uuid}.json"
        headers = {
            "Authorization": f"Token {self._token}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
        }
        try:
            async with self._session.post(
                url, headers=headers, json=payload, timeout=_REQUEST_TIMEOUT
            ) as resp:
                if resp.status == 401:
                    raise FireboardAuthError("Fireboard token rejected")
                if resp.status == 429:
                    raise FireboardRateLimitError(
                        "Fireboard API rate limit hit (17 / 5 min)"
                    )
                if resp.status >= 400:
                    body = await resp.text()
                    raise FireboardApiError(
                        f"POST device {uuid} → {resp.status}: {body[:200]}"
                    )
                try:
                    return await resp.json()
                except aiohttp.ContentTypeError:
                    return {}
        except asyncio.TimeoutError as err:
            raise FireboardApiError("Timed out updating device") from err
        except aiohttp.ClientError as err:
            raise FireboardApiError(f"Device update request failed: {err}") from err
