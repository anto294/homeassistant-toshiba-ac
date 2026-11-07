"""The Toshiba Link integration."""

from __future__ import annotations

import asyncio
import logging

from azure.iot.device.common.transport_exceptions import (
    ConnectionDroppedError,
    UnauthorizedError,
)
from azure.iot.device.exceptions import CredentialError
from toshiba_ac_community.device_manager import ToshibaAcDeviceManager
from toshiba_ac_community.utils import amqp_api as toshiba_amqp_api
from toshiba_ac_community.utils.http_api import ToshibaAcHttpApiAuthError

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)

from .const import CONNECT_TIMEOUT_S, DOMAIN
from .reconnect import ToshibaReconnectManager

# Monkey-patch: translate the raw Azure connection errors into something the
# user can act on. IoT Hub allows only one active connection per device, so a
# second instance - another Home Assistant or the Toshiba app - makes the
# library report "Credentials invalid, could not connect", which reads like a
# wrong password but is not one.

ONE_CONNECTION_HINT = (
    "Toshiba allows only one active connection per account. Another Home "
    "Assistant instance or the Toshiba app is most likely holding it. Stop "
    "that one and call the toshiba_ac_link.reconnect service here."
)

CONNECTION_REFUSED_ERROR = (
    f"Toshiba refused the cloud connection: {{err}}. {ONE_CONNECTION_HINT}"
)

CONNECTION_DROPPED_ERROR = (
    "The connection to the Toshiba cloud dropped while sending a command. "
    f"{ONE_CONNECTION_HINT}"
)

NOTIFICATION_ID = "toshiba_ac_link_connection_refused"


_original_send_message = toshiba_amqp_api.ToshibaAcAmqpApi.send_message


async def _patched_send_message(
    self: toshiba_amqp_api.ToshibaAcAmqpApi, message: str
) -> None:
    """Send as the library does, and name the real cause instead of "Credentials invalid"."""
    try:
        await _original_send_message(self, message)
    except (CredentialError, UnauthorizedError, ConnectionDroppedError) as ex:
        raise HomeAssistantError(CONNECTION_REFUSED_ERROR.format(err=ex)) from ex


toshiba_amqp_api.ToshibaAcAmqpApi.send_message = _patched_send_message

PLATFORMS = ["binary_sensor", "climate", "select", "sensor", "switch"]

SHUTDOWN_TIMEOUT_S = 10

# Raised by the Azure client when IoT Hub refuses the connection. Setup always
# registers a new SAS token, so these point at a competing connection.
CONNECTION_REFUSED_ERRORS = (ValueError, CredentialError, UnauthorizedError)

_LOGGER = logging.getLogger(__name__)

# The Azure client logs a warning for each dropped connection and retry, and the
# integration reports what matters by itself.
logging.getLogger("azure.iot.device").setLevel(logging.ERROR)


def _create_device_manager(entry: ConfigEntry) -> ToshibaAcDeviceManager:
    """Build the device manager from the stored account and tokens.

    A SAS token is never reused, because it may have expired while Home Assistant
    was stopped. The access token is: it lives long, and skipping the login avoids
    the most rate-limited endpoint of Toshiba's API. A stale one costs one 401 and
    a new login inside the library.
    """
    return ToshibaAcDeviceManager(
        entry.data["username"],
        entry.data["password"],
        entry.data["device_id"],
        sas_token=None,
        access_token=entry.data.get("access_token"),
        access_token_type=entry.data.get("access_token_type"),
        consumer_id=entry.data.get("consumer_id"),
    )


async def _async_connect(device_manager: ToshibaAcDeviceManager) -> str:
    """Connect and return the SAS token that is in use."""
    return await asyncio.wait_for(device_manager.connect(), timeout=CONNECT_TIMEOUT_S)


async def _async_shutdown(device_manager: ToshibaAcDeviceManager) -> None:
    """Close the connection, without letting a slow or failed close stop the caller."""
    try:
        await asyncio.wait_for(device_manager.shutdown(), timeout=SHUTDOWN_TIMEOUT_S)
    except Exception as ex:
        _LOGGER.warning("Error while shutting down device manager: %s", ex)


def _setup_error(ex: Exception) -> ConfigEntryError:
    """Translate a connect failure into the error Home Assistant reacts to."""
    if isinstance(ex, TimeoutError):
        return ConfigEntryNotReady(
            f"Connection to Toshiba AC service timed out after {CONNECT_TIMEOUT_S} seconds"
        )

    if isinstance(ex, CONNECTION_REFUSED_ERRORS):
        return ConfigEntryNotReady(
            f"Failed to connect to Toshiba AC service: {ex}. {ONE_CONNECTION_HINT}"
        )

    # Only a real login failure: a rate limit or a 403 from the WAF must not ask
    # the user for a password, and must keep retrying.
    if isinstance(ex, ToshibaAcHttpApiAuthError):
        return ConfigEntryAuthFailed(f"Authentication failed: {ex}")

    return ConfigEntryNotReady(f"Failed to connect to Toshiba AC service: {ex}")


def _notify_connection_refused(hass: HomeAssistant, message: str) -> None:
    """Surface the refusal in the UI, the log alone is easy to miss.

    `persistent_notification.async_create` is a callback, not a coroutine.
    Awaiting it raises TypeError: 'NoneType' object can't be awaited.
    """
    persistent_notification.async_create(
        hass,
        message,
        title="Toshiba Link: cloud connection refused",
        notification_id=NOTIFICATION_ID,
    )


def _clear_connection_notice(hass: HomeAssistant) -> None:
    """Remove the notice once the integration works again. Callback, not coroutine."""
    persistent_notification.async_dismiss(hass, NOTIFICATION_ID)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Toshiba Link component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Toshiba Link from a config entry."""
    device_manager = _create_device_manager(entry)

    async def access_token_updated(token_info: tuple[str, str, str]) -> None:
        """Store a new access token, so the next start needs no login."""
        access_token, access_token_type, consumer_id = token_info
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                "access_token": access_token,
                "access_token_type": access_token_type,
                "consumer_id": consumer_id,
            },
        )

    # Added before connect(), so the token of a login during connect() is stored too
    device_manager.on_access_token_updated_callback.add(access_token_updated)

    try:
        await _async_connect(device_manager)
    except CONNECTION_REFUSED_ERRORS as ex:
        refusal = CONNECTION_REFUSED_ERROR.format(err=ex)
        _LOGGER.error("%s", refusal)
        _notify_connection_refused(hass, refusal)
        raise _setup_error(ex) from ex
    except Exception as ex:
        raise _setup_error(ex) from ex

    _clear_connection_notice(hass)

    reconnect = ToshibaReconnectManager(hass, entry)
    entry.async_on_unload(reconnect.stop)

    async def sas_token_updated(_sas_token: str) -> None:
        """Handle a new SAS token, which means the client reconnected by itself."""
        reconnect.recovered(device_manager)

    device_manager.on_sas_token_updated_callback.add(sas_token_updated)
    reconnect.attach(device_manager)

    # Fetch the devices once before the platforms ask for them, so a slow answer
    # ends in a retry and not in a platform setup that gets cancelled.
    try:
        await asyncio.wait_for(device_manager.get_devices(), timeout=CONNECT_TIMEOUT_S)
    except Exception as ex:
        await _async_shutdown(device_manager)
        raise _setup_error(ex) from ex

    # Store device manager
    hass.data[DOMAIN][entry.entry_id] = device_manager

    # Register reconnect service (once per domain)
    await _async_register_services(hass)

    # Forward setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services."""
    if hass.services.has_service(DOMAIN, "reconnect"):
        return

    async def handle_reconnect(call: ServiceCall) -> None:
        """Handle the reconnect service call."""
        _LOGGER.info("Reconnect service called - reloading all config entries")
        # Reload all config entries for this domain
        for entry in hass.config_entries.async_entries(DOMAIN):
            await hass.config_entries.async_reload(entry.entry_id)

    hass.services.async_register(DOMAIN, "reconnect", handle_reconnect)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Toshiba Link integration")

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        await _async_shutdown(hass.data[DOMAIN].pop(entry.entry_id))

    return unload_ok
