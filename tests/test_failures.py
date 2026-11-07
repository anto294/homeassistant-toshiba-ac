"""Tests of what the client does with answers it did not hope for."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.toshiba_ac_link.const import DOMAIN
from custom_components.toshiba_ac_link.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.toshiba_ac_link.toshiba import api as api_module
from custom_components.toshiba_ac_link.toshiba import guard as guard_module
from custom_components.toshiba_ac_link.toshiba.api import parse_units

from .conftest import ACCOUNT
from .fake_toshiba import (
    RUNNING_STATE,
    FakeToshiba,
    add_real_entry,
    run_fake_toshiba,
    wait_until,
)

CLIMATE = "climate.living_room"
MANAGER = "custom_components.toshiba_ac_link.toshiba.manager"
REALTIME = "custom_components.toshiba_ac_link.toshiba.realtime"


async def send_form(hass: HomeAssistant, times: int = 1) -> dict:
    """Send the setup form, and return the last result."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    for _ in range(times):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(ACCOUNT)
        )
    return result


def failed_answer(status_code: str, message: str = "") -> str:
    """Return Toshiba's envelope for a refused request."""
    return json.dumps(
        {"IsSuccess": False, "StatusCode": status_code, "Message": message}
    )


async def set_temperature(hass: HomeAssistant, temperature: int = 24) -> None:
    """Send a command through the climate entity."""
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": CLIMATE, "temperature": temperature},
        blocking=True,
    )


async def stay_loaded_with_quick_refreshes(hass: HomeAssistant) -> ConfigEntry:
    """Add an entry whose periodic list requests come at once."""
    entry = await add_real_entry(hass)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# Logins


@pytest.mark.parametrize(
    ("answer", "error", "counted"),
    [
        ("not json", "cannot_connect", False),
        ("[1, 2]", "cannot_connect", False),
        (json.dumps({"IsSuccess": True, "ResObj": {}}), "cannot_connect", False),
        (json.dumps({"IsSuccess": True, "ResObj": None}), "cannot_connect", False),
        (failed_answer("Other", "Server busy"), "cannot_connect", True),
        (failed_answer("InvalidUserNameorPassword"), "invalid_auth", True),
        (failed_answer("Failed", "Account locked"), "invalid_auth", True),
    ],
)
def test_the_answers_to_a_login(run_in_hass, answer, error, counted) -> None:
    """Each answer gets the right text, and a refusal counts toward the limit."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.raw_answers["login"] = answer
            result = await send_form(hass)
            assert result["errors"] == {"base": error}
            guard = hass.data[guard_module._DATA_GUARD]
            assert bool(guard._recent_failures(ACCOUNT["username"])) is counted

    run_in_hass(scenario)


def test_a_rejected_login_status_counts(run_in_hass) -> None:
    """A 401 on the login says the password is wrong."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.statuses["login"] = 401
            result = await send_form(hass)
            assert result["errors"] == {"base": "invalid_auth"}

    run_in_hass(scenario)


def test_an_unreachable_server_is_a_connection_error(run_in_hass) -> None:
    """Nothing was refused, so nothing counts against the account."""

    async def scenario(hass: HomeAssistant) -> None:
        with patch.object(api_module, "BASE_URL", "http://127.0.0.1:9"):
            result = await send_form(hass)
        assert result["errors"] == {"base": "cannot_connect"}
        guard = hass.data[guard_module._DATA_GUARD]
        assert guard.diagnostics()["block_count"] == 0
        assert guard._recent_failures(ACCOUNT["username"]) == []

    run_in_hass(scenario)


def test_a_retry_after_that_is_not_a_number_uses_the_normal_wait(run_in_hass) -> None:
    """The header can hold a date, which is not worth reading."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.statuses["login"] = 429
            fake.headers["Retry-After"] = "Sat, 07 Nov 2026 12:00:00 GMT"
            result = await send_form(hass)
            assert result["errors"] == {"base": "too_many_attempts"}
            guard = hass.data[guard_module._DATA_GUARD]
            assert 0 < guard._data["blocked_until"] - time.time() <= 1100

    run_in_hass(scenario)


# Setup


@pytest.mark.parametrize(
    "answer",
    [
        "not json",
        json.dumps({"IsSuccess": True, "ResObj": {"not": "a list"}}),
        failed_answer("Other"),
    ],
)
def test_a_bad_list_of_units_is_a_retry(run_in_hass, answer) -> None:
    """It does not ask for a password, and it is not a block."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.raw_answers["mapping"] = answer
            entry = await add_real_entry(hass)
            assert entry.state is ConfigEntryState.SETUP_RETRY
            assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_groups_that_cannot_be_read_are_skipped() -> None:
    """A group without its list counts as skipped, and the next one is still read."""
    good = FakeToshiba.unit()
    units, skipped = parse_units([{"no": "list"}, None, {"ACList": [good]}])

    assert [unit.name for unit in units] == [good["Name"]]
    assert skipped == 2


def test_diagnostics_work_without_a_loaded_entry(run_in_hass) -> None:
    """They are most needed when setup fails."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.statuses["mapping"] = 500
            entry = await add_real_entry(hass)
            result = await async_get_config_entry_diagnostics(hass, entry)
            assert result["error"] == "Device manager not found"
            assert fake.token not in str(result)
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_diagnostics_report_a_failure_to_read_the_units(run_in_hass) -> None:
    """A problem while collecting must not cost the download."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba():
            entry = await add_real_entry(hass)
            with patch.object(
                entry.runtime_data,
                "get_devices",
                AsyncMock(side_effect=RuntimeError("surprise")),
            ):
                result = await async_get_config_entry_diagnostics(hass, entry)
            assert "surprise" in result["error"]
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


# Periodic requests


def test_a_rejected_token_in_a_refresh_asks_for_the_password_and_stops(
    run_in_hass,
) -> None:
    """The refreshes end, and nothing is sent with that token again."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            with (
                patch(f"{MANAGER}.REFRESH_S", 0.02),
                patch(f"{MANAGER}.REFRESH_MIN_S", 0.01),
            ):
                entry = await stay_loaded_with_quick_refreshes(hass)
                fake.statuses["mapping"] = 401
                await wait_until(
                    lambda: bool(
                        hass.config_entries.flow.async_progress_by_handler(DOMAIN)
                    )
                )
                sent = fake.total
                await asyncio.sleep(0.1)
                assert fake.total == sent
                await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_a_block_in_a_refresh_stops_the_refreshes(run_in_hass, caplog) -> None:
    """A 403 is one request, whatever the timer says."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            with (
                patch(f"{MANAGER}.REFRESH_S", 0.02),
                patch(f"{MANAGER}.REFRESH_MIN_S", 0.01),
            ):
                entry = await stay_loaded_with_quick_refreshes(hass)
                fake.statuses["mapping"] = 403
                seen = fake.requests["mapping"]
                await wait_until(lambda: fake.requests["mapping"] > seen)
                await wait_until(lambda: "asked to slow down" in caplog.text)
                sent = fake.requests["mapping"]
                await asyncio.sleep(0.1)
                assert fake.requests["mapping"] == sent
                assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
                await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_a_server_error_in_a_refresh_waits_for_the_next_turn(run_in_hass) -> None:
    """The refreshes go on, and the state arrives once the server is back."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            with (
                patch(f"{MANAGER}.REFRESH_S", 0.02),
                patch(f"{MANAGER}.REFRESH_MIN_S", 0.01),
            ):
                entry = await stay_loaded_with_quick_refreshes(hass)
                fake.statuses["mapping"] = 500
                seen = fake.requests["mapping"]
                await wait_until(lambda: fake.requests["mapping"] > seen + 1)

                del fake.statuses["mapping"]
                fake.units[0]["ACStateData"] = RUNNING_STATE
                await wait_until(lambda: hass.states.get(CLIMATE).state == "cool")
                await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_a_reconnection_catches_up_on_what_it_missed(run_in_hass) -> None:
    """Pushes do not arrive while the socket is down."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            with (
                patch(f"{REALTIME}.RETRY_MIN_S", 0.02),
                patch(f"{MANAGER}.REFRESH_MIN_S", 0.01),
            ):
                entry = await add_real_entry(hass)
                seen = fake.requests["mapping"]
                fake.units[0]["ACStateData"] = RUNNING_STATE
                await fake.sockets[-1].close()
                await wait_until(lambda: fake.requests["mapping"] == seen + 1)
                await wait_until(lambda: hass.states.get(CLIMATE).state == "cool")
                await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


@pytest.mark.parametrize(
    "answer",
    [
        json.dumps(
            {
                "IsSuccess": True,
                "ResObj": [
                    {"ACDeviceUniqueId": "x", "EnergyConsumption": [{"Energy": "bad"}]},
                    {"EnergyConsumption": [{"Energy": "1"}]},
                    {"ACDeviceUniqueId": "y", "EnergyConsumption": []},
                ],
            }
        ),
        json.dumps({"IsSuccess": True, "ResObj": None}),
    ],
)
def test_bad_energy_answers_leave_the_sensor_unknown(run_in_hass, answer) -> None:
    """They are skipped, and the entry stays loaded."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.units = [FakeToshiba.unit(model="3", merit="2c03")]
            fake.raw_answers["energy"] = answer
            entry = await add_real_entry(hass)
            # Without waiting for the answer, this would pass without looking at it
            await wait_until(lambda: fake.requests["energy"] == 1)
            await asyncio.sleep(0.05)
            await hass.async_block_till_done()
            assert entry.state is ConfigEntryState.LOADED
            energy = hass.states.get("sensor.living_room_energy_consumption")
            assert energy.state == "unknown"
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


# Realtime channel


def test_a_rejected_token_on_reconnecting_asks_for_the_password(run_in_hass) -> None:
    """The channel ends, since only a new login can help."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            with patch(f"{REALTIME}.RETRY_MIN_S", 0.02):
                entry = await add_real_entry(hass)
                fake.statuses["channel"] = 401
                await fake.sockets[-1].close()
                await wait_until(
                    lambda: bool(
                        hass.config_entries.flow.async_progress_by_handler(DOMAIN)
                    )
                )
                assert fake.requests["channel"] == 2
                await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_a_block_on_reconnecting_waits_as_long_as_it_says(run_in_hass) -> None:
    """Asking for an address again would be another request."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            with patch(f"{REALTIME}.RETRY_MIN_S", 0.02):
                entry = await add_real_entry(hass)
                unit = (await entry.runtime_data.get_devices())[0]
                fake.statuses["channel"] = 429
                fake.headers["Retry-After"] = "7200"
                await fake.sockets[-1].close()
                await wait_until(lambda: not unit.connected)
                await wait_until(lambda: fake.requests["channel"] == 2)
                await asyncio.sleep(0.1)
                assert fake.requests["channel"] == 2
                guard = hass.data[guard_module._DATA_GUARD]
                assert guard.diagnostics()["blocked_for_s"] >= 7100
                await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_a_stable_connection_starts_the_waits_over(run_in_hass) -> None:
    """Only a connection that lasted says that the problem was a passing one."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            sleeps: list[float] = []
            real_uniform = guard_module._RANDOM.uniform

            def record(low: float, high: float) -> float:
                sleeps.append(high)
                return real_uniform(low, high) / 100

            with (
                patch(f"{REALTIME}.STABLE_S", 0),
                patch.object(guard_module._RANDOM, "uniform", record),
            ):
                entry = await add_real_entry(hass)
                unit = (await entry.runtime_data.get_devices())[0]
                await fake.sockets[-1].close()
                await wait_until(lambda: not unit.connected)
                await wait_until(lambda: unit.connected, 5)
                await fake.sockets[-1].close()
                await wait_until(lambda: not unit.connected)
                await wait_until(lambda: unit.connected, 5)
                await hass.config_entries.async_unload(entry.entry_id)

            waits = [high for high in sleeps if high >= 5]
            assert waits == [10, 10, 10][: len(waits)]
            assert len(waits) >= 2

    run_in_hass(scenario)


# Commands


@pytest.mark.parametrize(
    ("status", "answer"),
    [(500, None), (403, None), (None, failed_answer("Other")), (None, "not json")],
)
def test_a_command_that_fails_says_so_once(run_in_hass, status, answer) -> None:
    """A command is never repeated, and the entity keeps its value."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_real_entry(hass)
            if status:
                fake.statuses["command"] = status
            else:
                fake.raw_answers["command"] = answer

            with pytest.raises(HomeAssistantError):
                await set_temperature(hass)

            assert fake.requests["command"] == 1
            assert hass.states.get(CLIMATE).attributes["temperature"] == 17
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_a_command_while_blocked_makes_no_request(run_in_hass) -> None:
    """A block holds for commands too, with a clear error."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_real_entry(hass)
            await hass.data[guard_module._DATA_GUARD].async_record_block()

            with pytest.raises(HomeAssistantError):
                await set_temperature(hass)

            assert fake.requests["command"] == 0
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


# Climate entity on the real unit


def test_presets_and_vanes_reach_the_wire(run_in_hass) -> None:
    """The names of the entity turn into the bytes of the unit."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.units[0]["ACStateData"] = RUNNING_STATE
            entry = await add_real_entry(hass)

            await hass.services.async_call(
                "climate",
                "set_preset_mode",
                {"entity_id": CLIMATE, "preset_mode": "Power 50"},
                blocking=True,
            )
            assert fake.commands[-1]["payload"]["data"][10:12] == "32"
            assert hass.states.get(CLIMATE).attributes["preset_mode"] == "Power 50"

            await hass.services.async_call(
                "climate",
                "set_swing_mode",
                {"entity_id": CLIMATE, "swing_mode": "Swing Vertical"},
                blocking=True,
            )
            assert fake.commands[-1]["payload"]["data"][8:10] == "41"
            assert hass.states.get(CLIMATE).attributes["swing_mode"] == "Swing Vertical"
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_horizontal_swing_appears_once_a_unit_reports_independent_vanes(
    run_in_hass,
) -> None:
    """It cannot be known at the start, so the entity gains the feature later."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            # This merit string declares horizontal swing, which commands depend on
            fake.units[0].update(ACStateData=RUNNING_STATE, MeritFeature="6c00")
            entry = await add_real_entry(hass)
            unit = (await entry.runtime_data.get_devices())[0]
            assert "swing_horizontal_mode" not in hass.states.get(CLIMATE).attributes

            await fake.publish("CMD_FCU_FROM_AC", {"data": "ffffffffb0" + "ff" * 17})
            await wait_until(lambda: unit.supports_independent_swing)
            await hass.async_block_till_done()

            await hass.services.async_call(
                "climate",
                "set_swing_horizontal_mode",
                {"entity_id": CLIMATE, "swing_horizontal_mode": "Swing"},
                blocking=True,
            )
            assert fake.commands[-1]["payload"]["data"][8:10] == "b0"
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


async def test_names_that_do_not_exist_send_nothing() -> None:
    """A name from an old automation is ignored rather than guessed at."""
    from custom_components.toshiba_ac_link.climate import ToshibaClimate

    from .conftest import make_device

    device = make_device()
    climate = ToshibaClimate(device)

    await climate.async_set_preset_mode("Nothing")
    await climate.async_set_swing_mode("Nothing")
    await climate.async_set_fan_mode("Nothing")
    await climate.async_set_swing_horizontal_mode("Nothing")

    for setter in (
        device.set_ac_power_selection,
        device.set_ac_swing_mode,
        device.set_ac_fan_mode,
        device.set_ac_horizontal_swing_mode,
    ):
        setter.assert_not_awaited()
