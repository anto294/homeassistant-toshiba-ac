"""Config flow for the Toshiba Link integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import secrets
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import probatio

from .const import DOMAIN
from .toshiba.api import async_login
from .toshiba.errors import ToshibaAuthError, ToshibaBlockedError, ToshibaError
from .toshiba.guard import async_get_guard

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

STEP_USER_DATA_SCHEMA = probatio.Schema(
    {
        probatio.Required("username"): TextSelector(),
        probatio.Required("password"): PASSWORD_SELECTOR,
    }
)
STEP_REAUTH_DATA_SCHEMA = probatio.Schema(
    {probatio.Required("password"): PASSWORD_SELECTOR}
)


# ---------------------------
#   ToshibaLinkConfigFlow
# ---------------------------
class ToshibaLinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Toshiba Link."""

    VERSION = 1

    async def _async_try_input(
        self, username: str, password: str, device_id: str | None = None
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """Log in once, and return the data of an entry or the errors for the form.

        The password is not stored. When the token stops working, in about two
        years, Home Assistant asks for it again, so nothing can repeat a login.
        The forms of an existing entry pass device_id, to stay the same device for Toshiba.
        """
        device_id = device_id or secrets.token_hex(8)
        try:
            login = await async_login(
                async_get_clientsession(self.hass),
                await async_get_guard(self.hass),
                username,
                password,
                device_id,
            )
        except ToshibaBlockedError as ex:
            _LOGGER.warning("Login not attempted: %s", ex)
            return None, {"base": "too_many_attempts"}
        except ToshibaAuthError:
            return None, {"base": "invalid_auth"}
        except ToshibaError as ex:
            _LOGGER.error("Toshiba connection error: %s", ex)
            return None, {"base": "cannot_connect"}
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            return None, {"base": "unknown"}
        return {
            "username": username,
            "device_id": device_id,
            "access_token": login.access_token,
            "access_token_type": login.token_type,
            "consumer_id": login.consumer_id,
        }, {}

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
                # A second entry would double the requests of the same account
                await self.async_set_unique_id(data["consumer_id"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=user_input["username"], data=data)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, _entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the stored token stopped working."""
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

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Correct the username and password of the same account, from the entry's menu.

        Only a login can tell which account this is. The answer of another account
        is dropped here, with its token, so nothing of the entry changes.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        username = entry.data["username"]

        if user_input is not None:
            username = user_input["username"]
            data, errors = await self._async_try_input(
                username, user_input["password"], entry.data.get("device_id")
            )
            if data is not None:
                # The login is already spent, so another open form must not waste it
                await self.async_set_unique_id(
                    data["consumer_id"], raise_on_progress=False
                )
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, title=username, data=data
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, {"username": username}
            ),
            errors=errors,
        )
