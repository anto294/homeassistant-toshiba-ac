"""Tests for the realtime channel and for commands, against a local stand-in for Toshiba."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.toshiba_ac_link.const import DOMAIN
from custom_components.toshiba_ac_link.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.toshiba_ac_link.toshiba.api import token_expiry
from custom_components.toshiba_ac_link.toshiba.device import ToshibaAcDevice
from custom_components.toshiba_ac_link.toshiba.properties import (
    ToshibaAcMode,
    ToshibaAcStatus,
    ToshibaAcSwingMode,
)

from .conftest import ACCOUNT
from .fake_toshiba import UNIT_ID, make_jwt, run_fake_toshiba

REALTIME = "custom_components.toshiba_ac_link.toshiba.realtime"


async def add_entry(hass: HomeAssistant) -> ConfigEntry:
    """Add an account through the real form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(ACCOUNT)
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry", result
    return result["result"]


async def wait_until(condition, timeout: float = 3) -> None:
    """Wait for something that happens on a real socket."""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


async def the_unit(entry: ConfigEntry) -> ToshibaAcDevice:
    """Return the only unit."""
    return (await entry.runtime_data.get_devices())[0]


def test_frames_captured_from_a_real_account_update_the_entity(run_in_hass) -> None:
    """An accepted change in the app arrives as partial frames with ff fields."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_entry(hass)
            unit = await the_unit(entry)
            assert unit.ac_status is ToshibaAcStatus.OFF
            assert unit.ac_mode is ToshibaAcMode.DRY

            await fake.publish(
                "CMD_FCU_FROM_AC",
                {"data": "30421141ffff00ffffffffffffffffffffffffffffff"},
            )
            await wait_until(lambda: unit.ac_status is ToshibaAcStatus.ON)
            assert unit.ac_mode is ToshibaAcMode.COOL
            assert unit.ac_temperature == 17

            await fake.publish(
                "CMD_FCU_FROM_AC",
                {"data": "ff421241ffff00ffffffffffffffffffffffffffffff"},
            )
            await wait_until(lambda: unit.ac_temperature == 18)
            assert unit.ac_status is ToshibaAcStatus.ON

            await fake.publish(
                "CMD_FCU_FROM_AC",
                {"data": "ffffffff41ffffffffffffffffffffffffffffffffff"},
            )
            await wait_until(
                lambda: unit.ac_swing_mode is ToshibaAcSwingMode.SWING_VERTICAL
            )

            state = hass.states.get("climate.living_room")
            assert state is not None
            assert state.state == "cool"
            assert state.attributes["temperature"] == 18
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_a_heartbeat_updates_the_temperatures(run_in_hass) -> None:
    """Fields that the unit does not have are ff, and empty ones must not hurt."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_entry(hass)
            unit = await the_unit(entry)

            await fake.publish(
                "CMD_HEARTBEAT",
                {"iTemp": "15", "oTemp": "1d", "fcuTcTemp": "ff", "Power": ""},
            )
            await wait_until(lambda: unit.ac_indoor_temperature == 21)
            assert unit.ac_outdoor_temperature == 29
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_bad_frames_do_not_end_the_channel(run_in_hass) -> None:
    """What the cloud sends must never take the connection down."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_entry(hass)
            unit = await the_unit(entry)
            socket = fake.sockets[-1]

            await socket.send_str("not json")
            await socket.send_str("[1, 2]")
            await socket.send_json({"type": "message", "data": "text"})
            await socket.send_json(
                {"type": "message", "data": {"sourceId": "unknown", "payload": {}}}
            )
            await fake.publish("CMD_FCU_FROM_AC", {"data": "zz"})
            await fake.publish("CMD_FCU_FROM_AC", {"data": 5})
            await fake.publish(
                "CMD_FCU_FROM_AC",
                {"data": "30ffffffffffffffffffffffffffffffffffffffffff"},
            )

            await wait_until(lambda: unit.ac_status is ToshibaAcStatus.ON)
            assert len(fake.sockets) == 1
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_a_lost_socket_makes_entities_unavailable_and_reconnects(run_in_hass) -> None:
    """An address works once, so each reconnection asks for a new one."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            with patch(f"{REALTIME}.RETRY_MIN_S", 0.05):
                entry = await add_entry(hass)
                unit = await the_unit(entry)
                assert unit.connected
                state = hass.states.get("climate.living_room")
                assert state is not None and state.state != "unavailable"

                await fake.sockets[-1].close()
                await wait_until(lambda: not unit.connected)
                await hass.async_block_till_done()
                state = hass.states.get("climate.living_room")
                assert state is not None and state.state == "unavailable"

                await wait_until(lambda: unit.connected)
                await hass.async_block_till_done()
                state = hass.states.get("climate.living_room")
                assert state is not None and state.state != "unavailable"
                assert fake.requests["channel"] == 2
                await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_a_refused_socket_waits_longer_each_time(run_in_hass) -> None:
    """Each attempt costs one request, so the waits have to grow."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.refuse_sockets = True
            sleeps: list[float] = []
            real_sleep = asyncio.sleep

            async def record_sleep(delay: float) -> None:
                # This replaces asyncio.sleep for all, so keep only the long waits
                if delay >= 1:
                    sleeps.append(delay)
                await real_sleep(0)

            with (
                patch(f"{REALTIME}.asyncio.sleep", record_sleep),
                patch(
                    "custom_components.toshiba_ac_link.toshiba.manager"
                    ".FIRST_CONNECTION_WAIT_S",
                    0.2,
                ),
            ):
                entry = await add_entry(hass)
                await wait_until(lambda: len(sleeps) >= 8)
                await hass.config_entries.async_unload(entry.entry_id)

            # Full waits double from 10 seconds, half of each is the jitter floor
            assert sleeps[0] >= 5
            assert sleeps[6] >= 320
            assert max(sleeps[:8]) <= 900
            assert fake.requests["channel"] >= 2

    run_in_hass(scenario)


def test_a_command_uses_the_format_of_the_app(run_in_hass) -> None:
    """One request, with what the echo of a command shows. The state follows at once."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_entry(hass)
            unit = await the_unit(entry)

            await hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": "climate.living_room", "temperature": 24},
                blocking=True,
            )

            assert fake.requests["command"] == 1
            command = fake.commands[0]
            device_id = entry.data["device_id"]
            assert command["sourceId"] == device_id
            assert command["targetId"] == [UNIT_ID]
            assert command["cmd"] == "CMD_FCU_TO_AC"
            assert command["messageId"] == f"MB_{device_id[:15].upper()}-00000001"
            assert command["payload"]["data"] == "ffff18" + "ff" * 19
            assert unit.ac_temperature == 24
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


@pytest.mark.parametrize(
    "answer",
    [
        {"Status": "Partial", "FailedTargetIds": [UNIT_ID]},
        {"Status": "Partial", "UnauthorizedTargetIds": [UNIT_ID]},
    ],
)
def test_an_undelivered_command_is_an_error_and_is_not_repeated(
    run_in_hass, answer
) -> None:
    """Repeating a command costs a request, and could undo a later one."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_entry(hass)
            unit = await the_unit(entry)
            fake.command_answer = answer

            with pytest.raises(HomeAssistantError):
                await hass.services.async_call(
                    "climate",
                    "set_temperature",
                    {"entity_id": "climate.living_room", "temperature": 24},
                    blocking=True,
                )

            assert fake.requests["command"] == 1
            assert unit.ac_temperature == 17
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_a_rejected_token_during_a_command_asks_for_the_password(run_in_hass) -> None:
    """The form appears, and the token is never sent again."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_entry(hass)
            fake.statuses["command"] = 401

            for _ in range(3):
                with pytest.raises(HomeAssistantError):
                    await hass.services.async_call(
                        "climate",
                        "set_temperature",
                        {"entity_id": "climate.living_room", "temperature": 24},
                        blocking=True,
                    )
            await hass.async_block_till_done()

            assert fake.requests["command"] == 1
            assert hass.config_entries.flow.async_progress_by_handler(DOMAIN)
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_diagnostics_hold_no_token_or_address(run_in_hass) -> None:
    """A shared diagnostics file holds no account data."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_entry(hass)
            result = await async_get_config_entry_diagnostics(hass, entry)
            text = str(result)
            for secret in (
                fake.token,
                "secret-url",
                "consumer-1",
                ACCOUNT["username"],
                entry.data["device_id"],
                UNIT_ID,
            ):
                assert secret not in text
            assert result["client"]["connected"] is True
            assert result["client"]["requests_this_hour"] == 3
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_token_expiry_reads_the_jwt() -> None:
    """A token that says nothing cannot be judged, and is left to Toshiba."""
    assert token_expiry(make_jwt(100)) is not None
    assert token_expiry("opaque") is None
    assert token_expiry("a.b.c") is None
