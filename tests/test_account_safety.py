"""The first goal of this integration: no restart loop can harm a Toshiba account.

Each test counts the requests that reach a local stand-in for Toshiba. A restart
drops the guard from memory, as a real one does, so only the disk can help.
"""

from __future__ import annotations

import time
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.toshiba_ac_link.const import DOMAIN
from custom_components.toshiba_ac_link.toshiba import guard as guard_module
from custom_components.toshiba_ac_link.toshiba.guard import (
    BLOCK_BASE_S,
    HOURLY_REQUEST_BUDGET,
    MAX_FAILED_LOGINS,
)

from .conftest import ACCOUNT
from .fake_toshiba import (
    FakeToshiba,
    add_real_entry as add_entry,
    make_jwt,
    run_fake_toshiba,
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
    """A restart only asks for the socket address, which works for one connection."""

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
            await hass.async_block_till_done()
            assert fake.requests["energy"] == 1

            for _ in range(10):
                await restart(hass, entry)

            assert fake.requests["energy"] == 1
            devices = await entry.runtime_data.get_devices()
            assert devices[0].ac_energy_consumption.energy_wh == 2000
            await unload(hass, entry)

    run_in_hass(scenario)
