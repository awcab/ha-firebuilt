"""Config and reauth flow for Fireboard."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FireboardApiError, FireboardAuthError, FireboardClient
from .const import CONF_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


async def _async_login(hass, username: str, password: str) -> str:
    client = FireboardClient(async_get_clientsession(hass))
    return await client.login(username, password)


class FireboardConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Fireboard."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()
            try:
                token = await _async_login(
                    self.hass, username, user_input[CONF_PASSWORD]
                )
            except FireboardAuthError:
                errors["base"] = "invalid_auth"
            except FireboardApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during Fireboard login")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=username,
                    data={CONF_USERNAME: username, CONF_TOKEN: token},
                )

        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> FlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        assert self._reauth_entry is not None
        errors: dict[str, str] = {}
        username = self._reauth_entry.data[CONF_USERNAME]

        if user_input is not None:
            try:
                token = await _async_login(
                    self.hass, username, user_input[CONF_PASSWORD]
                )
            except FireboardAuthError:
                errors["base"] = "invalid_auth"
            except FireboardApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during Fireboard reauth")
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={**self._reauth_entry.data, CONF_TOKEN: token},
                )
                await self.hass.config_entries.async_reload(
                    self._reauth_entry.entry_id
                )
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"username": username},
        )
