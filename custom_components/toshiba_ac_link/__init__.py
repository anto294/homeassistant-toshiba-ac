"""The Toshiba Link integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
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
        """Reload all entries, which makes new connections."""
        _LOGGER.info("Reconnect action called, reloading all entries")
        for entry in hass.config_entries.async_entries(DOMAIN):
            await hass.config_entries.async_reload(entry.entry_id)

    hass.services.async_register(DOMAIN, "reconnect", handle_reconnect)
    return True


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
