"""Tests of the state encoding and the features, which need no Home Assistant."""

from __future__ import annotations

import pytest

from custom_components.toshiba_ac_link.toshiba.features import ToshibaAcFeatures
from custom_components.toshiba_ac_link.toshiba.fcu_state import ToshibaAcFcuState
from custom_components.toshiba_ac_link.toshiba.properties import (
    ToshibaAcAirPureIon,
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

from .fake_toshiba import RUNNING_STATE

SETTINGS = [
    ("ac_status", ToshibaAcStatus),
    ("ac_mode", ToshibaAcMode),
    ("ac_fan_mode", ToshibaAcFanMode),
    ("ac_swing_mode", ToshibaAcSwingMode),
    ("ac_power_selection", ToshibaAcPowerSelection),
    ("ac_merit_b", ToshibaAcMeritB),
    ("ac_merit_a", ToshibaAcMeritA),
    ("ac_air_pure_ion", ToshibaAcAirPureIon),
    ("ac_self_cleaning", ToshibaAcSelfCleaning),
    ("ac_wireless_led", ToshibaAcWirelessLed),
]


@pytest.mark.parametrize(
    ("name", "value"),
    [(name, value) for name, enum in SETTINGS for value in enum],
    ids=lambda item: getattr(item, "name", item),
)
def test_every_value_survives_the_wire_format(name, value) -> None:
    """A value that does not come back would be a wrong command or a wrong state."""
    state = ToshibaAcFcuState()
    setattr(state, name, value)

    assert getattr(ToshibaAcFcuState.from_hex_state(state.encode()), name) is value


@pytest.mark.parametrize(
    ("name", "offset"),
    [
        ("ac_status", 0),
        ("ac_mode", 2),
        ("ac_fan_mode", 6),
        ("ac_power_selection", 10),
        ("ac_air_pure_ion", 14),
    ],
)
def test_a_byte_that_is_not_known_decodes_to_none(name, offset) -> None:
    """Toshiba keeps adding values for new hardware, and they must not raise."""
    hex_state = "ff" * 19
    hex_state = hex_state[:offset] + "7b" + hex_state[offset + 2 :]

    assert getattr(ToshibaAcFcuState.from_hex_state(hex_state), name).name == "NONE"


def test_an_empty_state_means_nothing_to_change() -> None:
    """The fields that a command does not set are all ff."""
    assert ToshibaAcFcuState().encode() == "ff" * 19


def test_a_partial_state_changes_only_its_own_fields() -> None:
    """A push has ff in the fields that did not change."""
    state = ToshibaAcFcuState.from_hex_state(RUNNING_STATE)

    assert state.update("ffff14" + "ff" * 19) is True
    assert state.ac_temperature == 20
    assert state.ac_status is ToshibaAcStatus.ON
    assert state.ac_mode is ToshibaAcMode.COOL

    assert state.update("ffff14" + "ff" * 19) is False


@pytest.mark.parametrize(
    ("given", "sent"),
    [
        (21, 21),
        (21.4, 21),
        (21.5, 22),
        (22.5, 23),
        (21.999999, 22),
        (-1, -1),
        (-2.5, -3),
    ],
)
def test_temperatures_round_half_up_to_whole_degrees(given, sent) -> None:
    """Home Assistant sends floats, and the wire only has whole degrees."""
    state = ToshibaAcFcuState()
    state.ac_temperature = given

    assert ToshibaAcFcuState.from_hex_state(state.encode()).ac_temperature == sent


def test_a_temperature_can_be_absent() -> None:
    """None is what a state without this field decodes to."""
    state = ToshibaAcFcuState()
    state.ac_temperature = None

    assert state.ac_temperature is None


def test_both_vanes_are_encoded_independently() -> None:
    """Newer units report each vane on its own, in the swing byte."""
    state = ToshibaAcFcuState()
    state.set_ac_independent_swing(
        ToshibaAcVerticalSwingMode.FIXED_2, ToshibaAcHorizontalSwingMode.SWING
    )

    decoded = ToshibaAcFcuState.from_hex_state(state.encode())
    assert decoded.uses_independent_swing
    assert decoded.ac_vertical_swing_mode is ToshibaAcVerticalSwingMode.FIXED_2
    assert decoded.ac_horizontal_swing_mode is ToshibaAcHorizontalSwingMode.SWING
    assert "Vertical" in str(decoded) or "vertical" in str(decoded)


@pytest.mark.parametrize(
    ("vertical", "horizontal", "legacy"),
    [
        (
            ToshibaAcVerticalSwingMode.SWING,
            ToshibaAcHorizontalSwingMode.SWING,
            ToshibaAcSwingMode.SWING_VERTICAL_AND_HORIZONTAL,
        ),
        (
            ToshibaAcVerticalSwingMode.SWING,
            ToshibaAcHorizontalSwingMode.NONE,
            ToshibaAcSwingMode.SWING_VERTICAL,
        ),
        (
            ToshibaAcVerticalSwingMode.NONE,
            ToshibaAcHorizontalSwingMode.SWING,
            ToshibaAcSwingMode.SWING_HORIZONTAL,
        ),
        (
            ToshibaAcVerticalSwingMode.FIXED_3,
            ToshibaAcHorizontalSwingMode.NONE,
            ToshibaAcSwingMode.FIXED_3,
        ),
        (
            ToshibaAcVerticalSwingMode.NONE,
            ToshibaAcHorizontalSwingMode.NONE,
            ToshibaAcSwingMode.OFF,
        ),
    ],
)
def test_independent_vanes_also_read_as_a_preset(vertical, horizontal, legacy) -> None:
    """The swing list of the climate entity still shows one of its presets."""
    state = ToshibaAcFcuState()
    state.set_ac_independent_swing(vertical, horizontal)

    assert state.ac_swing_mode is legacy


@pytest.mark.parametrize(
    ("legacy", "vertical", "horizontal"),
    [
        (
            ToshibaAcSwingMode.SWING_VERTICAL,
            ToshibaAcVerticalSwingMode.SWING,
            ToshibaAcHorizontalSwingMode.NONE,
        ),
        (
            ToshibaAcSwingMode.SWING_HORIZONTAL,
            ToshibaAcVerticalSwingMode.NONE,
            ToshibaAcHorizontalSwingMode.SWING,
        ),
    ],
)
def test_a_preset_also_reads_as_vanes(legacy, vertical, horizontal) -> None:
    """A unit that got a preset command reports the legacy byte again."""
    state = ToshibaAcFcuState()
    state.ac_swing_mode = legacy

    assert not state.uses_independent_swing
    assert state.ac_vertical_swing_mode is vertical
    assert state.ac_horizontal_swing_mode is horizontal


def test_a_state_can_be_described_for_the_log() -> None:
    """The text names the values."""
    text = str(ToshibaAcFcuState.from_hex_state(RUNNING_STATE))

    assert "ON" in text and "COOL" in text and "18" in text


def test_the_merit_string_of_the_tested_model_2_units() -> None:
    """2c00: 8 °C, fireplace and silent, without purifier or energy."""
    features = ToshibaAcFeatures.from_merit_string_and_model("2c00", "2")

    assert ToshibaAcMeritA.HEATING_8C in features.ac_merit_a
    assert ToshibaAcMeritA.CDU_SILENT_1 in features.ac_merit_a
    assert ToshibaAcMeritB.FIREPLACE_1 in features.ac_merit_b
    assert ToshibaAcAirPureIon.ON not in features.ac_air_pure_ion
    assert features.ac_energy_report is False
    assert "Supported AC modes" in str(features)


def test_the_merit_string_of_the_tested_model_3_units() -> None:
    """2c03 adds the energy report and fixed vane positions."""
    features = ToshibaAcFeatures.from_merit_string_and_model("2c03", "3")

    assert features.ac_energy_report is True
    assert ToshibaAcSwingMode.FIXED_1 in features.ac_swing_mode
    assert ToshibaAcSwingMode.HADA not in features.ac_swing_mode


def test_a_merit_string_can_add_the_other_functions() -> None:
    """Bits that the tested units do not have."""
    features = ToshibaAcFeatures.from_merit_string_and_model("d300", "2")

    assert ToshibaAcMeritA.FLOOR in features.ac_merit_a
    assert ToshibaAcAirPureIon.ON in features.ac_air_pure_ion
    assert ToshibaAcSwingMode.SWING_HORIZONTAL in features.ac_swing_mode


def test_a_short_merit_string_is_filled_up() -> None:
    """Older units can report fewer characters."""
    assert ToshibaAcFeatures.from_merit_string_and_model("2c", "2").ac_mode


@pytest.mark.parametrize(
    ("mode", "allowed"),
    [
        (ToshibaAcMode.HEAT, True),
        (ToshibaAcMode.COOL, False),
        (ToshibaAcMode.NONE, True),
    ],
)
def test_functions_depend_on_the_mode(mode, allowed) -> None:
    """8 °C only heats, and a mode that is not known disables nothing."""
    features = ToshibaAcFeatures.from_merit_string_and_model("2c00", "2")

    assert (ToshibaAcMeritA.HEATING_8C in features.for_ac_mode(mode).ac_merit_a) is (
        allowed
    )
