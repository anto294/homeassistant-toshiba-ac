"""Tests that the platforms load on a real core and control the unit correctly."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from custom_components.toshiba_ac_link.toshiba.properties import (
    ToshibaAcAirPureIon,
    ToshibaAcHorizontalSwingMode,
    ToshibaAcMeritA,
    ToshibaAcMeritB,
    ToshibaAcMode,
    ToshibaAcSelfCleaning,
    ToshibaAcStatus,
    ToshibaAcSwingMode,
    ToshibaAcVerticalSwingMode,
    ToshibaAcWirelessLed,
)

from custom_components.toshiba_ac_link.climate import ToshibaClimate
from custom_components.toshiba_ac_link.const import DOMAIN

from .conftest import async_add_entry, make_device, make_manager


def test_all_platforms_create_entities(run_in_hass) -> None:
    """A typical unit gets its entities, with ids built from the device name."""

    async def scenario(hass: HomeAssistant) -> None:
        await async_add_entry(hass, make_manager())

        state = hass.states.get("climate.living_room")
        assert state is not None
        assert state.state == "heat"
        assert state.attributes["temperature"] == 21
        assert state.attributes["current_temperature"] == 20

        assert hass.states.get("sensor.living_room_indoor_temperature").state == "20"
        assert hass.states.get("sensor.living_room_outdoor_temperature").state == "7"
        assert hass.states.get("binary_sensor.living_room_self_cleaning").state == "off"
        assert hass.states.get("switch.living_room_wireless_led").state == "on"
        assert hass.states.get("switch.living_room_eco_mode").state == "off"
        assert hass.states.get("select.living_room_fireplace_mode") is not None
        assert (
            hass.states.get("sensor.living_room_energy_consumption").state == "unknown"
        )

        registry = er.async_get(hass)
        entry = registry.async_get("climate.living_room")
        assert entry.unique_id == "unit-1_climate"
        assert entry.platform == DOMAIN

    run_in_hass(scenario)


def test_unsupported_functions_create_no_entities(run_in_hass) -> None:
    """A unit without the LED or self cleaning does not get their entities."""

    async def scenario(hass: HomeAssistant) -> None:
        device = make_device(
            ac_wireless_led=ToshibaAcWirelessLed.NONE,
            ac_self_cleaning=ToshibaAcSelfCleaning.NONE,
        )
        device.supported.ac_energy_report = False
        await async_add_entry(hass, make_manager([device]))

        assert hass.states.get("switch.living_room_wireless_led") is None
        assert hass.states.get("binary_sensor.living_room_self_cleaning") is None
        assert hass.states.get("sensor.living_room_energy_consumption") is None

    run_in_hass(scenario)


def test_outdoor_temperature_unavailable_without_a_value(run_in_hass) -> None:
    """The outdoor unit reports nothing while it is off."""

    async def scenario(hass: HomeAssistant) -> None:
        device = make_device(ac_outdoor_temperature=None)
        await async_add_entry(hass, make_manager([device]))

        state = hass.states.get("sensor.living_room_outdoor_temperature")
        assert state.state == "unavailable"

    run_in_hass(scenario)


def test_wireless_led_works_while_the_ac_is_off(run_in_hass) -> None:
    """Unlike the other switches, the LED does not need a running unit."""

    async def scenario(hass: HomeAssistant) -> None:
        device = make_device(ac_status=ToshibaAcStatus.OFF)
        await async_add_entry(hass, make_manager([device]))

        assert hass.states.get("switch.living_room_eco_mode").state == "unavailable"
        assert hass.states.get("switch.living_room_wireless_led").state == "on"

        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": "switch.living_room_wireless_led"},
            blocking=True,
        )
        device.set_ac_wireless_led.assert_awaited_once_with(ToshibaAcWirelessLed.OFF)

    run_in_hass(scenario)


def test_energy_read_when_added(run_in_hass) -> None:
    """A value fetched before the entity subscribed is not lost for an hour."""

    async def scenario(hass: HomeAssistant) -> None:
        device = make_device()
        device.ac_energy_consumption = MagicMock(energy_wh=1234.0, since=None)
        await async_add_entry(hass, make_manager([device]))

        state = hass.states.get("sensor.living_room_energy_consumption")
        assert state.state == "1234.0"

    run_in_hass(scenario)


def test_self_cleaning_ac_shows_as_off(run_in_hass) -> None:
    """A cleaning unit reports ON, but the user turned it off."""

    async def scenario(hass: HomeAssistant) -> None:
        device = make_device(ac_self_cleaning=ToshibaAcSelfCleaning.ON)
        await async_add_entry(hass, make_manager([device]))

        assert hass.states.get("climate.living_room").state == "off"
        assert hass.states.get("binary_sensor.living_room_self_cleaning").state == "on"

    run_in_hass(scenario)


@pytest.mark.parametrize(
    ("model_id", "merit", "minimum", "maximum"),
    [
        ("2", ToshibaAcMeritA.OFF, 17, 30),
        ("3", ToshibaAcMeritA.OFF, 17, 30),
        ("2", ToshibaAcMeritA.HEATING_8C, 5, 13),
        ("3", ToshibaAcMeritA.HEATING_8C, 5, 16),
    ],
)
async def test_temperature_range(model_id, merit, minimum, maximum) -> None:
    """The limits depend on frost protection and on the model."""
    device = make_device(ac_model_id=model_id, ac_merit_a=merit)
    climate = ToshibaClimate(device)

    assert (climate.min_temp, climate.max_temp) == (minimum, maximum)

    await climate.async_set_temperature(temperature=99)
    device.set_ac_temperature.assert_awaited_once_with(maximum)

    device.set_ac_temperature.reset_mock()
    await climate.async_set_temperature(temperature=1)
    device.set_ac_temperature.assert_awaited_once_with(minimum)


@pytest.mark.parametrize(
    ("name", "vertical"),
    [
        ("Off", ToshibaAcVerticalSwingMode.NONE),
        ("Fixed 2", ToshibaAcVerticalSwingMode.FIXED_2),
        ("Swing Vertical", ToshibaAcVerticalSwingMode.SWING),
    ],
)
async def test_vertical_swing_keeps_horizontal_on_independent_units(
    name, vertical
) -> None:
    """The legacy preset would reset the horizontal vane."""
    device = make_device(supports_independent_swing=True)

    await ToshibaClimate(device).async_set_swing_mode(name)

    device.set_ac_vertical_swing_mode.assert_awaited_once_with(vertical)
    device.set_ac_swing_mode.assert_not_awaited()


async def test_combined_swing_preset_stays_legacy() -> None:
    """A preset that describes both vanes has no per-axis equivalent."""
    device = make_device(supports_independent_swing=True)

    await ToshibaClimate(device).async_set_swing_mode("Swing Vertical And Horizontal")

    device.set_ac_swing_mode.assert_awaited_once_with(
        ToshibaAcSwingMode.SWING_VERTICAL_AND_HORIZONTAL
    )
    device.set_ac_vertical_swing_mode.assert_not_awaited()


async def test_other_units_use_the_legacy_swing() -> None:
    """Units without independent axes only know the presets."""
    device = make_device()

    await ToshibaClimate(device).async_set_swing_mode("Fixed 2")

    device.set_ac_swing_mode.assert_awaited_once_with(ToshibaAcSwingMode.FIXED_2)
    device.set_ac_vertical_swing_mode.assert_not_awaited()


async def test_horizontal_swing_only_on_independent_units() -> None:
    """The horizontal controls depend on what the unit has reported."""
    other = ToshibaClimate(make_device())
    assert other.swing_horizontal_modes is None
    assert other.swing_horizontal_mode is None

    device = make_device(supports_independent_swing=True)
    climate = ToshibaClimate(device)
    assert climate.swing_horizontal_mode == "Off"
    assert "Off" in climate.swing_horizontal_modes

    await climate.async_set_swing_horizontal_mode("Swing")
    device.set_ac_horizontal_swing_mode.assert_awaited_once_with(
        ToshibaAcHorizontalSwingMode.SWING
    )


def test_commands_reach_the_unit(run_in_hass) -> None:
    """Switches, selects and the climate entity call the right setter."""

    async def scenario(hass: HomeAssistant) -> None:
        device = make_device()
        await async_add_entry(hass, make_manager([device]))

        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": "switch.living_room_eco_mode"},
            blocking=True,
        )
        device.set_ac_merit_a.assert_awaited_once_with(ToshibaAcMeritA.ECO)

        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": "switch.living_room_air_purifier"},
            blocking=True,
        )
        device.set_ac_air_pure_ion.assert_awaited_once_with(ToshibaAcAirPureIon.ON)

        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": "select.living_room_fireplace_mode", "option": "fireplace_1"},
            blocking=True,
        )
        device.set_ac_merit_b.assert_awaited_once_with(ToshibaAcMeritB.FIREPLACE_1)

        await hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": "climate.living_room", "hvac_mode": "cool"},
            blocking=True,
        )
        device.set_ac_mode.assert_awaited_once_with(ToshibaAcMode.COOL)

        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": "climate.living_room", "temperature": 23},
            blocking=True,
        )
        device.set_ac_temperature.assert_awaited_once_with(23)

    run_in_hass(scenario)


def test_pushed_state_reaches_the_entities(run_in_hass) -> None:
    """A state pushed by the unit updates the entities without polling."""

    async def scenario(hass: HomeAssistant) -> None:
        device = make_device()
        callbacks = []
        device.on_state_changed_callback.add = callbacks.append
        await async_add_entry(hass, make_manager([device]))

        device.ac_temperature = 25
        for callback in callbacks:
            callback(device)
        await hass.async_block_till_done()

        assert hass.states.get("climate.living_room").attributes["temperature"] == 25

    run_in_hass(scenario)
