"""The Toshiba Link integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .toshiba.api import LoginResult
from .toshiba.errors import ToshibaAuthError, ToshibaBlockedError, ToshibaError
from .toshiba.guard import async_get_guard
from .toshiba.manager import ToshibaAcDeviceManager

type ToshibaConfigEntry = ConfigEntry[ToshibaAcDeviceManager]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = ["binary_sensor", "climate", "select", "sensor", "switch"]

_LOGGER = logging.getLogger(__name__)


# ---------------------------
#   async_setup
# ---------------------------
async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Register the actions, which exist even when no entry is loaded."""

    async def handle_reconnect(_call: ServiceCall) -> None:
        """Reload all entries, which makes new connections, and report any that failed."""
        _LOGGER.info("Reconnect action called, reloading all entries")
        entries = hass.config_entries.async_entries(
            DOMAIN, include_disabled=False, include_ignore=False
        )
        results = [
            await hass.config_entries.async_reload(entry.entry_id) for entry in entries
        ]
        if failed := results.count(False):
            raise HomeAssistantError(
                f"{failed} of {len(entries)} Toshiba entries did not load. "
                "The integration page shows what they need, and most retry by themselves"
            )

    hass.services.async_register(DOMAIN, "reconnect", handle_reconnect)
    return True


# ---------------------------
#   _registered_unit_ids
# ---------------------------
def _registered_unit_ids(hass: HomeAssistant, entry: ToshibaConfigEntry) -> set[str]:
    """Return the units that the device registry holds for this entry."""
    return {
        unit_id
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        for domain, unit_id in device.identifiers
        if domain == DOMAIN
    }


# ---------------------------
#   _async_units_changed
# ---------------------------
def _async_units_changed(
    hass: HomeAssistant, entry: ToshibaConfigEntry, removed: set[str]
) -> None:
    """Remove the devices of units that left, and reload to match the account."""
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if any(
            domain == DOMAIN and unit_id in removed
            for domain, unit_id in device.identifiers
        ):
            _LOGGER.info("Removing a unit that left the Toshiba account")
            registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )
    hass.config_entries.async_schedule_reload(entry.entry_id)


# ---------------------------
#   async_setup_entry
# ---------------------------
async def async_setup_entry(hass: HomeAssistant, entry: ToshibaConfigEntry) -> bool:
    """Set up Toshiba Link from a config entry.

    Nothing here logs in. Only the forms do, when the user sends them, so no
    restart can repeat a login.
    """
    manager = ToshibaAcDeviceManager(
        hass,
        async_get_clientsession(hass),
        await async_get_guard(hass),
        entry.data["device_id"],
        LoginResult(
            entry.data["access_token"],
            entry.data["access_token_type"],
            entry.data["consumer_id"],
        ),
        lambda: entry.async_start_reauth(hass),
        lambda removed: _async_units_changed(hass, entry, removed),
        _registered_unit_ids(hass, entry),
    )

    try:
        await manager.connect()
    except ToshibaAuthError as ex:
        raise ConfigEntryAuthFailed(str(ex)) from ex
    except ToshibaBlockedError as ex:
        # Without retry_after, Home Assistant would try again within 10 minutes
        raise ConfigEntryNotReady(str(ex), retry_after=ex.retry_after) from ex
    except ToshibaError as ex:
        raise ConfigEntryNotReady(str(ex)) from ex

    entry.runtime_data = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


# ---------------------------
#   async_unload_entry
# ---------------------------
async def async_unload_entry(hass: HomeAssistant, entry: ToshibaConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.shutdown()
    return unload_ok


# ---------------------------
#   async_remove_config_entry_device
# ---------------------------
async def async_remove_config_entry_device(
    _hass: HomeAssistant, entry: ToshibaConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow deleting a device only if it is no longer a unit of the account."""
    if entry.state is not ConfigEntryState.LOADED:
        return False
    units = {device.ac_unique_id for device in entry.runtime_data.devices}
    return not any(
        domain == DOMAIN and unit_id in units
        for domain, unit_id in device_entry.identifiers
    )
