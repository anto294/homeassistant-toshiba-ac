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
