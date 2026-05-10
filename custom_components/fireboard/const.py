"""Constants for the Fireboard integration."""
from __future__ import annotations

DOMAIN = "fireboard"

CONF_TOKEN = "token"

DEFAULT_SCAN_INTERVAL = 60

API_BASE = "https://fireboard.io/api"
AUTH_URL = f"{API_BASE}/rest-auth/login/"
DEVICES_URL = f"{API_BASE}/v1/devices.json"
SESSIONS_URL = f"{API_BASE}/v1/sessions.json"

USER_AGENT = "HomeAssistant-Fireboard/0.1.0"

DEGREETYPE_CELSIUS = 1
DEGREETYPE_FAHRENHEIT = 2

CARD_VERSION = "0.2.0"
CARD_BASE_URL = "/fireboard_static"
CARD_URL_PATH = f"{CARD_BASE_URL}/fireboard-card.js"
CARD_URL_VERSIONED = f"{CARD_URL_PATH}?v={CARD_VERSION}"
