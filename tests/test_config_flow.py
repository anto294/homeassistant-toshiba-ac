"""Tests for the setup and reauthentication forms, on a real core."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.toshiba_ac_link.const import DOMAIN
from custom_components.toshiba_ac_link.toshiba.api import LoginResult
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


RECONFIGURED = {"username": "new@example.com", "password": "new-secret"}


async def start_reconfigure(hass: HomeAssistant, entry) -> dict:
    """Open the form from the menu of the entry."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )


def suggested_username(result: dict) -> str:
    """Return the username that the form shows."""
    field = next(key for key in result["data_schema"].schema if key == "username")
    return field.description["suggested_value"]


def test_reconfiguring_updates_the_entry_with_one_login(run_in_hass) -> None:
    """A corrected username reaches the data and the title, and stays the same device."""

    async def scenario(hass: HomeAssistant) -> None:
        entry = await async_add_entry(hass, make_manager())
        device_id = entry.data["device_id"]
        answer = LoginResult("new-token", "Bearer", "consumer-1")

        with (
            patch(LOGIN_CALL, AsyncMock(return_value=answer)) as login,
            patch(
                "custom_components.toshiba_ac_link.ToshibaAcDeviceManager",
                return_value=make_manager(),
            ) as manager,
        ):
            result = await start_reconfigure(hass, entry)
            assert result["type"] == "form"
            assert result["step_id"] == "reconfigure"
            assert suggested_username(result) == ACCOUNT["username"]
            login.assert_not_awaited()

            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], dict(RECONFIGURED)
            )
            await hass.async_block_till_done()

        assert result["type"] == "abort"
        assert result["reason"] == "reconfigure_successful"
        login.assert_awaited_once()
        assert login.await_args.args[2:] == (
            "new@example.com",
            "new-secret",
            device_id,
        )
        assert entry.title == "new@example.com"
        assert entry.unique_id == "consumer-1"
        assert entry.data == {
            "username": "new@example.com",
            "device_id": device_id,
            "access_token": "new-token",
            "access_token_type": "Bearer",
            "consumer_id": "consumer-1",
        }
        manager.assert_called_once()

    run_in_hass(scenario)


def test_reconfiguring_refuses_another_account_and_keeps_nothing(run_in_hass) -> None:
    """Its token must not replace the one of this entry, or its units would mix."""

    async def scenario(hass: HomeAssistant) -> None:
        entry = await async_add_entry(hass, make_manager())
        before = dict(entry.data)
        answer = LoginResult("other-token", "Bearer", "consumer-2")

        with (
            patch(LOGIN_CALL, AsyncMock(return_value=answer)),
            patch(
                "custom_components.toshiba_ac_link.ToshibaAcDeviceManager"
            ) as manager,
        ):
            result = await start_reconfigure(hass, entry)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], dict(RECONFIGURED)
            )
            await hass.async_block_till_done()

        assert result["type"] == "abort"
        assert result["reason"] == "wrong_account"
        assert dict(entry.data) == before
        assert entry.title == ACCOUNT["username"]
        assert entry.unique_id == "consumer-1"
        manager.assert_not_called()

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
def test_reconfiguring_shows_errors_and_changes_nothing(
    run_in_hass, error, code
) -> None:
    """The form stays open with what the user typed, and the entry stays as it was."""

    async def scenario(hass: HomeAssistant) -> None:
        entry = await async_add_entry(hass, make_manager())
        before = dict(entry.data)

        with patch(LOGIN_CALL, AsyncMock(side_effect=error)):
            result = await start_reconfigure(hass, entry)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], dict(RECONFIGURED)
            )

        assert result["type"] == "form"
        assert result["step_id"] == "reconfigure"
        assert result["errors"] == {"base": code}
        assert suggested_username(result) == "new@example.com"
        assert dict(entry.data) == before
        assert entry.title == ACCOUNT["username"]

    run_in_hass(scenario)
