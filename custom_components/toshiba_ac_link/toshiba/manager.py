"""The account: its units, the realtime channel, and the few periodic requests."""

from __future__ import annotations

from collections.abc import Callable
import asyncio
from datetime import UTC, datetime
import logging
import struct
import time
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .api import LoginResult, ToshibaApi, parse_units
from .device import ToshibaAcDevice
from .errors import (
    ToshibaAuthError,
    ToshibaBlockedError,
    ToshibaConnectionError,
    ToshibaError,
)
from .guard import ToshibaGuard, jitter
from .properties import ToshibaAcDeviceEnergyConsumption
from .realtime import ToshibaRealtime

_LOGGER = logging.getLogger(__name__)

# A restart within this time reuses the list of units instead of asking again
UNITS_CACHE_S = 15 * 60

# The socket can miss a change while it reconnects, so ask for all the units now
# and then. One request covers every unit of the account.
REFRESH_S = 30 * 60
REFRESH_MIN_S = 5 * 60

# Energy is a yearly total that does not change faster than this
ENERGY_S = 60 * 60

# An address for the socket is not reused this close to its expiry
CHANNEL_MARGIN_S = 60

FIRST_CONNECTION_WAIT_S = 10


# ---------------------------
#   ToshibaAcDeviceManager
# ---------------------------
class ToshibaAcDeviceManager:  # pylint: disable=too-many-instance-attributes
    """Everything that belongs to one logged in account."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        guard: ToshibaGuard,
        device_id: str,
        login: LoginResult,
        on_auth_failed: Callable[[], None],
    ) -> None:
        """Store the account. Nothing is requested before connect."""
        self._hass = hass
        self._guard = guard
        self._device_id = device_id
        self._consumer_id = login.consumer_id
        self._on_auth_failed = on_auth_failed
        self._api = ToshibaApi(session, guard, device_id, login)
        self._realtime = ToshibaRealtime(
            session,
            self._async_channel_url,
            self._async_forget_channel,
            self._handle_message,
            self._handle_connection,
        )
        self._devices: dict[str, ToshibaAcDevice] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._first_connection = asyncio.Event()
        self._refresh_now = asyncio.Event()
        self._message_count = 0
        self._message_counter = 0

    # Start and stop

    async def connect(self) -> None:
        """Load the units, and open the channel. Raises what setup has to react to."""
        self._api.check_token()
        await self._async_load_units()
        self._start_task(self._async_run_realtime(), "realtime")
        self._start_task(self._async_refresh_loop(), "refresh")
        if any(device.supported.ac_energy_report for device in self.devices):
            self._start_task(self._async_energy_loop(), "energy")
        # The channel keeps trying by itself, so a slow start is not a failed one
        try:
            async with asyncio.timeout(FIRST_CONNECTION_WAIT_S):
                await self._first_connection.wait()
        except TimeoutError:
            _LOGGER.debug("The realtime channel is not connected yet")

    def _start_task(self, coroutine: Any, name: str) -> None:
        """Run a loop for as long as the account is loaded."""
        self._tasks.append(
            self._hass.async_create_background_task(
                coroutine, f"toshiba_ac_link {name}"
            )
        )

    async def shutdown(self) -> None:
        """Stop everything, which also closes the socket."""
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # Units

    @property
    def devices(self) -> list[ToshibaAcDevice]:
        """Return the units."""
        return list(self._devices.values())

    async def get_devices(self) -> list[ToshibaAcDevice]:
        """Return the units, which connect has already loaded."""
        return self.devices

    @property
    def connected(self) -> bool:
        """Return whether the realtime channel is open."""
        return self._realtime.connected

    async def _async_load_units(self) -> None:
        """Build the units, from a recent answer if a restart left one."""
        cached = self._guard.cached("units", self._consumer_id)
        if cached and 0 <= time.time() - cached["at"] < UNITS_CACHE_S:
            groups = cached["groups"]
        else:
            groups = await self._api.async_get_groups()
            await self._guard.async_cache(
                "units", self._consumer_id, {"at": time.time(), "groups": groups}
            )
        try:
            for unit in parse_units(groups):
                self._devices[unit.ac_unique_id] = ToshibaAcDevice(
                    unit.name,
                    unit.ac_id,
                    unit.ac_unique_id,
                    unit.state,
                    unit.firmware_version,
                    unit.merit_feature,
                    unit.ac_model_id,
                    self._async_send_command,
                    lambda: self._realtime.connected,
                )
        except (TypeError, ValueError, struct.error) as ex:
            raise ToshibaConnectionError("Unexpected state of a unit") from ex

    # Realtime channel

    async def _async_run_realtime(self) -> None:
        """Run the channel, which ends only when the token is rejected."""
        try:
            await self._realtime.async_run()
        except ToshibaAuthError:
            self._on_auth_failed()

    async def _async_channel_url(self) -> str:
        """Return an address for the socket, without a request if one is still good."""
        cached = self._guard.cached("channel", self._consumer_id)
        if cached and cached["expires_at"] - time.time() > CHANNEL_MARGIN_S:
            url: str = cached["url"]
            return url
        channel = await self._api.async_get_channel()
        await self._guard.async_cache(
            "channel",
            self._consumer_id,
            {"url": channel.url, "expires_at": channel.expires_at},
        )
        return channel.url

    async def _async_forget_channel(self) -> None:
        """Stop reusing the address that was refused."""
        await self._guard.async_cache("channel", self._consumer_id, None)

    def _handle_connection(self, connected: bool) -> None:
        """Update the entities, and catch up on what the channel may have missed."""
        if connected:
            if self._first_connection.is_set():
                self._refresh_now.set()
            self._first_connection.set()
        for device in self.devices:
            self._start_short_task(device.state_changed())

    def _handle_message(self, source_id: str, data: dict[str, Any]) -> None:
        """Hand a published message to its unit."""
        device = self._devices.get(source_id)
        payload = data.get("payload")
        if device is None or not isinstance(payload, dict):
            return
        self._message_count += 1
        if data.get("cmd") == "CMD_FCU_FROM_AC":
            if isinstance(hex_state := payload.get("data"), str):
                self._start_short_task(self._async_update_device(device, hex_state))
        elif data.get("cmd") == "CMD_HEARTBEAT":
            self._start_short_task(device.handle_cmd_heartbeat(payload))

    async def _async_update_device(
        self, device: ToshibaAcDevice, hex_state: str
    ) -> None:
        """Merge a state. A value that cannot be decoded must not end the channel."""
        try:
            await device.async_update_state(hex_state)
        except (ValueError, TypeError) as ex:
            _LOGGER.debug("Ignoring a state of %s: %s", device.name, ex)

    def _start_short_task(self, coroutine: Any) -> None:
        """Run work that ends by itself, and that shutdown does not have to wait for."""
        self._hass.async_create_background_task(coroutine, "toshiba_ac_link update")

    # Commands

    async def _async_send_command(self, unit_id: str, hex_state: str) -> None:
        """Hand a command to Toshiba."""
        self._message_counter += 1
        message = {
            "sourceId": self._device_id,
            "messageId": (
                f"MB_{self._device_id[:15].upper()}-"
                f"{self._message_counter % 100000000:08d}"
            ),
            "targetId": [unit_id],
            "cmd": "CMD_FCU_TO_AC",
            "payload": {"data": hex_state},
            "timeStamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        }
        try:
            await self._api.async_send_command(message)
        except ToshibaAuthError as ex:
            self._on_auth_failed()
            raise HomeAssistantError("Toshiba rejected the login") from ex
        except ToshibaError as ex:
            raise HomeAssistantError(f"Command not sent: {ex}") from ex

    # Periodic requests

    async def _async_refresh_loop(self) -> None:
        """Ask for the state of all units now and then, and after a reconnection."""
        while True:
            try:
                async with asyncio.timeout(REFRESH_S * jitter(0.9, 1.1)):
                    await self._refresh_now.wait()
                await asyncio.sleep(REFRESH_MIN_S)
            except TimeoutError:
                pass
            self._refresh_now.clear()
            if not await self._async_periodic(self._async_refresh):
                return

    async def _async_refresh(self) -> None:
        """Merge the current state of all units."""
        groups = await self._api.async_get_groups()
        await self._guard.async_cache(
            "units", self._consumer_id, {"at": time.time(), "groups": groups}
        )
        for unit in parse_units(groups):
            if device := self._devices.get(unit.ac_unique_id):
                await self._async_update_device(device, unit.state)

    async def _async_energy_loop(self) -> None:
        """Read the energy once per hour, which a restart does not bring forward."""
        while True:
            cached = self._guard.cached("energy", self._consumer_id)
            age = time.time() - cached["at"] if cached else ENERGY_S
            if cached and 0 <= age < ENERGY_S:
                await self._async_apply_energy(cached["units"])
                await asyncio.sleep((ENERGY_S - age) * jitter(1, 1.1))
            if not await self._async_periodic(self._async_fetch_energy):
                return
            await asyncio.sleep(ENERGY_S * jitter(1, 1.1))

    async def _async_fetch_energy(self) -> None:
        """Read the energy of the units that report it."""
        units = await self._api.async_get_energy(
            [d.ac_unique_id for d in self.devices if d.supported.ac_energy_report]
        )
        await self._guard.async_cache(
            "energy", self._consumer_id, {"at": time.time(), "units": units}
        )
        await self._async_apply_energy(units)

    async def _async_apply_energy(self, units: dict[str, int]) -> None:
        """Hand the readings to their units."""
        since = datetime(datetime.now().year, 1, 1).astimezone(UTC)
        for unit_id, energy_wh in units.items():
            if device := self._devices.get(unit_id):
                await device.handle_update_ac_energy_consumption(
                    ToshibaAcDeviceEnergyConsumption(energy_wh, since)
                )

    async def _async_periodic(self, request: Any) -> bool:
        """Run a periodic request. A failure waits for the next turn, with no retry."""
        try:
            await request()
        except ToshibaAuthError:
            self._on_auth_failed()
            return False
        except ToshibaBlockedError as ex:
            _LOGGER.warning("Toshiba asked to slow down: %s", ex)
            await asyncio.sleep(ex.retry_after)
        except ToshibaError as ex:
            _LOGGER.debug("Periodic request failed: %s", ex)
        return True

    def diagnostics(self) -> dict[str, Any]:
        """Return counters without secrets."""
        return {
            "connected": self.connected,
            "messages_received": self._message_count,
            **self._guard.diagnostics(),
        }
