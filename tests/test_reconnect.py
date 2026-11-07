"""Tests for the recovery of a lost connection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant

from custom_components.toshiba_ac_link.reconnect import (
    RELOAD_DELAY_S,
    ToshibaReconnectManager,
)

from .conftest import make_manager


def _lose_connection(hass: HomeAssistant, manager: MagicMock) -> None:
    """Report a lost connection, as the Azure client does."""
    manager.amqp_api.device.connected = False
    manager.amqp_api.device.on_connection_state_change()


def _setup(hass: HomeAssistant) -> tuple[ToshibaReconnectManager, MagicMock]:
    """Return a watched device manager, and a mock in place of the reload."""
    hass.config_entries.async_reload = AsyncMock()
    entry = MagicMock(entry_id="entry")
    manager = make_manager()
    reconnect = ToshibaReconnectManager(hass, entry)
    reconnect.attach(manager)
    return reconnect, manager


def test_lost_connection_schedules_a_reload(run_in_hass) -> None:
    """A loss schedules one reload, not one per report."""

    async def scenario(hass: HomeAssistant) -> None:
        reconnect, manager = _setup(hass)

        _lose_connection(hass, manager)
        _lose_connection(hass, manager)
        await hass.async_block_till_done()

        assert reconnect._cancel_reload is not None
        await reconnect._async_reload()
        hass.config_entries.async_reload.assert_awaited_once_with("entry")

        await reconnect._async_reload()
        hass.config_entries.async_reload.assert_awaited_once()

    run_in_hass(scenario)


def test_recovery_cancels_the_reload(run_in_hass) -> None:
    """A client that recovers by itself must not be interrupted."""

    async def scenario(hass: HomeAssistant) -> None:
        reconnect, manager = _setup(hass)

        _lose_connection(hass, manager)
        await hass.async_block_till_done()
        reconnect.recovered(manager)

        assert reconnect._cancel_reload is None

    run_in_hass(scenario)


def test_replaced_client_is_ignored(run_in_hass) -> None:
    """A late call from the client before a reconnect means nothing."""

    async def scenario(hass: HomeAssistant) -> None:
        reconnect, manager = _setup(hass)
        old_client = manager.amqp_api.device
        old_client.connected = False
        manager.amqp_api.device = MagicMock()

        old_client.on_connection_state_change()
        await hass.async_block_till_done()

        assert reconnect._cancel_reload is None

    run_in_hass(scenario)


def test_stop_prevents_a_reload(run_in_hass) -> None:
    """An entry that is being unloaded must not come back."""

    async def scenario(hass: HomeAssistant) -> None:
        reconnect, manager = _setup(hass)

        _lose_connection(hass, manager)
        await hass.async_block_till_done()
        reconnect.stop()

        assert reconnect._cancel_reload is None
        await reconnect._async_reload()
        hass.config_entries.async_reload.assert_not_awaited()

    run_in_hass(scenario)
