"""Tests of units that join or leave the account while the integration runs.

Each refresh is called directly, so these count exactly one answer per call.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.toshiba_ac_link.const import DOMAIN
from custom_components.toshiba_ac_link.toshiba import guard as guard_module

from .fake_toshiba import (
    OTHER_UNIT_ID,
    UNIT_ID,
    FakeToshiba,
    add_real_entry,
    run_fake_toshiba,
)

BEDROOM = "climate.bedroom"


def second_unit() -> dict:
    """Return another unit of the account."""
    return {
        **FakeToshiba.unit(),
        "Id": "ac-2",
        "DeviceUniqueId": OTHER_UNIT_ID,
        "Name": "Bedroom",
    }


async def refresh(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Make the periodic request now, and let what it starts finish."""
    await entry.runtime_data._async_refresh()
    await hass.async_block_till_done()


def has_device(hass: HomeAssistant, entry: ConfigEntry, unit_id: str) -> bool:
    """Return whether the device registry holds this unit for the entry."""
    return any(
        (DOMAIN, unit_id) in device.identifiers
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
    )


def test_a_new_unit_appears_with_one_more_request(run_in_hass) -> None:
    """The reload reuses the list it just got, so only the socket address is new."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_real_entry(hass)
            fake.units.append(second_unit())

            await refresh(hass, entry)

            assert entry.state is ConfigEntryState.LOADED
            assert hass.states.get(BEDROOM) is not None
            assert fake.requests == {"login": 1, "mapping": 2, "channel": 2}

            await refresh(hass, entry)
            assert fake.requests == {"login": 1, "mapping": 3, "channel": 2}
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_a_new_unit_that_cannot_be_read_causes_no_reload(run_in_hass) -> None:
    """Reloading could not help, and each reload costs a request."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_real_entry(hass)
            fake.units.append({**second_unit(), "ACStateData": ""})

            for _ in range(3):
                await refresh(hass, entry)

            assert fake.requests["channel"] == 1
            assert hass.states.get(BEDROOM) is None
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_a_unit_skipped_at_the_start_returns_when_it_can_be_read(run_in_hass) -> None:
    """It no longer has to wait for a restart."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.units.append({**second_unit(), "ACStateData": ""})
            entry = await add_real_entry(hass)
            assert hass.states.get(BEDROOM) is None

            fake.units[1] = second_unit()
            await refresh(hass, entry)

            assert hass.states.get(BEDROOM) is not None
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_a_unit_gone_from_two_answers_is_removed(run_in_hass) -> None:
    """One answer could be a passing mistake, and removing loses the user's settings."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.units.append(second_unit())
            entry = await add_real_entry(hass)
            registry = er.async_get(hass)
            assert registry.async_get(BEDROOM) is not None

            fake.units.pop()
            await refresh(hass, entry)
            assert has_device(hass, entry, OTHER_UNIT_ID)
            assert fake.requests["channel"] == 1

            await refresh(hass, entry)
            assert not has_device(hass, entry, OTHER_UNIT_ID)
            assert has_device(hass, entry, UNIT_ID)
            assert registry.async_get(BEDROOM) is None
            assert hass.states.get("climate.living_room") is not None
            assert entry.state is ConfigEntryState.LOADED
            assert fake.requests["channel"] == 2
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_a_unit_that_comes_back_between_answers_stays(run_in_hass) -> None:
    """The two answers have to be in a row."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.units.append(second_unit())
            entry = await add_real_entry(hass)

            fake.units.pop()
            await refresh(hass, entry)
            fake.units.append(second_unit())
            await refresh(hass, entry)
            fake.units.pop()
            await refresh(hass, entry)

            assert has_device(hass, entry, OTHER_UNIT_ID)
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_an_empty_list_removes_nothing(run_in_hass) -> None:
    """An account does not lose all its units in the same moment, but a server can."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            entry = await add_real_entry(hass)
            fake.units = []

            for _ in range(3):
                await refresh(hass, entry)

            assert has_device(hass, entry, UNIT_ID)
            assert fake.requests["channel"] == 1
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_a_list_with_an_unreadable_unit_removes_nothing(run_in_hass) -> None:
    """The unit that looks gone could be the one that cannot be read."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.units.append(second_unit())
            entry = await add_real_entry(hass)

            fake.units[1] = {"Id": "no other fields"}
            for _ in range(3):
                await refresh(hass, entry)

            assert has_device(hass, entry, OTHER_UNIT_ID)
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)


def test_a_unit_that_left_while_stopped_is_removed_too(run_in_hass) -> None:
    """The registry knows it, although this run never loaded it."""

    async def scenario(hass: HomeAssistant) -> None:
        async with run_fake_toshiba() as fake:
            fake.units.append(second_unit())
            entry = await add_real_entry(hass)
            await hass.config_entries.async_unload(entry.entry_id)

            fake.units.pop()
            guard = hass.data[guard_module._DATA_GUARD]
            guard._data["cache"]["units"]["consumer-1"]["at"] = 0
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            assert has_device(hass, entry, OTHER_UNIT_ID)

            await refresh(hass, entry)
            assert has_device(hass, entry, OTHER_UNIT_ID)
            await refresh(hass, entry)
            assert not has_device(hass, entry, OTHER_UNIT_ID)
            await hass.config_entries.async_unload(entry.entry_id)

    run_in_hass(scenario)
