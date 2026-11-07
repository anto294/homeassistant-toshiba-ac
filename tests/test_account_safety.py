"""The first goal of this integration: no restart loop can harm a Toshiba account.

Each test counts the requests that reach a local stand-in for Toshiba. A restart
drops the guard from memory, as a real one does, so only the disk can help.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.toshiba_ac_link.const import DOMAIN
from custom_components.toshiba_ac_link.toshiba import guard as guard_module
from custom_components.toshiba_ac_link.toshiba.guard import (
    BLOCK_BASE_S,
    HOURLY_REQUEST_BUDGET,
    MAX_FAILED_LOGINS,
)

from .conftest import ACCOUNT
from .fake_toshiba import (
    RUNNING_STATE,
    FakeToshiba,
    add_real_entry as add_entry,
    make_jwt,
    run_fake_toshiba,
    wait_until,
)

MANAGER = "custom_components.toshiba_ac_link.toshiba.manager"


async def restart(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry with an empty memory, as after a restart."""
    if entry.state is not ConfigEntryState.NOT_LOADED:
        await hass.config_entries.async_unload(entry.entry_id)
    hass.data.pop(guard_module._DATA_GUARD, None)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def unload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Close the socket, so the fake server can stop."""
    await hass.config_entries.async_unload(entry.entry_id)


def test_the_password_is_not_stored(run_in_hass) -> None:
    """What is not stored cannot be retried, and cannot leak."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba():
            entry = await add_entry(hass)
            assert "password" not in entry.data
            assert ACCOUNT["password"] not in str(entry.as_dict())
            await unload(hass, entry)

    run_in_hass(scenario)


def test_fifty_restarts_make_no_login_and_reuse_the_units(run_in_hass) -> None:
    """A restart only asks for the socket address, which works for one connection.

    The catch-up after a saved list waits 5 minutes, so a loop cancels it every time.
    """

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_entry(hass)
            assert entry.state is ConfigEntryState.LOADED
            assert fake.requests == {"login": 1, "mapping": 1, "channel": 1}

            for _ in range(50):
                await restart(hass, entry)
                assert entry.state is ConfigEntryState.LOADED

            assert fake.requests == {"login": 1, "mapping": 1, "channel": 51}
            assert len(fake.sockets) == 51
            await unload(hass, entry)

    run_in_hass(scenario)


def test_a_start_from_the_saved_list_catches_up_later(run_in_hass) -> None:
    """The saved list misses commands and pushes, so one request corrects it."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            with patch(f"{MANAGER}.REFRESH_MIN_S", 0.05):
                entry = await add_entry(hass)
                # A start that just asked for the list has nothing to catch up on
                await asyncio.sleep(0.2)
                assert fake.requests["mapping"] == 1

                fake.units[0]["ACStateData"] = RUNNING_STATE
                await restart(hass, entry)
                assert fake.requests["mapping"] == 1
                assert hass.states.get("climate.living_room").state == "off"

                await wait_until(lambda: fake.requests["mapping"] == 2)
                await wait_until(
                    lambda: hass.states.get("climate.living_room").state == "cool"
                )
                await asyncio.sleep(0.2)
                assert fake.requests["mapping"] == 2
                await unload(hass, entry)

    run_in_hass(scenario)


def test_a_restart_remembers_commands_and_pushes(run_in_hass) -> None:
    """The saved list is corrected on disk, with no request at all."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_entry(hass)
            await hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {"entity_id": "climate.living_room", "hvac_mode": "heat"},
                blocking=True,
            )
            sent = fake.total - fake.requests["channel"]

            await restart(hass, entry)

            assert hass.states.get("climate.living_room").state == "heat"
            assert fake.total - fake.requests["channel"] == sent
            await unload(hass, entry)

    run_in_hass(scenario)


def test_remembering_a_state_does_not_make_the_list_younger(run_in_hass) -> None:
    """Otherwise a busy unit would stop the list from ever being asked for again."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_entry(hass)
            guard = hass.data[guard_module._DATA_GUARD]
            saved = guard.cached("units", "consumer-1")["at"]

            await fake.publish(
                "CMD_FCU_FROM_AC",
                {"data": "30ffffffffffffffffffffffffffffffffffffffffff"},
            )
            await wait_until(
                lambda: hass.states.get("climate.living_room").state != "off"
            )

            cached = guard.cached("units", "consumer-1")
            assert cached["at"] == saved
            assert cached["groups"][0]["ACList"][0]["ACStateData"].startswith("30")
            await unload(hass, entry)

    run_in_hass(scenario)


def test_restarts_cannot_exceed_the_hourly_budget(run_in_hass) -> None:
    """Even when the list of units is out of date, the disk stops the requests."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_entry(hass)
            with patch(f"{MANAGER}.UNITS_CACHE_S", 0):
                for _ in range(HOURLY_REQUEST_BUDGET):
                    await restart(hass, entry)
            assert fake.total <= HOURLY_REQUEST_BUDGET
            assert entry.state is ConfigEntryState.SETUP_RETRY
            await unload(hass, entry)

    run_in_hass(scenario)


def test_rejected_token_is_sent_once_and_asks_for_the_password(run_in_hass) -> None:
    """After a 401, restarts send nothing until the user logs in again."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_entry(hass)
            with patch(f"{MANAGER}.UNITS_CACHE_S", 0):
                fake.statuses["mapping"] = 401
                await restart(hass, entry)
                assert entry.state is ConfigEntryState.SETUP_ERROR
                sent = fake.total

                for _ in range(20):
                    await restart(hass, entry)
                    assert entry.state is ConfigEntryState.SETUP_ERROR

                assert fake.total == sent
                assert fake.requests["login"] == 1

                flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
                assert flows[0]["context"]["source"] == "reauth"

                # A new login and a new token end it
                del fake.statuses["mapping"]
                fake.token = make_jwt(1000)
                result = await hass.config_entries.flow.async_configure(
                    flows[0]["flow_id"], {"password": "secret"}
                )
                await hass.async_block_till_done()

            assert result["reason"] == "reauth_successful"
            assert entry.state is ConfigEntryState.LOADED
            assert entry.data["access_token"] == fake.token
            assert fake.requests["login"] == 2
            await unload(hass, entry)

    run_in_hass(scenario)


def test_expired_token_makes_no_request(run_in_hass) -> None:
    """A token that says it expired is not worth a request."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.token = make_jwt(-10)
            entry = await add_entry(hass)
            assert entry.state is ConfigEntryState.SETUP_ERROR
            assert fake.requests == {"login": 1}

    run_in_hass(scenario)


def test_waf_answer_stops_all_requests_across_restarts(run_in_hass) -> None:
    """A 403 is one request, and asks for no password."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_entry(hass)
            with patch(f"{MANAGER}.UNITS_CACHE_S", 0):
                fake.statuses["mapping"] = 403
                await restart(hass, entry)
                assert entry.state is ConfigEntryState.SETUP_RETRY
                assert "reauth" not in str(
                    hass.config_entries.flow.async_progress_by_handler(DOMAIN)
                )
                sent = fake.total

                for _ in range(20):
                    await restart(hass, entry)

                assert fake.total == sent

                # Still blocked just before the wait ends, free after it
                blocked_until = hass.data[guard_module._DATA_GUARD]._data[
                    "blocked_until"
                ]
                assert blocked_until - time.time() >= BLOCK_BASE_S - 5
                del fake.statuses["mapping"]
                with patch("time.time", return_value=blocked_until + 1):
                    await restart(hass, entry)
                    assert entry.state is ConfigEntryState.LOADED
                    await unload(hass, entry)

    run_in_hass(scenario)


def test_repeated_blocks_wait_longer_and_honour_retry_after(run_in_hass) -> None:
    """The wait doubles, and never undercuts what Toshiba asks for."""

    async def scenario(hass: HomeAssistant) -> None:
        guard = await guard_module.async_get_guard(hass)
        first = await guard.async_record_block()
        second = await guard.async_record_block()
        assert BLOCK_BASE_S <= first <= BLOCK_BASE_S * 1.2
        assert BLOCK_BASE_S * 2 <= second <= BLOCK_BASE_S * 2.4

        await guard.async_record_success()
        assert await guard.async_record_block(40000) >= 40000
        assert guard.diagnostics()["block_count"] == 1

    run_in_hass(scenario)


def test_failed_logins_stop_before_a_lockout(run_in_hass) -> None:
    """The form refuses to try again, even after a restart."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}
            )
            wrong = {"username": ACCOUNT["username"], "password": "wrong"}

            for _ in range(MAX_FAILED_LOGINS):
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], wrong
                )
                assert result["errors"] == {"base": "invalid_auth"}

            hass.data.pop(guard_module._DATA_GUARD)

            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], wrong
            )
            assert result["errors"] == {"base": "too_many_attempts"}
            assert fake.requests["login"] == MAX_FAILED_LOGINS

            # Another account has its own count
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {**ACCOUNT, "username": "other@example.com"}
            )
            assert result["type"] == "create_entry"
            await hass.async_block_till_done()
            await unload(hass, result["result"])

    run_in_hass(scenario)


def test_blocked_login_is_not_sent(run_in_hass) -> None:
    """A 429 on the form blocks the next attempt, and is not a wrong password."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.statuses["login"] = 429
            fake.headers["Retry-After"] = "7200"
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}
            )
            for _ in range(3):
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], dict(ACCOUNT)
                )
                assert result["errors"] == {"base": "too_many_attempts"}
            assert fake.requests["login"] == 1

    run_in_hass(scenario)


def test_energy_is_not_read_again_by_a_restart(run_in_hass) -> None:
    """Units with energy reports cost one request per hour, restarted or not."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.units = [FakeToshiba.unit(model="3", merit="2c03")]
            entry = await add_entry(hass)
            # It is read by a task in the background, which nothing here waits for
            await wait_until(lambda: fake.requests["energy"] == 1)

            for _ in range(10):
                await restart(hass, entry)

            devices = await entry.runtime_data.get_devices()
            await wait_until(lambda: devices[0].ac_energy_consumption is not None)
            assert devices[0].ac_energy_consumption.energy_wh == 2000
            await asyncio.sleep(0.1)
            assert fake.requests["energy"] == 1
            await unload(hass, entry)

    run_in_hass(scenario)


@pytest.mark.parametrize("status", [400, 404, 422])
def test_other_client_errors_count_as_failed_logins(run_in_hass, status) -> None:
    """Toshiba may count them toward a lockout, so the form must stop as well."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.statuses["login"] = status
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}
            )
            for _ in range(MAX_FAILED_LOGINS):
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], dict(ACCOUNT)
                )
                assert result["errors"] == {"base": "cannot_connect"}

            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], dict(ACCOUNT)
            )
            assert result["errors"] == {"base": "too_many_attempts"}
            assert fake.requests["login"] == MAX_FAILED_LOGINS

    run_in_hass(scenario)


def test_server_errors_do_not_count_as_failed_logins(run_in_hass) -> None:
    """A 500 says nothing about the password, and the account did not fail anything."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.statuses["login"] = 500
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}
            )
            for _ in range(MAX_FAILED_LOGINS + 1):
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], dict(ACCOUNT)
                )
                assert result["errors"] == {"base": "cannot_connect"}
            assert fake.requests["login"] == MAX_FAILED_LOGINS + 1

    run_in_hass(scenario)


async def set_temperature(hass: HomeAssistant, temperature: int = 24) -> None:
    """Send a command through the climate entity."""
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.living_room", "temperature": temperature},
        blocking=True,
    )


def test_a_command_with_no_echo_corrects_the_state_and_is_not_repeated(
    run_in_hass, caplog
) -> None:
    """Toshiba can accept a command that the unit never applies."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            with patch(f"{MANAGER}.ECHO_WAIT_S", 0.02):
                entry = await add_entry(hass)
                seen = fake.requests["mapping"]

                await set_temperature(hass)
                assert (
                    hass.states.get("climate.living_room").attributes["temperature"]
                    == 24
                )

                await wait_until(lambda: fake.requests["mapping"] == seen + 1)
                await wait_until(
                    lambda: hass.states.get("climate.living_room").attributes[
                        "temperature"
                    ]
                    == 17
                )
                await asyncio.sleep(0.1)
                assert fake.requests["command"] == 1
                assert fake.requests["mapping"] == seen + 1
                assert caplog.text.count("did not confirm a command") == 1
                await unload(hass, entry)

    run_in_hass(scenario)


def test_a_command_with_an_echo_makes_no_more_requests(run_in_hass, caplog) -> None:
    """The usual case costs nothing."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.echo_commands = True
            with patch(f"{MANAGER}.ECHO_WAIT_S", 0.05):
                entry = await add_entry(hass)
                seen = fake.requests["mapping"]

                await set_temperature(hass)
                await asyncio.sleep(0.2)

                assert fake.requests["mapping"] == seen
                assert "did not confirm a command" not in caplog.text
                await unload(hass, entry)

    run_in_hass(scenario)


def test_many_commands_with_no_echo_ask_for_the_list_together(run_in_hass) -> None:
    """A unit that went away must not turn each command into two requests."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            with patch(f"{MANAGER}.ECHO_WAIT_S", 0.05):
                entry = await add_entry(hass)
                seen = fake.requests["mapping"]

                for temperature in (20, 21, 22, 23, 24):
                    await set_temperature(hass, temperature)
                await wait_until(lambda: fake.requests["mapping"] > seen)
                await asyncio.sleep(0.2)

                assert fake.requests["command"] == 5
                assert fake.requests["mapping"] == seen + 1
                await unload(hass, entry)

    run_in_hass(scenario)


def test_a_command_that_failed_awaits_no_echo(run_in_hass, caplog) -> None:
    """The user already got the error, and nothing is there to correct."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            with patch(f"{MANAGER}.ECHO_WAIT_S", 0.02):
                entry = await add_entry(hass)
                seen = fake.requests["mapping"]
                fake.statuses["command"] = 500

                with pytest.raises(HomeAssistantError):
                    await set_temperature(hass)
                await asyncio.sleep(0.1)

                assert fake.requests["mapping"] == seen
                assert "did not confirm a command" not in caplog.text
                await unload(hass, entry)

    run_in_hass(scenario)


def test_stopping_cancels_the_wait_for_an_echo(run_in_hass) -> None:
    """Nothing is sent after the entry is gone."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            with patch(f"{MANAGER}.ECHO_WAIT_S", 0.05):
                entry = await add_entry(hass)
                seen = fake.requests["mapping"]

                await set_temperature(hass)
                await unload(hass, entry)
                await asyncio.sleep(0.2)

                assert fake.requests["mapping"] == seen

    run_in_hass(scenario)


def test_no_echo_while_the_socket_is_down_says_nothing_about_the_unit(
    run_in_hass, caplog
) -> None:
    """It could not have arrived, and the reconnection catches up anyway."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            with patch(f"{MANAGER}.ECHO_WAIT_S", 0.1):
                entry = await add_entry(hass)
                unit = (await entry.runtime_data.get_devices())[0]
                await set_temperature(hass)
                fake.refuse_sockets = True
                await fake.sockets[-1].close()
                await wait_until(lambda: not unit.connected)
                await asyncio.sleep(0.2)

                assert "did not confirm a command" not in caplog.text
                await unload(hass, entry)

    run_in_hass(scenario)


async def reconfigure(
    hass: HomeAssistant, entry: ConfigEntry, flow_id=None, **form
) -> dict:
    """Send the reconfiguration form once, and return what it answers."""
    if flow_id is None:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
        )
        flow_id = result["flow_id"]
    result = await hass.config_entries.flow.async_configure(flow_id, form)
    await hass.async_block_till_done()
    return result


def test_reconfiguring_logs_in_once_and_a_restart_does_not(run_in_hass) -> None:
    """Opening the form sends nothing, and the new token needs no other login."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_entry(hass)
            assert fake.requests["login"] == 1
            fake.token = make_jwt(100 * 86400)

            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
            )
            assert fake.requests["login"] == 1

            result = await reconfigure(
                hass,
                entry,
                result["flow_id"],
                username="new@example.com",
                password="secret",
            )
            assert result["reason"] == "reconfigure_successful"
            assert fake.requests["login"] == 2
            assert entry.state is ConfigEntryState.LOADED
            assert entry.data["access_token"] == fake.token
            assert "secret" not in str(entry.data)

            for _ in range(3):
                await restart(hass, entry)
            assert fake.requests["login"] == 2
            await unload(hass, entry)

    run_in_hass(scenario)


def test_failed_reconfiguring_stops_at_the_login_limit(run_in_hass) -> None:
    """Its failures count with those of the other forms, and survive a restart."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_entry(hass)
            before = dict(entry.data)
            logins = fake.requests["login"]
            wrong = {"username": ACCOUNT["username"], "password": "wrong"}

            flow_id = None
            for _ in range(MAX_FAILED_LOGINS):
                result = await reconfigure(hass, entry, flow_id, **wrong)
                flow_id = result["flow_id"]
                assert result["errors"] == {"base": "invalid_auth"}
            assert fake.requests["login"] == logins + MAX_FAILED_LOGINS

            await unload(hass, entry)
            hass.data.pop(guard_module._DATA_GUARD)
            result = await reconfigure(hass, entry, flow_id, **wrong)

            assert result["errors"] == {"base": "too_many_attempts"}
            assert fake.requests["login"] == logins + MAX_FAILED_LOGINS
            assert dict(entry.data) == before

    run_in_hass(scenario)


def test_reconfiguring_with_another_account_makes_only_its_login(run_in_hass) -> None:
    """The entry goes on with its own token, with no reload and no other request."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_entry(hass)
            before = dict(entry.data)
            total = fake.total
            fake.consumer_id = "consumer-2"
            fake.token = make_jwt(100 * 86400)

            result = await reconfigure(
                hass, entry, username="other@example.com", password="secret"
            )

            assert result["reason"] == "wrong_account"
            assert fake.total == total + 1
            assert dict(entry.data) == before
            assert entry.title == ACCOUNT["username"]
            await unload(hass, entry)

    run_in_hass(scenario)


def test_reconfiguring_after_a_rejected_token_ends_the_request_for_a_password(
    run_in_hass,
) -> None:
    """The entry works again, so nothing should keep asking for the password."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_entry(hass)
            guard = hass.data[guard_module._DATA_GUARD]
            await guard.async_reject_token(entry.data["access_token"])
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            assert entry.state is ConfigEntryState.SETUP_ERROR
            assert hass.config_entries.flow.async_progress_by_handler(DOMAIN)

            fake.token = make_jwt(100 * 86400)
            result = await reconfigure(hass, entry, **ACCOUNT)

            assert result["reason"] == "reconfigure_successful"
            assert entry.state is ConfigEntryState.LOADED
            assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
            await unload(hass, entry)

    run_in_hass(scenario)
