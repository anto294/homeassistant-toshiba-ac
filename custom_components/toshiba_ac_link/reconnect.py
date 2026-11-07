"""Automatic recovery of a lost connection to the Toshiba cloud."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later
from toshiba_ac_community.device_manager import ToshibaAcDeviceManager

_LOGGER = logging.getLogger(__name__)

# How long the Azure client gets to recover by itself before the entry is
# reloaded. It reconnects within about a second when it refreshes its SAS token,
# and a reload must not interrupt that.
RELOAD_DELAY_S = 30


# ---------------------------
#   ToshibaReconnectManager
# ---------------------------
class ToshibaReconnectManager:
    """Reload the config entry when the connection is lost and does not come back."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Store the context."""
        self._hass = hass
        self._entry = entry
        self._cancel_reload: Callable[[], None] | None = None
        self._stopped = False

    async def _async_reload(self, _now: datetime | None = None) -> None:
        """Reload the entry to make a new connection."""
        self._cancel_reload = None
        if self._stopped:
            return
        self._stopped = True
        _LOGGER.info("Reloading Toshiba Link to restore the connection")
        await self._hass.config_entries.async_reload(self._entry.entry_id)

    def _schedule_reload(self) -> None:
        """Schedule a reload. Runs on the event loop."""
        if self._cancel_reload is not None or self._stopped:
            return
        _LOGGER.warning(
            "Connection to the Toshiba cloud lost, reloading in %d seconds if it does not recover",
            RELOAD_DELAY_S,
        )
        self._cancel_reload = async_call_later(
            self._hass, RELOAD_DELAY_S, self._async_reload
        )

    def attach(self, device_manager: ToshibaAcDeviceManager) -> None:
        """Watch the current Azure client of the device manager.

        The Azure client is replaced when it reconnects, so a call from a client
        that is no longer in use is ignored.
        """
        if not (device_manager.amqp_api and device_manager.amqp_api.device):
            return

        client = device_manager.amqp_api.device

        def on_connection_state_change() -> None:
            """Handle a connection change, which arrives on a thread of the client."""
            amqp_api = device_manager.amqp_api
            if amqp_api and amqp_api.device is client and not client.connected:
                self._hass.loop.call_soon_threadsafe(self._schedule_reload)

        client.on_connection_state_change = on_connection_state_change

    def recovered(self, device_manager: ToshibaAcDeviceManager) -> None:
        """Cancel a pending reload after the client reconnected by itself."""
        if self._cancel_reload is not None:
            _LOGGER.info("Connection to the Toshiba cloud restored")
            self._cancel_reload()
            self._cancel_reload = None
        self.attach(device_manager)

    def stop(self) -> None:
        """Stop reloading, because the entry is being unloaded."""
        self._stopped = True
        if self._cancel_reload is not None:
            self._cancel_reload()
            self._cancel_reload = None
