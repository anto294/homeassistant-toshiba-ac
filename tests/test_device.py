"""Tests of ToshibaAcDevice, down to the frame that it hands to the cloud."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.toshiba_ac_link.toshiba.device import (
    COMMAND_HEX_LENGTH,
    ToshibaAcCallback,
    ToshibaAcDevice,
)
from custom_components.toshiba_ac_link.toshiba.fcu_state import ToshibaAcFcuState
from custom_components.toshiba_ac_link.toshiba.properties import (
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

from .conftest import assert_translated
from .fake_toshiba import RUNNING_STATE, UNIT_STATE

INDEPENDENT = "ffffffff" + "b0" + "ff" * 17


def make_unit(
    state: str = RUNNING_STATE, merit: str = "2c00", model: str = "2"
) -> ToshibaAcDevice:
    """Return a real unit that records the frames it sends."""
    return ToshibaAcDevice(
        "Unit", "ac", "unit-1", state, "5.0.00", merit, model, AsyncMock(), lambda: True
    )


def sent_state(unit: ToshibaAcDevice) -> ToshibaAcFcuState:
    """Decode the frame of the only command."""
    unit._send_command.assert_awaited_once()
    unit_id, frame = unit._send_command.await_args.args
    assert unit_id == "unit-1"
    assert len(frame) == COMMAND_HEX_LENGTH
    return ToshibaAcFcuState.from_hex_state(frame)


@pytest.mark.parametrize(
    ("setter", "attribute", "value"),
    [
        ("set_ac_status", "ac_status", ToshibaAcStatus.OFF),
        ("set_ac_mode", "ac_mode", ToshibaAcMode.HEAT),
        ("set_ac_fan_mode", "ac_fan_mode", ToshibaAcFanMode.LOW),
        ("set_ac_swing_mode", "ac_swing_mode", ToshibaAcSwingMode.SWING_VERTICAL),
        (
            "set_ac_power_selection",
            "ac_power_selection",
            ToshibaAcPowerSelection.POWER_50,
        ),
        ("set_ac_merit_a", "ac_merit_a", ToshibaAcMeritA.ECO),
    ],
)
async def test_a_setter_sends_its_field_and_shows_it_at_once(
    setter, attribute, value
) -> None:
    """The answer of the unit can be late, and the entity must not jump back."""
    unit = make_unit()
    changed = MagicMock()
    unit.on_state_changed_callback.add(changed)

    await getattr(unit, setter)(value)

    assert getattr(sent_state(unit), attribute) is value
    assert getattr(unit, attribute) is value
    changed.assert_called_once_with(unit)


async def test_a_failed_command_leaves_the_state_alone() -> None:
    """The entity must not show what the unit never received."""
    unit = make_unit()
    unit._send_command.side_effect = RuntimeError("refused")

    with pytest.raises(RuntimeError):
        await unit.set_ac_mode(ToshibaAcMode.HEAT)

    assert unit.ac_mode is ToshibaAcMode.COOL


async def test_a_mode_that_the_unit_lacks_is_refused() -> None:
    """Merit bits can say that a unit only heats, and no command should leave then."""
    unit = make_unit(merit="0100")

    with pytest.raises(HomeAssistantError) as raised:
        await unit.set_ac_mode(ToshibaAcMode.COOL)

    assert_translated(raised.value, "unsupported_mode", unit="Unit", mode="COOL")
    unit._send_command.assert_not_awaited()
    assert unit.ac_mode is ToshibaAcMode.COOL


async def test_a_status_that_the_unit_lacks_is_refused() -> None:
    """Every unit has every status today, but the tables can say otherwise."""
    unit = make_unit()
    unit.supported.ac_status.remove(ToshibaAcStatus.OFF)

    with pytest.raises(HomeAssistantError) as raised:
        await unit.set_ac_status(ToshibaAcStatus.OFF)

    assert_translated(raised.value, "unsupported_status", unit="Unit", status="OFF")

    unit._send_command.assert_not_awaited()
    assert unit.ac_status is ToshibaAcStatus.ON


DRY_ON_A_HEAT_ONLY_UNIT = "3044" + RUNNING_STATE[4:]


@pytest.mark.parametrize(
    ("status", "frame"),
    [(ToshibaAcStatus.OFF, "31ff"), (ToshibaAcStatus.ON, "30ff")],
)
async def test_a_command_that_keeps_an_unsupported_mode_is_sent(status, frame) -> None:
    """A unit in a mode outside its features must still be possible to turn off."""
    unit = make_unit(DRY_ON_A_HEAT_ONLY_UNIT, merit="0100")
    assert ToshibaAcMode.DRY not in unit.supported.ac_mode

    await unit.set_ac_status(status)

    assert unit._send_command.await_args.args[1].startswith(frame)
    assert unit.ac_status is status
    assert unit.ac_mode is ToshibaAcMode.DRY


async def test_other_settings_work_in_a_mode_outside_the_features() -> None:
    """The check looks at what a command sets, not at what the unit is doing."""
    unit = make_unit(DRY_ON_A_HEAT_ONLY_UNIT, merit="0100")

    await unit.set_ac_temperature(21)

    assert sent_state(unit).ac_temperature == 21


async def test_turning_on_in_a_mode_that_the_unit_lacks_is_refused() -> None:
    """The status in that one command must not leave without its mode."""
    unit = make_unit(UNIT_STATE, merit="0100")

    with pytest.raises(HomeAssistantError, match="COOL"):
        await unit.turn_on_in_mode(ToshibaAcMode.COOL)

    unit._send_command.assert_not_awaited()
    assert unit.ac_status is ToshibaAcStatus.OFF


async def test_turn_on_in_mode_clears_self_cleaning() -> None:
    """Turning on has to end the cleaning, as it does from the app."""
    unit = make_unit("3042124131640010171bfe0b00001802000000")
    assert unit.ac_self_cleaning is ToshibaAcSelfCleaning.ON

    await unit.turn_on_in_mode(ToshibaAcMode.HEAT)

    state = sent_state(unit)
    assert state.ac_status is ToshibaAcStatus.ON
    assert state.ac_mode is ToshibaAcMode.HEAT
    assert state.ac_self_cleaning is ToshibaAcSelfCleaning.OFF
    assert unit.ac_self_cleaning is ToshibaAcSelfCleaning.OFF


@pytest.mark.parametrize(
    ("setter", "value", "attribute", "sent"),
    [
        (
            "set_ac_merit_b",
            ToshibaAcMeritB.FIREPLACE_1,
            "ac_merit_b",
            ToshibaAcMeritB.OFF,
        ),
        ("set_ac_merit_a", ToshibaAcMeritA.FLOOR, "ac_merit_a", ToshibaAcMeritA.OFF),
        (
            "set_ac_air_pure_ion",
            ToshibaAcAirPureIon.ON,
            "ac_air_pure_ion",
            ToshibaAcAirPureIon.NONE,
        ),
        (
            "set_ac_swing_mode",
            ToshibaAcSwingMode.FIXED_1,
            "ac_swing_mode",
            ToshibaAcSwingMode.NONE,
        ),
    ],
)
async def test_what_the_unit_cannot_do_in_its_mode_is_taken_out(
    setter, value, attribute, sent
) -> None:
    """A command with a function the unit lacks in this mode has that field replaced."""
    unit = make_unit()

    await getattr(unit, setter)(value)

    assert getattr(sent_state(unit), attribute) is sent


async def test_the_led_of_a_unit_without_one_is_left_alone() -> None:
    """The merit string does not list the LED: a unit that has one reports it."""
    unit = make_unit("3042124131640010171bfe0b00001000ff0000")
    unit.fcu_state.ac_wireless_led = ToshibaAcWirelessLed.NONE

    await unit.set_ac_wireless_led(ToshibaAcWirelessLed.ON)

    assert sent_state(unit).ac_wireless_led is ToshibaAcWirelessLed.NONE


async def test_the_led_of_a_unit_with_one_is_sent() -> None:
    """It works while the unit is off too."""
    unit = make_unit()
    assert unit.ac_wireless_led is ToshibaAcWirelessLed.OFF

    await unit.set_ac_wireless_led(ToshibaAcWirelessLed.ON)

    assert sent_state(unit).ac_wireless_led is ToshibaAcWirelessLed.ON


async def test_8c_mode_shifts_the_temperature_on_the_wire() -> None:
    """In 8 °C mode the wire value is 16 degrees above the setpoint."""
    unit = make_unit()
    await unit.async_update_state("ff43ffffffff" + "04" + "ff" * 15)
    assert unit.ac_merit_a is ToshibaAcMeritA.HEATING_8C

    await unit.set_ac_temperature(10)

    assert sent_state(unit).ac_temperature == 26
    assert unit.ac_temperature == 10


async def test_outside_8c_mode_the_temperature_is_sent_as_it_is() -> None:
    """Only heating in 8 °C mode shifts it."""
    unit = make_unit()

    await unit.set_ac_temperature(24)

    assert sent_state(unit).ac_temperature == 24
    assert unit.ac_temperature == 24


async def test_independent_swing_needs_a_unit_that_reported_it() -> None:
    """Sending it to other units proves nothing, and they could misread it."""
    unit = make_unit()
    assert unit.supports_independent_swing is False

    await unit.set_ac_horizontal_swing_mode(ToshibaAcHorizontalSwingMode.SWING)

    unit._send_command.assert_not_awaited()


async def test_independent_swing_stays_known_after_a_legacy_report() -> None:
    """Units report the legacy byte again after a preset command."""
    unit = make_unit(merit="2c03", model="3")
    await unit.async_update_state(INDEPENDENT)
    assert unit.supports_independent_swing is True

    await unit.async_update_state("ffffffff" + "31" + "ff" * 17)
    assert unit.supports_independent_swing is True

    await unit.set_ac_vertical_swing_mode(ToshibaAcVerticalSwingMode.FIXED_1)
    state = sent_state(unit)
    assert state.ac_vertical_swing_mode is ToshibaAcVerticalSwingMode.FIXED_1
    assert state.uses_independent_swing


async def test_a_vane_keeps_the_other_vane() -> None:
    """Each setter sends the current position of the other one."""
    unit = make_unit()
    await unit.async_update_state(INDEPENDENT)
    vertical = unit.ac_vertical_swing_mode

    await unit.set_ac_horizontal_swing_mode(ToshibaAcHorizontalSwingMode.FIXED_2)

    state = sent_state(unit)
    assert state.ac_horizontal_swing_mode is ToshibaAcHorizontalSwingMode.FIXED_2
    assert state.ac_vertical_swing_mode is vertical


async def test_hada_is_learned_from_the_unit() -> None:
    """No merit bit says it, so it becomes available once a unit reports it."""
    unit = make_unit(merit="2c03", model="3")
    assert ToshibaAcSwingMode.HADA not in unit.supported.ac_swing_mode

    state = ToshibaAcFcuState()
    state.ac_swing_mode = ToshibaAcSwingMode.HADA
    await unit.async_update_state(state.encode())

    assert ToshibaAcSwingMode.HADA in unit.supported.ac_swing_mode
    assert unit.supported.ac_swing_mode.count(ToshibaAcSwingMode.HADA) == 1


async def test_a_heartbeat_reads_signed_temperatures_and_drops_bad_values() -> None:
    """An empty value must not cost the others, and winter is below zero."""
    unit = make_unit()
    changed = MagicMock()
    unit.on_state_changed_callback.add(changed)

    await unit.handle_cmd_heartbeat(
        {"iTemp": "15", "oTemp": "fb", "Power": "", "fanRpm": 5, "wide": "ffff"}
    )

    assert unit.ac_indoor_temperature == 21
    assert unit.ac_outdoor_temperature == -5
    changed.assert_called_once_with(unit)

    await unit.handle_cmd_heartbeat({"iTemp": "15", "oTemp": "fb"})
    changed.assert_called_once_with(unit)


async def test_a_heartbeat_with_nothing_usable_changes_nothing() -> None:
    """All values are bad."""
    unit = make_unit()
    changed = MagicMock()
    unit.on_state_changed_callback.add(changed)

    await unit.handle_cmd_heartbeat({"iTemp": None, "oTemp": ""})

    changed.assert_not_called()


async def test_energy_is_reported_when_it_changes() -> None:
    """The same reading again is no news."""
    unit = make_unit()
    changed = MagicMock()
    unit.on_energy_consumption_changed_callback.add(changed)
    reading = ToshibaAcDeviceEnergyConsumption(10, MagicMock())

    await unit.handle_update_ac_energy_consumption(reading)
    await unit.handle_update_ac_energy_consumption(reading)

    assert unit.ac_energy_consumption is reading
    changed.assert_called_once_with(unit)


async def test_callbacks_run_with_or_without_await() -> None:
    """Entities use both kinds, and each is added once."""
    callbacks: ToshibaAcCallback[str] = ToshibaAcCallback()
    plain = MagicMock(return_value=None)
    waited = AsyncMock()

    callbacks.add(plain)
    callbacks.add(plain)
    callbacks.add(waited)
    await callbacks("value")

    plain.assert_called_once_with("value")
    waited.assert_awaited_once_with("value")

    callbacks.remove(plain)
    callbacks.remove(plain)
    await callbacks("again")
    plain.assert_called_once_with("value")


async def test_connected_comes_from_the_channel() -> None:
    """Availability follows the socket."""
    connected = [True]
    unit = ToshibaAcDevice(
        "Unit",
        "ac",
        "unit-1",
        RUNNING_STATE,
        "5.0.00",
        "2c00",
        "2",
        AsyncMock(),
        lambda: connected[0],
    )

    assert unit.connected is True
    connected[0] = False
    assert unit.connected is False


@pytest.mark.parametrize(
    ("setter", "value", "byte"),
    [
        ("set_ac_vertical_swing_mode", ToshibaAcVerticalSwingMode.FIXED_1, "b1"),
        ("set_ac_horizontal_swing_mode", ToshibaAcHorizontalSwingMode.FIXED_2, "90"),
    ],
)
async def test_a_unit_that_reported_independent_vanes_can_set_them(
    setter, value, byte
) -> None:
    """Its report proves the hardware, even if its merit string does not declare it."""
    unit = make_unit(merit="2c00")
    await unit.async_update_state(INDEPENDENT)

    await getattr(unit, setter)(value)

    unit._send_command.assert_awaited_once()
    assert unit._send_command.await_args.args[1][8:10] == byte
    assert getattr(unit, setter.removeprefix("set_")) is value


async def test_a_legacy_preset_the_unit_lacks_is_still_taken_out() -> None:
    """Only a per-axis command, which needs that report, skips the check."""
    unit = make_unit(merit="2c00")
    await unit.async_update_state(INDEPENDENT)

    await unit.set_ac_swing_mode(ToshibaAcSwingMode.FIXED_1)

    assert sent_state(unit).ac_swing_mode is ToshibaAcSwingMode.NONE


HEATING = "3043124131640010171bfe0b00001002000000"
IN_8C = "3043184131640410171bfe0b00001002000000"


async def test_entering_8c_mode_sends_a_target_it_can_show() -> None:
    """The old wire value would read as 2 °C here, below what the mode allows."""
    unit = make_unit(HEATING)

    await unit.set_ac_merit_a(ToshibaAcMeritA.HEATING_8C)

    state = sent_state(unit)
    assert state.ac_merit_a is ToshibaAcMeritA.HEATING_8C
    assert state.ac_temperature == 24
    assert unit.ac_temperature == 8


async def test_leaving_8c_mode_goes_back_to_the_earlier_target() -> None:
    """The wire value of 8 °C mode would read as 29 °C, and the unit could heat to it."""
    unit = make_unit(HEATING)
    await unit.set_ac_merit_a(ToshibaAcMeritA.HEATING_8C)
    await unit.set_ac_temperature(13)
    unit._send_command.reset_mock()

    await unit.set_ac_merit_a(ToshibaAcMeritA.OFF)

    state = sent_state(unit)
    assert state.ac_merit_a is ToshibaAcMeritA.OFF
    assert state.ac_temperature == 18
    assert unit.ac_temperature == 18


async def test_leaving_8c_mode_without_an_earlier_target_uses_a_safe_one() -> None:
    """After a restart nothing is known about the time before the mode."""
    unit = make_unit(IN_8C)
    assert unit.ac_temperature == 8

    await unit.set_ac_merit_a(ToshibaAcMeritA.OFF)

    assert sent_state(unit).ac_temperature == 20
    assert unit.ac_temperature == 20


@pytest.mark.parametrize(
    ("setter", "value"),
    [
        ("set_ac_merit_a", ToshibaAcMeritA.ECO),
        ("set_ac_mode", ToshibaAcMode.COOL),
    ],
)
async def test_every_way_out_of_8c_mode_corrects_the_target(setter, value) -> None:
    """Another function in the same field, or a mode without 8 °C, also ends it."""
    unit = make_unit(IN_8C)

    await getattr(unit, setter)(value)

    state = sent_state(unit)
    assert state.ac_temperature == 20
    assert unit.ac_merit_a is not ToshibaAcMeritA.HEATING_8C
    assert unit.ac_temperature == 20


async def test_other_commands_in_8c_mode_leave_the_target_alone() -> None:
    """Only entering and leaving touch it."""
    unit = make_unit(IN_8C)

    await unit.set_ac_fan_mode(ToshibaAcFanMode.LOW)

    assert sent_state(unit).ac_temperature is None
    assert unit.ac_temperature == 8


async def test_a_temperature_in_8c_mode_is_not_taken_for_the_earlier_one() -> None:
    """Leaving later must not bring a frost target back as a normal one."""
    unit = make_unit(HEATING)
    await unit.set_ac_temperature(22)
    await unit.set_ac_merit_a(ToshibaAcMeritA.HEATING_8C)
    await unit.set_ac_temperature(6)
    await unit.set_ac_merit_a(ToshibaAcMeritA.OFF)

    assert unit.ac_temperature == 22


ENTERED_IN_THE_APP = "ffffffffffff04" + "ff" * 12
LEFT_IN_THE_APP = "ffffffffffff00" + "ff" * 12


async def test_leaving_8c_mode_forgets_the_earlier_target() -> None:
    """A later visit that began in the app must not bring back an old target."""
    unit = make_unit(HEATING)
    await unit.set_ac_temperature(24)
    await unit.set_ac_merit_a(ToshibaAcMeritA.HEATING_8C)
    await unit.set_ac_merit_a(ToshibaAcMeritA.OFF)
    assert unit.ac_temperature == 24
    await unit.set_ac_temperature(19)

    await unit.async_update_state(ENTERED_IN_THE_APP)
    await unit.set_ac_merit_a(ToshibaAcMeritA.OFF)

    assert unit.ac_temperature == 20


async def test_leaving_8c_mode_in_the_app_forgets_the_earlier_target_too() -> None:
    """The target saved on the way in belongs to that visit, however it ended."""
    unit = make_unit(HEATING)
    await unit.set_ac_temperature(24)
    await unit.set_ac_merit_a(ToshibaAcMeritA.HEATING_8C)

    await unit.async_update_state(LEFT_IN_THE_APP)
    await unit.async_update_state(ENTERED_IN_THE_APP)
    await unit.set_ac_merit_a(ToshibaAcMeritA.OFF)

    assert unit.ac_temperature == 20


async def test_a_failed_exit_from_8c_mode_keeps_the_earlier_target() -> None:
    """The unit is still in the mode, and the next attempt needs the value."""
    unit = make_unit(HEATING)
    await unit.set_ac_temperature(24)
    await unit.set_ac_merit_a(ToshibaAcMeritA.HEATING_8C)
    unit._send_command.side_effect = RuntimeError("refused")

    with pytest.raises(RuntimeError):
        await unit.set_ac_merit_a(ToshibaAcMeritA.OFF)
    assert unit.ac_merit_a is ToshibaAcMeritA.HEATING_8C

    unit._send_command.side_effect = None
    await unit.set_ac_merit_a(ToshibaAcMeritA.OFF)
    assert unit.ac_temperature == 24


async def test_an_echo_during_an_exit_from_8c_mode_does_not_restore_the_target() -> (
    None
):
    """An echo can arrive before Toshiba's answer to the command."""
    unit = make_unit(HEATING)
    await unit.set_ac_temperature(24)
    await unit.set_ac_merit_a(ToshibaAcMeritA.HEATING_8C)

    async def echo(_unit_id: str, frame: str) -> None:
        await unit.async_update_state(frame[:38])

    unit._send_command.side_effect = echo
    await unit.set_ac_merit_a(ToshibaAcMeritA.OFF)
    assert unit.ac_temperature == 24
    unit._send_command.side_effect = None

    await unit.async_update_state(ENTERED_IN_THE_APP)
    await unit.set_ac_merit_a(ToshibaAcMeritA.OFF)
    assert unit.ac_temperature == 20
