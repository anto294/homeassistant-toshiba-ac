"""Tests for the setup and reauthentication forms, on a real core."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.toshiba_ac_link.const import DOMAIN
from custom_components.toshiba_ac_link.toshiba.errors import (
    ToshibaAuthError,
    ToshibaBlockedError,
    ToshibaConnectionError,
)

from .conftest import ACCOUNT, LOGIN, async_add_entry, make_manager

LOGIN_CALL = "custom_components.toshiba_ac_link.config_flow.async_login"


def test_setup_stores_the_token_and_not_the_password(run_in_hass) -> None:
    """Setup needs no second login, and a stored password could be retried."""

    async def scenario(hass: HomeAssistant) -> None:
        entry = await async_add_entry(hass, make_manager())

        assert entry.unique_id == "consumer-1"
        assert entry.title == ACCOUNT["username"]
        assert entry.data["access_token"] == "access-token"
        assert entry.data["consumer_id"] == "consumer-1"
        assert "password" not in entry.data
        assert len(entry.data["device_id"]) == 16

    run_in_hass(scenario)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ToshibaAuthError("wrong password"), "invalid_auth"),
        (ToshibaBlockedError("slow down", 100), "too_many_attempts"),
        (ToshibaConnectionError("HTTP 500"), "cannot_connect"),
        (RuntimeError("surprise"), "unknown"),
    ],
)
def test_setup_shows_the_right_error(run_in_hass, error, code) -> None:
    """Each failure gets its own text."""

    async def scenario(hass: HomeAssistant) -> None:
        with patch(LOGIN_CALL, AsyncMock(side_effect=error)):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], dict(ACCOUNT)
            )
        assert result["type"] == "form"
        assert result["errors"] == {"base": code}

    run_in_hass(scenario)


def test_second_entry_on_one_account_is_refused(run_in_hass) -> None:
    """A second entry would double the requests of the same account."""

    async def scenario(hass: HomeAssistant) -> None:
        await async_add_entry(hass, make_manager())
        with patch(LOGIN_CALL, AsyncMock(return_value=LOGIN)):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], dict(ACCOUNT)
            )
        assert result["type"] == "abort"
        assert result["reason"] == "already_configured"

    run_in_hass(scenario)


def test_reauthentication_keeps_the_device_id(run_in_hass) -> None:
    """A new login must stay the same device for Toshiba."""

    async def scenario(hass: HomeAssistant) -> None:
        entry = await async_add_entry(hass, make_manager())
        device_id = entry.data["device_id"]

        with (
            patch(LOGIN_CALL, AsyncMock(return_value=LOGIN)) as login,
            patch(
                "custom_components.toshiba_ac_link.ToshibaAcDeviceManager",
                return_value=make_manager(),
            ),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "reauth", "entry_id": entry.entry_id},
                data=dict(entry.data),
            )
            assert result["step_id"] == "reauth_confirm"
            assert result["description_placeholders"]["username"] == ACCOUNT["username"]

            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"password": "new-secret"}
            )
            await hass.async_block_till_done()

        assert result["type"] == "abort"
        assert result["reason"] == "reauth_successful"
        assert entry.data["device_id"] == device_id
        assert "password" not in entry.data
        assert login.await_args.args[2:] == (
            ACCOUNT["username"],
            "new-secret",
            device_id,
        )

    run_in_hass(scenario)
