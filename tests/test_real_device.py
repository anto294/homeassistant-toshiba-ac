"""Tests of entities on the real ToshibaAcDevice, against a local stand-in for Toshiba.

The mock in conftest.py answers everything, so it cannot show what these look for.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.toshiba_ac_link.toshiba.device import ToshibaAcDevice
from custom_components.toshiba_ac_link.toshiba.properties import ToshibaAcMode

from .fake_toshiba import (
    OTHER_UNIT_ID,
    RUNNING_STATE,
    FakeToshiba,
    add_real_entry,
    run_fake_toshiba,
    wait_until,
)

CLIMATE = "climate.living_room"


async def the_unit(entry: ConfigEntry) -> ToshibaAcDevice:
    """Return the first unit."""
    return (await entry.runtime_data.get_devices())[0]


def second_unit() -> dict:
    """Return another unit of the account."""
    return {
        **FakeToshiba.unit(),
        "Id": "ac-2",
        "DeviceUniqueId": OTHER_UNIT_ID,
        "Name": "Bedroom",
    }


def test_unknown_mode_at_start_keeps_the_entities_of_all_units(run_in_hass) -> None:
    """A mode of 00 on one unit used to raise while the platforms were set up."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.units[0]["ACStateData"] = "3000" + RUNNING_STATE[4:]
            fake.units.append(second_unit())
            entry = await add_real_entry(hass)

            assert entry.state is ConfigEntryState.LOADED
            assert hass.states.get("select.living_room_fireplace_mode") is not None
            assert hass.states.get("select.bedroom_fireplace_mode") is not None
            assert hass.states.get("switch.living_room_eco_mode") is not None
            assert hass.states.get(CLIMATE).state == "unknown"
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_pushed_unknown_mode_keeps_entities_and_commands_working(run_in_hass) -> None:
    """A new mode byte used to freeze the entities and fail every command."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.units[0]["ACStateData"] = RUNNING_STATE
            entry = await add_real_entry(hass)
            unit = await the_unit(entry)
            assert hass.states.get(CLIMATE).state == "cool"

            await fake.publish(
                "CMD_FCU_FROM_AC",
                {"data": "3046ffffffff00ffffffffffffffffffffffffffffff"},
            )
            await wait_until(lambda: unit.ac_mode is ToshibaAcMode.NONE)
            await hass.async_block_till_done()
            assert hass.states.get(CLIMATE).state == "unknown"

            await hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": CLIMATE, "temperature": 24},
                blocking=True,
            )
            assert fake.requests["command"] == 1
            assert hass.states.get(CLIMATE).attributes["temperature"] == 24

            # A later known mode brings the entity back
            await fake.publish(
                "CMD_FCU_FROM_AC",
                {"data": "3042ffffffff00ffffffffffffffffffffffffffffff"},
            )
            await wait_until(lambda: unit.ac_mode is ToshibaAcMode.COOL)
            await hass.async_block_till_done()
            assert hass.states.get(CLIMATE).state == "cool"
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)
