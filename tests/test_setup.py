"""Tests for setup, unload and the reconnect action, on a real core."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from azure.iot.device.common.transport_exceptions import (
    ConnectionDroppedError,
    UnauthorizedError,
)
from azure.iot.device.exceptions import CredentialError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
import pytest
from toshiba_ac_community.utils import amqp_api
from toshiba_ac_community.utils.http_api import (
    ToshibaAcHttpApiAuthError,
    ToshibaAcHttpApiRateLimitError,
)

from custom_components.toshiba_ac_link import (
    CONNECT_TIMEOUT_S,
    NOTIFICATION_ID,
    ONE_CONNECTION_HINT,
    _setup_error,
)
from custom_components.toshiba_ac_link.const import DOMAIN
from custom_components.toshiba_ac_link.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import async_add_entry, make_manager

MANAGER = "custom_components.toshiba_ac_link.ToshibaAcDeviceManager"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ToshibaAcHttpApiAuthError("HTTP 401"), ConfigEntryAuthFailed),
        (ToshibaAcHttpApiRateLimitError("HTTP 403"), ConfigEntryNotReady),
        (RuntimeError("403 from the WAF, auth service"), ConfigEntryNotReady),
        (ConnectionError("Network unreachable"), ConfigEntryNotReady),
        (TimeoutError(), ConfigEntryNotReady),
        (CredentialError("Credentials invalid"), ConfigEntryNotReady),
    ],
)
def test_only_a_rejected_login_asks_for_the_password(error, expected) -> None:
    """A rate limit or WAF answer must keep retrying, not ask to reconfigure."""
    assert isinstance(_setup_error(error), expected)


def test_timeout_message_names_the_limit() -> None:
    """The message says how long setup waited."""
    assert str(CONNECT_TIMEOUT_S) in str(_setup_error(TimeoutError()))


def test_stored_access_token_is_reused_and_sas_token_is_not(run_in_hass) -> None:
    """A restart skips the login, and always registers a new SAS token."""

    async def scenario(hass: HomeAssistant) -> None:
        manager = make_manager()
        with patch(MANAGER, return_value=manager) as created:
            entry = await async_add_entry(hass, manager)
            created.reset_mock()
            await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()

        args = created.call_args
        assert args.kwargs["sas_token"] is None
        assert args.kwargs["access_token"] == "access-token"
        assert args.kwargs["consumer_id"] == "consumer-1"

    run_in_hass(scenario)


def test_new_access_token_is_stored(run_in_hass) -> None:
    """A login made by the library during operation survives a restart."""

    async def scenario(hass: HomeAssistant) -> None:
        manager = make_manager()
        added = []
        manager.on_access_token_updated_callback.add = added.append
        entry = await async_add_entry(hass, manager)

        await added[-1](("new-token", "Bearer", "consumer-1"))

        assert entry.data["access_token"] == "new-token"

    run_in_hass(scenario)


def test_refused_connection_shows_a_notice(run_in_hass) -> None:
    """A refusal points at the competing connection, not at the password."""

    async def scenario(hass: HomeAssistant) -> None:
        good = make_manager()
        entry = await async_add_entry(hass, good)
        assert entry.state is ConfigEntryState.LOADED

        refused = make_manager()
        refused.connect = AsyncMock(side_effect=UnauthorizedError("not authorised"))
        with (
            patch(MANAGER, return_value=refused),
            patch(
                "custom_components.toshiba_ac_link.persistent_notification.async_create"
            ) as create,
        ):
            await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.SETUP_RETRY
        assert ONE_CONNECTION_HINT in entry.reason
        create.assert_called_once()
        assert create.call_args.kwargs["notification_id"] == NOTIFICATION_ID

        await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_network_error_shows_no_notice(run_in_hass) -> None:
    """A plain network failure must not accuse another instance."""

    async def scenario(hass: HomeAssistant) -> None:
        entry = await async_add_entry(hass, make_manager())

        failing = make_manager()
        failing.connect = AsyncMock(side_effect=ConnectionError("unreachable"))
        with (
            patch(MANAGER, return_value=failing),
            patch(
                "custom_components.toshiba_ac_link.persistent_notification.async_create"
            ) as create,
        ):
            await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.SETUP_RETRY
        create.assert_not_called()

        await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_failed_device_fetch_closes_the_connection(run_in_hass) -> None:
    """A retry must not leave the first connection open, since only one is allowed."""

    async def scenario(hass: HomeAssistant) -> None:
        entry = await async_add_entry(hass, make_manager())

        failing = make_manager()
        failing.get_devices = AsyncMock(side_effect=RuntimeError("HTTP 500"))
        with patch(MANAGER, return_value=failing):
            await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.SETUP_RETRY
        failing.shutdown.assert_awaited_once()

        await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_unload_closes_the_connection(run_in_hass) -> None:
    """Unloading ends the single connection that the account has."""

    async def scenario(hass: HomeAssistant) -> None:
        manager = make_manager()
        entry = await async_add_entry(hass, manager)

        assert await hass.config_entries.async_unload(entry.entry_id)

        assert entry.state is ConfigEntryState.NOT_LOADED
        manager.shutdown.assert_awaited()

    run_in_hass(scenario)


def test_reconnect_action_reloads_the_entry(run_in_hass) -> None:
    """The action makes a new connection."""

    async def scenario(hass: HomeAssistant) -> None:
        manager = make_manager()
        with patch(MANAGER, return_value=manager) as created:
            entry = await async_add_entry(hass, manager)
            created.reset_mock()
            await hass.services.async_call(DOMAIN, "reconnect", blocking=True)
            await hass.async_block_till_done()

        created.assert_called_once()
        assert entry.state is ConfigEntryState.LOADED

    run_in_hass(scenario)


def test_diagnostics_hide_secrets(run_in_hass) -> None:
    """A shared diagnostics file holds no account data or tokens."""

    async def scenario(hass: HomeAssistant) -> None:
        entry = await async_add_entry(hass, make_manager())

        result = await async_get_config_entry_diagnostics(hass, entry)

        text = str(result)
        for secret in ("secret", "user@example.com", "access-token", "consumer-1"):
            assert secret not in text
        assert result["device_count"] == 1
        assert result["devices"][0]["ac_model_id"] == "2"

    run_in_hass(scenario)


@pytest.mark.parametrize(
    "error",
    [
        CredentialError("Credentials invalid, could not connect"),
        UnauthorizedError("Connection Refused: not authorised"),
        ConnectionDroppedError("Unexpected disconnection"),
    ],
)
async def test_refused_command_names_the_cause(error) -> None:
    """The library's text reads like a wrong password, which it is not."""
    api = MagicMock()
    api.device.send_message = AsyncMock(side_effect=error)

    with pytest.raises(HomeAssistantError) as excinfo:
        await amqp_api.ToshibaAcAmqpApi.send_message(api, "{}")

    assert ONE_CONNECTION_HINT in str(excinfo.value)
    assert "toshiba_ac_link.reconnect" in str(excinfo.value)


async def test_working_command_keeps_the_message_properties() -> None:
    """The patch must still send what the library builds, not the bare text."""
    api = MagicMock()
    api.device.send_message = AsyncMock()

    await amqp_api.ToshibaAcAmqpApi.send_message(api, "{}")

    message = api.device.send_message.await_args.args[0]
    assert message.custom_properties["type"] == "mob"
    assert message.content_type == "application/json"
