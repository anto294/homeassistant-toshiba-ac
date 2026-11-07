"""Shared fixtures for Toshiba Link tests, which run on a real Home Assistant core."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
import tempfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ThreadedResolver
from homeassistant import config_entries, loader
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry,
    category_registry,
    device_registry,
    entity_registry,
    floor_registry,
    frame,
    issue_registry,
    label_registry,
    translation,
)
import pytest
from toshiba_ac_community.device.properties import (
    ToshibaAcAirPureIon,
    ToshibaAcFanMode,
    ToshibaAcHorizontalSwingMode,
    ToshibaAcMeritA,
    ToshibaAcMeritB,
    ToshibaAcMode,
    ToshibaAcPowerSelection,
    ToshibaAcSelfCleaning,
    ToshibaAcStatus,
    ToshibaAcSwingMode,
    ToshibaAcWirelessLed,
)

import custom_components
from custom_components.toshiba_ac_link.const import DOMAIN

ACCOUNT = {"username": "user@example.com", "password": "secret"}


@pytest.fixture(autouse=True)
def _resolver_without_zeroconf() -> Iterator[None]:
    """Resolve names without Home Assistant's resolver, which starts mDNS sockets."""
    with patch(
        "homeassistant.helpers.aiohttp_client._async_make_resolver",
        lambda _hass: ThreadedResolver(),
    ):
        yield


# ---------------------------
#   make_device
# ---------------------------
def make_device(**overrides: Any) -> MagicMock:
    """Return a stand-in for ToshibaAcDevice with the real enums of a typical unit."""
    device = MagicMock()
    device.name = "Living room"
    device.ac_id = "ac-id"
    device.ac_unique_id = "unit-1"
    device.ac_model_id = "2"
    device.device_id = "user@example.com_device"
    device.firmware_version = "5.0.00"
    device.merit_feature = "2c00"
    device.ac_status = ToshibaAcStatus.ON
    device.ac_mode = ToshibaAcMode.HEAT
    device.ac_temperature = 21
    device.ac_indoor_temperature = 20
    device.ac_outdoor_temperature = 7
    device.ac_fan_mode = ToshibaAcFanMode.AUTO
    device.ac_swing_mode = ToshibaAcSwingMode.OFF
    device.ac_power_selection = ToshibaAcPowerSelection.POWER_100
    device.ac_merit_a = ToshibaAcMeritA.OFF
    device.ac_merit_b = ToshibaAcMeritB.OFF
    device.ac_air_pure_ion = ToshibaAcAirPureIon.OFF
    device.ac_self_cleaning = ToshibaAcSelfCleaning.OFF
    device.ac_wireless_led = ToshibaAcWirelessLed.ON
    device.ac_horizontal_swing_mode = ToshibaAcHorizontalSwingMode.NONE
    device.ac_energy_consumption = None
    device.supports_independent_swing = False

    supported = device.supported
    supported.ac_mode = list(ToshibaAcMode)
    supported.ac_fan_mode = list(ToshibaAcFanMode)
    supported.ac_swing_mode = list(ToshibaAcSwingMode)
    supported.ac_power_selection = list(ToshibaAcPowerSelection)
    supported.ac_merit_a = list(ToshibaAcMeritA)
    supported.ac_merit_b = list(ToshibaAcMeritB)
    supported.ac_air_pure_ion = list(ToshibaAcAirPureIon)
    supported.ac_energy_report = True
    supported.for_ac_mode.return_value = supported

    for setter in (
        "set_ac_status",
        "set_ac_mode",
        "set_ac_temperature",
        "set_ac_fan_mode",
        "set_ac_swing_mode",
        "set_ac_vertical_swing_mode",
        "set_ac_horizontal_swing_mode",
        "set_ac_power_selection",
        "set_ac_merit_a",
        "set_ac_merit_b",
        "set_ac_air_pure_ion",
        "set_ac_wireless_led",
    ):
        setattr(device, setter, AsyncMock())

    for name, value in overrides.items():
        setattr(device, name, value)
    return device


# ---------------------------
#   make_manager
# ---------------------------
def make_manager(devices: list[MagicMock] | None = None) -> MagicMock:
    """Return a stand-in for ToshibaAcDeviceManager that connects at once."""
    manager = MagicMock()
    manager.connect = AsyncMock(return_value="sas-token")
    manager.shutdown = AsyncMock()
    manager.get_devices = AsyncMock(
        return_value=[make_device()] if devices is None else devices
    )
    manager.amqp_api.device.connected = True
    manager.http_api.access_token = "access-token"
    manager.http_api.access_token_type = "Bearer"
    manager.http_api.consumer_id = "consumer-1"
    return manager


# ---------------------------
#   async_start_hass
# ---------------------------
async def async_start_hass() -> HomeAssistant:
    """Return a real Home Assistant core that can load this integration."""
    hass = HomeAssistant(tempfile.mkdtemp())
    hass.config.skip_pip = True
    frame.async_setup(hass)
    loader.async_setup(hass)
    translation.async_setup(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    device_registry.async_setup(hass)
    for registry in (
        area_registry,
        floor_registry,
        label_registry,
        category_registry,
        device_registry,
        entity_registry,
        issue_registry,
    ):
        await registry.async_load(hass)

    hass.data[loader.DATA_CUSTOM_COMPONENTS] = {
        DOMAIN: loader.Integration.resolve_from_root(hass, custom_components, DOMAIN)
    }
    return hass


# ---------------------------
#   run_in_hass
# ---------------------------
@pytest.fixture
def run_in_hass() -> Callable[[Callable[[HomeAssistant], Awaitable[None]]], None]:
    """Return a runner for a scenario on a real core."""

    def _run(scenario: Callable[[HomeAssistant], Awaitable[None]]) -> None:
        async def main() -> None:
            hass = await async_start_hass()
            try:
                await scenario(hass)
            finally:
                await hass.async_stop(force=True)

        asyncio.run(main())

    return _run


# ---------------------------
#   async_add_entry
# ---------------------------
async def async_add_entry(
    hass: HomeAssistant, manager: MagicMock
) -> config_entries.ConfigEntry:
    """Add an entry through the real setup form, with a fake device manager."""
    with (
        patch(
            "custom_components.toshiba_ac_link.ToshibaAcDeviceManager",
            return_value=manager,
        ),
        patch(
            "custom_components.toshiba_ac_link.config_flow.ToshibaAcDeviceManager",
            return_value=manager,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(ACCOUNT)
        )
        await hass.async_block_till_done()
    assert result["type"] == "create_entry", result
    return result["result"]
