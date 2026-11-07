"""Tests of entities on the real ToshibaAcDevice, against a local stand-in for Toshiba.

The mock in conftest.py answers everything, so it cannot show what these look for.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.toshiba_ac_link.toshiba.api import ToshibaApi

from custom_components.toshiba_ac_link.toshiba.device import ToshibaAcDevice
from custom_components.toshiba_ac_link.toshiba.properties import ToshibaAcMode

from .fake_toshiba import (
    OTHER_UNIT_ID,
    RUNNING_STATE,
    UNIT_ID,
    FakeToshiba,
    add_real_entry,
    run_fake_toshiba,
    wait_until,
)

CLIMATE = "climate.living_room"
MANAGER = "custom_components.toshiba_ac_link.toshiba.manager"
REALTIME = "custom_components.toshiba_ac_link.toshiba.realtime"


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


@pytest.mark.parametrize(
    "change",
    [
        {"ACStateData": ""},
        {"ACStateData": None},
        {"ACStateData": "3142"},
        {"ACStateData": "not hex at all, and long enough to reach the end of a state"},
        {"MeritFeature": None},
        {"MeritFeature": "zzzz"},
        {"DeviceUniqueId": None},
        {"Name": None, "Id": None, "FirmwareVersion": None, "ACStateData": 5},
    ],
)
def test_a_malformed_unit_does_not_cost_the_others(run_in_hass, change) -> None:
    """One unit with bad data used to stop the whole account, with no retry."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.units[0].update(change)
            fake.units.append(second_unit())
            entry = await add_real_entry(hass)

            assert entry.state is ConfigEntryState.LOADED
            assert [u.name for u in await entry.runtime_data.get_devices()] == [
                "Bedroom"
            ]
            assert hass.states.get("climate.bedroom") is not None
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_a_unit_without_its_fields_does_not_cost_the_others(run_in_hass) -> None:
    """A missing field or a value that is no object is also one bad unit."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.units = [{"Id": "x"}, "text", second_unit()]
            entry = await add_real_entry(hass)
            assert entry.state is ConfigEntryState.LOADED
            assert hass.states.get("climate.bedroom") is not None
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_no_readable_unit_is_a_retry(run_in_hass) -> None:
    """Loading with nothing would look fine, and stay empty until the next restart."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.units[0]["ACStateData"] = ""
            entry = await add_real_entry(hass)
            assert entry.state is ConfigEntryState.SETUP_RETRY
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_an_account_without_units_still_loads(run_in_hass) -> None:
    """No units is a valid answer, unlike units that cannot be read."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.units = []
            entry = await add_real_entry(hass)
            assert entry.state is ConfigEntryState.LOADED
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_a_short_state_in_a_refresh_does_not_end_the_refreshes(run_in_hass) -> None:
    """It used to leave the periodic task with an IndexError or a struct.error."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_real_entry(hass)
            with (
                patch(f"{MANAGER}.REFRESH_S", 0.05),
                patch(f"{MANAGER}.REFRESH_MIN_S", 0.01),
            ):
                await hass.config_entries.async_reload(entry.entry_id)
                await hass.async_block_till_done()
                for state in ("", "3142", "31" * 8):
                    fake.units[0]["ACStateData"] = state
                    seen = fake.requests["mapping"]
                    await wait_until(lambda: fake.requests["mapping"] > seen)

                fake.units[0]["ACStateData"] = RUNNING_STATE
                await wait_until(lambda: hass.states.get(CLIMATE).state == "cool")
                await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_a_short_pushed_state_does_not_end_the_channel(run_in_hass) -> None:
    """A short state is ignored, and the next good one still arrives."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_real_entry(hass)
            unit = await the_unit(entry)
            for state in ("", "30", "30424"):
                await fake.publish("CMD_FCU_FROM_AC", {"data": state})
            await fake.publish(
                "CMD_FCU_FROM_AC",
                {"data": "30ffffffffffffffffffffffffffffffffffffffffff"},
            )
            await wait_until(lambda: unit.ac_status.name == "ON")
            assert len(fake.sockets) == 1
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_an_unexpected_error_in_a_periodic_request_is_logged_and_survived(
    run_in_hass, caplog
) -> None:
    """Whatever goes wrong, the next turn still happens, with no immediate retry."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            real = ToshibaApi.async_get_groups
            calls = []

            async def fail_once(api: ToshibaApi) -> list:
                calls.append(1)
                if len(calls) == 2:
                    raise RuntimeError("surprise")
                return await real(api)

            with (
                patch.object(ToshibaApi, "async_get_groups", fail_once),
                patch(f"{MANAGER}.REFRESH_S", 0.05),
                patch(f"{MANAGER}.REFRESH_MIN_S", 0.01),
            ):
                entry = await add_real_entry(hass)
                await hass.config_entries.async_reload(entry.entry_id)
                await hass.async_block_till_done()
                fake.units[0]["ACStateData"] = RUNNING_STATE
                await wait_until(lambda: len(calls) >= 3)
                await wait_until(lambda: hass.states.get(CLIMATE).state == "cool")
                await hass.config_entries.async_unload(entry.entry_id)

        assert "Periodic request failed unexpectedly" in caplog.text

    run_in_hass(scenario)


@pytest.mark.parametrize(
    "answer", [{"Url": None}, {"Url": 5}, {"Url": ["x"]}, {}, None, "text"]
)
def test_a_bad_channel_address_is_a_failed_attempt(run_in_hass, caplog, answer) -> None:
    """A null address used to end the realtime task with a TypeError, for good."""

    async def scenario(hass: HomeAssistant) -> None:
        caplog.set_level(logging.DEBUG)
        async with run_fake_toshiba() as fake:
            fake.bad_channel_answers.append(answer)
            with (
                patch(f"{REALTIME}.RETRY_MIN_S", 0.05),
                patch(f"{MANAGER}.FIRST_CONNECTION_WAIT_S", 0.01),
            ):
                entry = await add_real_entry(hass)
                unit = await the_unit(entry)
                await wait_until(lambda: unit.connected)
                await hass.config_entries.async_unload(entry.entry_id)

        # The fake server's own access log shows the address, so look only at ours
        ours = [r for r in caplog.records if r.name.startswith("custom_components")]
        assert ours
        assert not [r for r in ours if "secret-url" in r.getMessage()]

    run_in_hass(scenario)


def test_the_energy_sensor_follows_the_connection(run_in_hass) -> None:
    """It only listened for new readings, so availability stayed as it was at start."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.units = [FakeToshiba.unit(model="3", merit="2c03")]
            fake.refuse_sockets = True
            energy = "sensor.living_room_energy_consumption"
            with (
                patch(f"{REALTIME}.RETRY_MIN_S", 0.05),
                patch(f"{MANAGER}.FIRST_CONNECTION_WAIT_S", 0.01),
            ):
                entry = await add_real_entry(hass)
                await hass.async_block_till_done()
                assert hass.states.get(energy).state == "unavailable"

                fake.refuse_sockets = False
                await wait_until(lambda: hass.states.get(energy).state == "2000")

                await fake.sockets[-1].close()
                await wait_until(lambda: hass.states.get(energy).state == "unavailable")
                await wait_until(lambda: hass.states.get(energy).state == "2000")

                registry = er.async_get(hass)
                assert registry.async_get(energy).unique_id == f"{UNIT_ID}_sensor"
                await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)
