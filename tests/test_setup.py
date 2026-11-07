"""Tests for setup, unload and the reconnect action, on a real core."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.toshiba_ac_link.const import DOMAIN
from custom_components.toshiba_ac_link.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.toshiba_ac_link.toshiba.errors import (
    ToshibaAuthError,
    ToshibaBlockedError,
    ToshibaConnectionError,
)

from .conftest import async_add_entry, make_manager

MANAGER = "custom_components.toshiba_ac_link.ToshibaAcDeviceManager"


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (ToshibaAuthError("rejected"), ConfigEntryState.SETUP_ERROR),
        (ToshibaBlockedError("slow down", 100), ConfigEntryState.SETUP_RETRY),
        (ToshibaConnectionError("HTTP 500"), ConfigEntryState.SETUP_RETRY),
    ],
)
def test_only_a_rejected_token_asks_for_the_password(run_in_hass, error, state) -> None:
    """A rate limit or a network error must keep retrying, not ask to reconfigure."""

    async def scenario(hass: HomeAssistant) -> None:
        entry = await async_add_entry(hass, make_manager())

        failing = make_manager()
        failing.connect = AsyncMock(side_effect=error)
        with patch(MANAGER, return_value=failing):
            await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()

        assert entry.state is state
        asked = bool(hass.config_entries.flow.async_progress_by_handler(DOMAIN))
        assert asked is isinstance(error, ToshibaAuthError)

    run_in_hass(scenario)


def test_a_block_sets_the_wait_of_the_retry(run_in_hass) -> None:
    """Home Assistant alone would try again within 10 minutes."""

    async def scenario(hass: HomeAssistant) -> None:
        hass.set_state(CoreState.running)
        entry = await async_add_entry(hass, make_manager())

        failing = make_manager()
        failing.connect = AsyncMock(side_effect=ToshibaBlockedError("slow down", 7200))
        with (
            patch(MANAGER, return_value=failing),
            patch(
                "homeassistant.config_entries.async_call_later",
                return_value=lambda: None,
            ) as later,
        ):
            await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()

        assert later.call_args.args[1] >= 7200

    run_in_hass(scenario)


def test_unload_closes_the_connection(run_in_hass) -> None:
    """Unloading ends the socket."""

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


@pytest.mark.parametrize(
    "error",
    [
        ToshibaAuthError("rejected"),
        ToshibaBlockedError("slow down", 100),
        ToshibaConnectionError("HTTP 500"),
    ],
)
def test_reconnect_action_reports_an_entry_that_did_not_load(
    run_in_hass, error
) -> None:
    """An automation must learn that the action did not reconnect."""

    async def scenario(hass: HomeAssistant) -> None:
        entry = await async_add_entry(hass, make_manager())

        failing = make_manager()
        failing.connect = AsyncMock(side_effect=error)
        with (
            patch(MANAGER, return_value=failing),
            pytest.raises(HomeAssistantError, match="1 of 1") as raised,
        ):
            await hass.services.async_call(DOMAIN, "reconnect", blocking=True)
        await hass.async_block_till_done()

        assert entry.state is not ConfigEntryState.LOADED
        assert "user@example.com" not in str(raised.value)

    run_in_hass(scenario)


def test_reconnect_action_leaves_out_a_disabled_entry(run_in_hass) -> None:
    """The user turned it off, so its not loading is no failure."""

    async def scenario(hass: HomeAssistant) -> None:
        manager = make_manager()
        with patch(MANAGER, return_value=manager) as created:
            entry = await async_add_entry(hass, manager)
            await hass.config_entries.async_set_disabled_by(
                entry.entry_id, ConfigEntryDisabler.USER
            )
            created.reset_mock()

            await hass.services.async_call(DOMAIN, "reconnect", blocking=True)

        created.assert_not_called()
        assert entry.state is ConfigEntryState.NOT_LOADED

    run_in_hass(scenario)


def test_diagnostics_hide_secrets(run_in_hass) -> None:
    """A shared diagnostics file holds no account data or tokens."""

    async def scenario(hass: HomeAssistant) -> None:
        entry = await async_add_entry(hass, make_manager())

        result = await async_get_config_entry_diagnostics(hass, entry)

        text = str(result)
        for secret in (
            "secret",
            "user@example.com",
            "access-token",
            "consumer-1",
            "Living room",
        ):
            assert secret not in text
        assert result["device_count"] == 1
        assert result["devices"][0]["ac_model_id"] == "2"

    run_in_hass(scenario)
