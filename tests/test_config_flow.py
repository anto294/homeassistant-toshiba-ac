"""Tests for the setup and reauthentication forms, on a real core."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
import pytest
from toshiba_ac_community.utils.http_api import (
    ToshibaAcHttpApiAuthError,
    ToshibaAcHttpApiError,
)

from custom_components.toshiba_ac_link.const import DOMAIN

from .conftest import ACCOUNT, async_add_entry, make_manager

MANAGER = "custom_components.toshiba_ac_link.config_flow.ToshibaAcDeviceManager"


def test_setup_stores_the_tokens_and_no_sas_token(run_in_hass) -> None:
    """Setup must not repeat the login, and a SAS token is never reused."""

    async def scenario(hass: HomeAssistant) -> None:
        manager = make_manager()
        entry = await async_add_entry(hass, manager)

        assert entry.unique_id == "consumer-1"
        assert entry.title == ACCOUNT["username"]
        assert entry.data["access_token"] == "access-token"
        assert entry.data["consumer_id"] == "consumer-1"
        assert "sas_token" not in entry.data
        assert len(entry.data["device_id"]) == 16

    run_in_hass(scenario)


def test_tokens_are_read_before_the_shutdown(run_in_hass) -> None:
    """shutdown() clears http_api, so reading after it would crash every login."""

    async def scenario(hass: HomeAssistant) -> None:
        manager = make_manager()

        async def shutdown() -> None:
            manager.http_api = None

        manager.shutdown = AsyncMock(side_effect=shutdown)
        with (
            patch(MANAGER, return_value=manager),
            patch(
                "custom_components.toshiba_ac_link.ToshibaAcDeviceManager",
                return_value=make_manager(),
            ),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], dict(ACCOUNT)
            )
            await hass.async_block_till_done()
        assert result["type"] == "create_entry", result
        assert result["result"].data["access_token"] == "access-token"

    run_in_hass(scenario)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ToshibaAcHttpApiAuthError("wrong password"), "invalid_auth"),
        (ToshibaAcHttpApiError("HTTP 500"), "cannot_connect"),
        (TimeoutError(), "cannot_connect"),
        (RuntimeError("surprise"), "unknown"),
    ],
)
def test_setup_shows_the_right_error(run_in_hass, error, code) -> None:
    """Each failure gets its own text, and the connection is closed."""

    async def scenario(hass: HomeAssistant) -> None:
        manager = make_manager()
        manager.connect = AsyncMock(side_effect=error)
        with patch(MANAGER, return_value=manager):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], dict(ACCOUNT)
            )
        assert result["type"] == "form"
        assert result["errors"] == {"base": code}
        manager.shutdown.assert_awaited()

    run_in_hass(scenario)


def test_second_entry_on_one_account_is_refused(run_in_hass) -> None:
    """Toshiba allows one connection per account."""

    async def scenario(hass: HomeAssistant) -> None:
        manager = make_manager()
        await async_add_entry(hass, manager)
        with patch(MANAGER, return_value=make_manager()):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], dict(ACCOUNT)
            )
        assert result["type"] == "abort"
        assert result["reason"] == "already_configured"

    run_in_hass(scenario)


def test_reauthentication_keeps_the_registered_device(run_in_hass) -> None:
    """A new password must not add another device to the account."""

    async def scenario(hass: HomeAssistant) -> None:
        manager = make_manager()
        entry = await async_add_entry(hass, manager)
        device_id = entry.data["device_id"]

        new_manager = make_manager()
        with (
            patch(MANAGER, return_value=new_manager) as flow_manager,
            patch(
                "custom_components.toshiba_ac_link.ToshibaAcDeviceManager",
                return_value=new_manager,
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
        assert entry.data["password"] == "new-secret"
        assert entry.data["device_id"] == device_id
        flow_manager.assert_called_once_with(
            ACCOUNT["username"], "new-secret", device_id
        )

    run_in_hass(scenario)
