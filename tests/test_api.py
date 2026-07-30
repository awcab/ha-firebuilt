import pytest
import aiohttp

from custom_components.fireboard.api import FireboardClient
from custom_components.fireboard.const import API_BASE


class DummyResp:
    def __init__(self, status=200, json_data=None, text_data=""):
        self.status = status
        self._json = json_data or {}
        self._text = text_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._json

    async def text(self):
        return self._text

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(None, None, status=self.status)


class DummySession:
    def __init__(self):
        self.last = {}

    def delete(self, url, headers=None, timeout=None):
        self.last = {"method": "delete", "url": url, "headers": headers}
        return DummyResp(status=204)

    def post(self, url, headers=None, json=None, timeout=None):
        self.last = {"method": "post", "url": url, "headers": headers, "json": json}
        return DummyResp(status=200, json_data={})


@pytest.mark.asyncio
async def test_delete_session_uses_api_base():
    session = DummySession()
    client = FireboardClient(session, token="fake-token")

    await client.async_delete_session(123)

    expected = f"{API_BASE}/v1/sessions/123.json"
    assert session.last["url"] == expected
    assert session.last["method"] == "delete"


@pytest.mark.asyncio
async def test_post_device_uses_api_base_and_payload():
    session = DummySession()
    client = FireboardClient(session, token="fake-token")

    payload = {"setpoint": 200}
    await client.async_post_device("uuid-abc", payload)

    expected = f"{API_BASE}/v1/devices/uuid-abc.json"
    assert session.last["url"] == expected
    assert session.last["method"] == "post"
    assert session.last["json"] == payload
