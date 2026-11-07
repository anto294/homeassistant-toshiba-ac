"""Config flow for the Toshiba Link integration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
import secrets
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.exceptions import HomeAssistantError
from toshiba_ac_community.device_manager import ToshibaAcDeviceManager
from toshiba_ac_community.utils.http_api import (
    ToshibaAcHttpApiAuthError,
    ToshibaAcHttpApiError,
)
import voluptuous as vol

from .const import CONNECT_TIMEOUT_S, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("username"): str,
        vol.Required("password"): str,
    }
)
STEP_REAUTH_DATA_SCHEMA = vol.Schema({vol.Required("password"): str})


# ---------------------------
#   async_validate_input
# ---------------------------
async def async_validate_input(
    username: str, password: str, device_id: str | None = None
) -> dict[str, Any]:
    """Return the data of an entry, if the account can connect.

    Reauthentication passes device_id, to keep the device that Toshiba has already
    registered instead of adding another one to the account.
    """
    if device_id is None:
        device_id = secrets.token_hex(8)

    device_manager = ToshibaAcDeviceManager(username, password, device_id)

    try:
        await asyncio.wait_for(device_manager.connect(), timeout=CONNECT_TIMEOUT_S)
        # shutdown() clears http_api, so read the tokens before it. Storing them
        # means setup does not have to repeat this login.
        http_api = device_manager.http_api
        if http_api is None:
            raise CannotConnect
        access_token = http_api.access_token
        access_token_type = http_api.access_token_type
        consumer_id = http_api.consumer_id
    except TimeoutError as ex:
        _LOGGER.error("Toshiba connection timed out")
        raise CannotConnect from ex
    except ToshibaAcHttpApiAuthError as ex:
        _LOGGER.error("Toshiba rejected the login: %s", ex)
        raise InvalidAuth from ex
    except ToshibaAcHttpApiError as ex:
        _LOGGER.error("Toshiba connection error: %s", ex)
        raise CannotConnect from ex
    finally:
        await device_manager.shutdown()

    return {
        "username": username,
        "password": password,
        "device_id": device_id,
        "access_token": access_token,
        "access_token_type": access_token_type,
        "consumer_id": consumer_id,
    }


# ---------------------------
#   ToshibaLinkConfigFlow
# ---------------------------
class ToshibaLinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Toshiba Link."""

    VERSION = 1

    async def _async_try_input(
        self, username: str, password: str, device_id: str | None = None
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """Return the data of an entry, or the errors for the form."""
        try:
            data = await async_validate_input(username, password, device_id)
        except CannotConnect:
            return None, {"base": "cannot_connect"}
        except InvalidAuth:
            return None, {"base": "invalid_auth"}
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            return None, {"base": "unknown"}
        return data, {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the account, as in the Toshiba app."""
        errors: dict[str, str] = {}

        if user_input is not None:
            data, errors = await self._async_try_input(
                user_input["username"], user_input["password"]
            )
            if data is not None:
                # Toshiba allows one connection per account, so a second entry on
                # the same account could only fight with the first.
                await self.async_set_unique_id(data["consumer_id"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=user_input["username"], data=data)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, _entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the stored password stopped working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the current password of the same account."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            data, errors = await self._async_try_input(
                entry.data["username"],
                user_input["password"],
                entry.data.get("device_id"),
            )
            if data is not None:
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            description_placeholders={"username": entry.data["username"]},
            errors=errors,
        )


# ---------------------------
#   CannotConnect
# ---------------------------
class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


# ---------------------------
#   InvalidAuth
# ---------------------------
class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
