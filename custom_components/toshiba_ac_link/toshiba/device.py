# Based on toshiba_ac_community/device/__init__.py, see NOTICE.
# Copyright 2021 Kamil Sroka

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""One air conditioner: its state, what it supports, and how to change it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import inspect
import logging
import struct
from typing import Any, Generic, TypeVar

from .fcu_state import ToshibaAcFcuState
from .features import ToshibaAcFeatures
from .properties import (
    ToshibaAcAirPureIon,
    ToshibaAcDeviceEnergyConsumption,
    ToshibaAcFanMode,
    ToshibaAcHorizontalSwingMode,
    ToshibaAcMeritA,
    ToshibaAcMeritB,
    ToshibaAcMode,
    ToshibaAcPowerSelection,
    ToshibaAcSelfCleaning,
    ToshibaAcStatus,
    ToshibaAcSwingMode,
    ToshibaAcVerticalSwingMode,
    ToshibaAcWirelessLed,
)

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")

# The state is 19 bytes in the library's encoding, and 22 in the app's commands
COMMAND_HEX_LENGTH = 44


# ---------------------------
#   ToshibaAcCallback
# ---------------------------
class ToshibaAcCallback(Generic[T]):
    """A list of functions to call, with or without await."""

    def __init__(self) -> None:
        """Start empty."""
        self.callbacks: list[Callable[[T], Awaitable[None] | None]] = []

    def add(self, callback: Callable[[T], Awaitable[None] | None]) -> None:
        """Add a function, once."""
        if callback not in self.callbacks:
            self.callbacks.append(callback)

    def remove(self, callback: Callable[[T], Awaitable[None] | None]) -> None:
        """Remove a function."""
        if callback in self.callbacks:
            self.callbacks.remove(callback)

    async def __call__(self, value: T) -> None:
        """Call the functions."""
        for callback in list(self.callbacks):
            result = callback(value)
            if inspect.isawaitable(result):
                await result


# ---------------------------
#   ToshibaAcDevice
# ---------------------------
class ToshibaAcDevice:  # pylint: disable=too-many-public-methods
    """One unit. Its state comes from the realtime channel and the list of units."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        name: str,
        ac_id: str,
        ac_unique_id: str,
        initial_ac_state: str,
        firmware_version: str,
        merit_feature: str,
        ac_model_id: str,
        send_command: Callable[[str, str], Awaitable[None]],
        is_connected: Callable[[], bool],
    ) -> None:
        """Decode the first state, and what the unit supports."""
        self.name = name
        self.ac_id = ac_id
        self.ac_unique_id = ac_unique_id
        self.firmware_version = firmware_version
        # Kept for behaviour the feature bits cannot express, and for diagnostics
        self.merit_feature = merit_feature
        self.ac_model_id = ac_model_id
        self._send_command = send_command
        self._is_connected = is_connected
        self.fcu_state = ToshibaAcFcuState.from_hex_state(initial_ac_state)
        self._supported = ToshibaAcFeatures.from_merit_string_and_model(
            merit_feature, ac_model_id
        )
        self._seen_independent_swing = False
        self._on_state_changed_callback: ToshibaAcCallback[ToshibaAcDevice] = (
            ToshibaAcCallback()
        )
        self._on_energy_consumption_changed_callback: ToshibaAcCallback[
            ToshibaAcDevice
        ] = ToshibaAcCallback()
        self._ac_energy_consumption: ToshibaAcDeviceEnergyConsumption | None = None

    @property
    def connected(self) -> bool:
        """Return whether pushes can reach this unit's state."""
        return self._is_connected()

    async def async_update_state(self, hex_state: str) -> None:
        """Merge a state, in which unchanged fields have the value ff."""
        if self.fcu_state.update(hex_state):
            await self.state_changed()

    async def state_changed(self) -> None:
        """Tell the entities."""
        if self.fcu_state.uses_independent_swing:
            self._seen_independent_swing = True
        await self.on_state_changed_callback(self)

    async def handle_cmd_heartbeat(self, payload: dict[str, Any]) -> None:
        """Read the temperatures of a heartbeat.

        Each value is one hex byte, but the cloud can send an empty one, which must
        not lose the others. Temperatures are signed.
        """
        data: dict[str, int] = {}
        for key, value in payload.items():
            try:
                raw = bytes.fromhex(value)
                if len(raw) == 1:
                    data[key] = struct.unpack("b" if "Temp" in key else "B", raw)[0]
            except (TypeError, ValueError):
                continue
        if data and self.fcu_state.update_from_hbt(data):
            await self.state_changed()

    async def handle_update_ac_energy_consumption(
        self, val: ToshibaAcDeviceEnergyConsumption
    ) -> None:
        """Store a new energy reading."""
        if self._ac_energy_consumption != val:
            self._ac_energy_consumption = val
            await self.on_energy_consumption_changed_callback(self)

    def _limit_to_supported(
        self, state: ToshibaAcFcuState, future: ToshibaAcFcuState
    ) -> None:
        """Remove from a command what the unit cannot do in the mode it will be in."""
        supported = self.supported.for_ac_mode(future.ac_mode)
        if future.ac_fan_mode not in supported.ac_fan_mode:
            state.ac_fan_mode = ToshibaAcFanMode.NONE
        # A per-axis command needs a unit that reported independent vanes, which proves
        # the hardware even when the merit string does not declare these positions
        if (
            not state.uses_independent_swing
            and future.ac_swing_mode not in supported.ac_swing_mode
        ):
            state.ac_swing_mode = ToshibaAcSwingMode.NONE
        if future.ac_power_selection not in supported.ac_power_selection:
            state.ac_power_selection = ToshibaAcPowerSelection.NONE
        if future.ac_merit_b not in supported.ac_merit_b:
            state.ac_merit_b = ToshibaAcMeritB.OFF
        if future.ac_merit_a not in supported.ac_merit_a:
            state.ac_merit_a = ToshibaAcMeritA.OFF
        if future.ac_air_pure_ion not in supported.ac_air_pure_ion:
            state.ac_air_pure_ion = ToshibaAcAirPureIon.NONE
        if future.ac_self_cleaning not in supported.ac_self_cleaning:
            state.ac_self_cleaning = ToshibaAcSelfCleaning.NONE

    async def send_state_to_ac(self, state: ToshibaAcFcuState) -> None:
        """Send a command, and show its result right away.

        The unit's answer can take more than a minute to arrive, and shows the same
        values. Without this, the entity would jump back until then.
        """
        future = ToshibaAcFcuState.from_hex_state(self.fcu_state.encode())
        future.update(state.encode())
        if future.ac_status not in self.supported.ac_status:
            raise ValueError(f"Unsupported status: {future.ac_status}")
        if future.ac_mode not in self.supported.ac_mode:
            raise ValueError(f"Unsupported mode: {future.ac_mode}")
        self._limit_to_supported(state, future)

        # Wireless LED support is not in the merit string: a unit that has it reports it
        if (
            state.ac_wireless_led != ToshibaAcWirelessLed.NONE
            and self.ac_wireless_led == ToshibaAcWirelessLed.NONE
        ):
            state.ac_wireless_led = ToshibaAcWirelessLed.NONE

        # Turning on has to clear self cleaning
        if (
            state.ac_status == ToshibaAcStatus.ON
            and self.ac_self_cleaning == ToshibaAcSelfCleaning.ON
        ):
            state.ac_self_cleaning = ToshibaAcSelfCleaning.OFF

        # In 8 °C mode the wire value is 16 degrees above the setpoint
        if (
            state.ac_temperature is not None
            and future.ac_mode == ToshibaAcMode.HEAT
            and future.ac_merit_a == ToshibaAcMeritA.HEATING_8C
        ):
            state.ac_temperature = state.ac_temperature + 16

        hex_state = state.encode()
        await self._send_command(
            self.ac_unique_id, hex_state.ljust(COMMAND_HEX_LENGTH, "f")
        )
        await self.async_update_state(hex_state)

    @property
    def ac_status(self) -> ToshibaAcStatus:
        """Return whether the unit is on."""
        return self.fcu_state.ac_status

    async def set_ac_status(self, val: ToshibaAcStatus) -> None:
        """Turn the unit on or off."""
        state = ToshibaAcFcuState()
        state.ac_status = val
        await self.send_state_to_ac(state)

    @property
    def ac_mode(self) -> ToshibaAcMode:
        """Return the mode."""
        return self.fcu_state.ac_mode

    async def set_ac_mode(self, val: ToshibaAcMode) -> None:
        """Set the mode."""
        state = ToshibaAcFcuState()
        state.ac_mode = val
        await self.send_state_to_ac(state)

    async def turn_on_in_mode(self, val: ToshibaAcMode) -> None:
        """Turn the unit on in a mode, with one command."""
        state = ToshibaAcFcuState()
        state.ac_status = ToshibaAcStatus.ON
        state.ac_mode = val
        await self.send_state_to_ac(state)

    @property
    def ac_temperature(self) -> int | None:
        """Return the setpoint. In 8 °C mode the wire value is 16 degrees above it."""
        value = self.fcu_state.ac_temperature
        if (
            self.fcu_state.ac_mode == ToshibaAcMode.HEAT
            and self.fcu_state.ac_merit_a == ToshibaAcMeritA.HEATING_8C
            and isinstance(value, int)
        ):
            return value - 16
        return value

    async def set_ac_temperature(self, val: int | float | None) -> None:
        """Set the setpoint, rounded to a whole degree."""
        state = ToshibaAcFcuState()
        state.ac_temperature = val
        await self.send_state_to_ac(state)

    @property
    def ac_fan_mode(self) -> ToshibaAcFanMode:
        """Return the fan mode."""
        return self.fcu_state.ac_fan_mode

    async def set_ac_fan_mode(self, val: ToshibaAcFanMode) -> None:
        """Set the fan mode."""
        state = ToshibaAcFcuState()
        state.ac_fan_mode = val
        await self.send_state_to_ac(state)

    @property
    def ac_swing_mode(self) -> ToshibaAcSwingMode:
        """Return the swing mode."""
        return self.fcu_state.ac_swing_mode

    async def set_ac_swing_mode(self, val: ToshibaAcSwingMode) -> None:
        """Set the swing mode."""
        state = ToshibaAcFcuState()
        state.ac_swing_mode = val
        await self.send_state_to_ac(state)

    @property
    def supports_independent_swing(self) -> bool:
        """Return whether the unit ever reported separate vertical and horizontal swing.

        Sticky: units report the legacy bytes again after a legacy preset command,
        and only seeing the other encoding proves the capability.
        """
        if not self._seen_independent_swing and self.fcu_state.uses_independent_swing:
            self._seen_independent_swing = True
        return self._seen_independent_swing

    @property
    def ac_vertical_swing_mode(self) -> ToshibaAcVerticalSwingMode:
        """Return the vertical swing."""
        return self.fcu_state.ac_vertical_swing_mode

    async def set_ac_vertical_swing_mode(self, val: ToshibaAcVerticalSwingMode) -> None:
        """Set the vertical swing."""
        await self._set_independent_swing(val, self.ac_horizontal_swing_mode)

    @property
    def ac_horizontal_swing_mode(self) -> ToshibaAcHorizontalSwingMode:
        """Return the horizontal swing."""
        return self.fcu_state.ac_horizontal_swing_mode

    async def set_ac_horizontal_swing_mode(
        self, val: ToshibaAcHorizontalSwingMode
    ) -> None:
        """Set the horizontal swing."""
        await self._set_independent_swing(self.ac_vertical_swing_mode, val)

    async def _set_independent_swing(
        self,
        vertical: ToshibaAcVerticalSwingMode,
        horizontal: ToshibaAcHorizontalSwingMode,
    ) -> None:
        """Set both swing axes, on a unit that reported having them."""
        if not self.supports_independent_swing:
            _LOGGER.warning("[%s] Independent swing is not supported", self.name)
            return
        state = ToshibaAcFcuState()
        state.set_ac_independent_swing(vertical, horizontal)
        await self.send_state_to_ac(state)

    @property
    def ac_power_selection(self) -> ToshibaAcPowerSelection:
        """Return the power selection."""
        return self.fcu_state.ac_power_selection

    async def set_ac_power_selection(self, val: ToshibaAcPowerSelection) -> None:
        """Set the power selection."""
        state = ToshibaAcFcuState()
        state.ac_power_selection = val
        await self.send_state_to_ac(state)

    @property
    def ac_merit_b(self) -> ToshibaAcMeritB:
        """Return merit B."""
        return self.fcu_state.ac_merit_b

    async def set_ac_merit_b(self, val: ToshibaAcMeritB) -> None:
        """Set merit B."""
        state = ToshibaAcFcuState()
        state.ac_merit_b = val
        await self.send_state_to_ac(state)

    @property
    def ac_merit_a(self) -> ToshibaAcMeritA:
        """Return merit A."""
        return self.fcu_state.ac_merit_a

    async def set_ac_merit_a(self, val: ToshibaAcMeritA) -> None:
        """Set merit A."""
        state = ToshibaAcFcuState()
        state.ac_merit_a = val
        await self.send_state_to_ac(state)

    @property
    def ac_air_pure_ion(self) -> ToshibaAcAirPureIon:
        """Return the air purifier state."""
        return self.fcu_state.ac_air_pure_ion

    async def set_ac_air_pure_ion(self, val: ToshibaAcAirPureIon) -> None:
        """Set the air purifier."""
        state = ToshibaAcFcuState()
        state.ac_air_pure_ion = val
        await self.send_state_to_ac(state)

    @property
    def ac_indoor_temperature(self) -> int | None:
        """Return the indoor temperature."""
        return self.fcu_state.ac_indoor_temperature

    @property
    def ac_outdoor_temperature(self) -> int | None:
        """Return the outdoor temperature."""
        return self.fcu_state.ac_outdoor_temperature

    @property
    def ac_self_cleaning(self) -> ToshibaAcSelfCleaning:
        """Return the self cleaning state."""
        return self.fcu_state.ac_self_cleaning

    @property
    def ac_wireless_led(self) -> ToshibaAcWirelessLed:
        """Return the wireless LED state."""
        return self.fcu_state.ac_wireless_led

    async def set_ac_wireless_led(self, val: ToshibaAcWirelessLed) -> None:
        """Set the wireless LED."""
        state = ToshibaAcFcuState()
        state.ac_wireless_led = val
        await self.send_state_to_ac(state)

    @property
    def ac_energy_consumption(self) -> ToshibaAcDeviceEnergyConsumption | None:
        """Return the energy used since the start of the year."""
        return self._ac_energy_consumption

    @property
    def on_state_changed_callback(self) -> ToshibaAcCallback[ToshibaAcDevice]:
        """Return the callbacks for a change of the state or the connection."""
        return self._on_state_changed_callback

    @property
    def on_energy_consumption_changed_callback(
        self,
    ) -> ToshibaAcCallback[ToshibaAcDevice]:
        """Return the callbacks for a new energy reading."""
        return self._on_energy_consumption_changed_callback

    @property
    def supported(self) -> ToshibaAcFeatures:
        """Return what the unit supports.

        H.DA has no merit bit, so it is learned from the unit reporting it. Sticky,
        like supports_independent_swing.
        """
        if (
            self.fcu_state.ac_swing_mode == ToshibaAcSwingMode.HADA
            and ToshibaAcSwingMode.HADA not in self._supported.ac_swing_mode
        ):
            self._supported.ac_swing_mode.append(ToshibaAcSwingMode.HADA)
        return self._supported
