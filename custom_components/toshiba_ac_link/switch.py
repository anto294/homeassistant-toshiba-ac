"""Switch platform for the Toshiba AC Link integration."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
import logging
from typing import Any, Generic, TypeVar

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from .toshiba.device import ToshibaAcDevice
from .toshiba.features import ToshibaAcFeatures
from .toshiba.properties import (
    ToshibaAcAirPureIon,
    ToshibaAcMeritA,
    ToshibaAcStatus,
    ToshibaAcWirelessLed,
)

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)

from homeassistant.const import EntityCategory

from . import ToshibaConfigEntry
from .entity import ToshibaAcStateEntity
from .entity_description import ToshibaAcEnumEntityDescriptionMixin

_LOGGER = logging.getLogger(__name__)

# Updates are pushed, and each command only sends one message to the cloud
PARALLEL_UPDATES = 0


# ---------------------------
#   ToshibaAcSwitchDescription
# ---------------------------
@dataclass(frozen=True, kw_only=True)
class ToshibaAcSwitchDescription(SwitchEntityDescription):
    """Describe a Toshiba AC switch entity type."""

    device_class = SwitchDeviceClass.SWITCH
    requires_ac_on: bool = True

    async def async_turn_on(self, _device: ToshibaAcDevice) -> None:
        """Turn the switch on."""

    async def async_turn_off(self, _device: ToshibaAcDevice) -> None:
        """Turn the switch off."""

    def is_on(self, _device: ToshibaAcDevice) -> bool:
        """Return True if the switch is on."""
        return False

    def is_supported(self, _features: ToshibaAcFeatures) -> bool:
        """
        Return True if the switch is available.

        Called to determine if the switch should be created in the first place, and then
        later to determine if it should be available based on the current AC mode.
        """
        return False

    def is_supported_by(self, device: ToshibaAcDevice) -> bool:
        """Return True if the device supports this switch, when creating entities."""
        return self.is_supported(device.supported)


TEnum = TypeVar("TEnum", bound=Enum)


# ---------------------------
#   ToshibaAcEnumSwitchDescription
# ---------------------------
@dataclass(frozen=True, kw_only=True)
class ToshibaAcEnumSwitchDescription(
    ToshibaAcSwitchDescription,
    ToshibaAcEnumEntityDescriptionMixin[TEnum],
    Generic[TEnum],
):
    """Describe a Toshiba AC switch that is controlled using an enum flag."""

    ac_on_value: TEnum | None = None
    ac_off_value: TEnum | None = None
    ac_attr_name: str = ""
    ac_attr_setter: str = ""

    async def async_turn_off(self, device: ToshibaAcDevice) -> None:
        """Turn the switch off, if it is on.

        ECO, high power, 8 °C and silent share one field of the unit, so an off from
        a switch that is already off would clear whichever of the others is active.
        """
        if self.is_on(device):
            await self.async_set_attr(device, self.ac_off_value)

    async def async_turn_on(self, device: ToshibaAcDevice) -> None:
        """Turn the switch on."""
        await self.async_set_attr(device, self.ac_on_value)

    def is_on(self, device: ToshibaAcDevice) -> bool:
        """Return True if the switch is on."""
        return self.get_device_attr(device) == self.ac_on_value

    def is_supported(self, features: ToshibaAcFeatures) -> bool:
        """Return True if the switch is available."""
        return self.ac_on_value in self.get_features_attr(features)


# ---------------------------
#   ToshibaAcWirelessLedSwitchDescription
# ---------------------------
@dataclass(frozen=True, kw_only=True)
class ToshibaAcWirelessLedSwitchDescription(ToshibaAcSwitchDescription):
    """Describe the switch of the wireless LED."""

    async def async_turn_on(self, device: ToshibaAcDevice) -> None:
        """Turn the LED on."""
        await device.set_ac_wireless_led(ToshibaAcWirelessLed.ON)

    async def async_turn_off(self, device: ToshibaAcDevice) -> None:
        """Turn the LED off."""
        await device.set_ac_wireless_led(ToshibaAcWirelessLed.OFF)

    def is_on(self, device: ToshibaAcDevice) -> bool:
        """Return True if the LED is on."""
        return device.ac_wireless_led == ToshibaAcWirelessLed.ON

    def is_supported(self, _features: ToshibaAcFeatures) -> bool:
        """Return True, because the features do not list the LED."""
        return True

    def is_supported_by(self, device: ToshibaAcDevice) -> bool:
        """Return True if the unit reports the LED: no report means there is none."""
        return device.ac_wireless_led is not ToshibaAcWirelessLed.NONE


_SWITCH_DESCRIPTIONS: Sequence[ToshibaAcSwitchDescription] = [
    ToshibaAcEnumSwitchDescription(
        key="8_degc_mode",
        translation_key="8_degc_mode",
        ac_attr_name="ac_merit_a",
        ac_on_value=ToshibaAcMeritA.HEATING_8C,
        ac_off_value=ToshibaAcMeritA.OFF,
    ),
    ToshibaAcEnumSwitchDescription(
        key="air_purifier",
        translation_key="air_purifier",
        ac_attr_name="ac_air_pure_ion",
        ac_on_value=ToshibaAcAirPureIon.ON,
        ac_off_value=ToshibaAcAirPureIon.OFF,
    ),
    ToshibaAcEnumSwitchDescription(
        key="eco_mode",
        translation_key="eco_mode",
        ac_attr_name="ac_merit_a",
        ac_on_value=ToshibaAcMeritA.ECO,
        ac_off_value=ToshibaAcMeritA.OFF,
    ),
    ToshibaAcEnumSwitchDescription(
        key="high_power_mode",
        translation_key="high_power_mode",
        ac_attr_name="ac_merit_a",
        ac_on_value=ToshibaAcMeritA.HIGH_POWER,
        ac_off_value=ToshibaAcMeritA.OFF,
    ),
    ToshibaAcWirelessLedSwitchDescription(
        key="wireless_led",
        translation_key="wireless_led",
        entity_category=EntityCategory.CONFIG,
        requires_ac_on=False,
    ),
]


# ---------------------------
#   async_setup_entry
# ---------------------------
async def async_setup_entry(
    _hass: HomeAssistant,
    config_entry: ToshibaConfigEntry,
    async_add_devices: AddConfigEntryEntitiesCallback,
) -> None:
    """Add switch entities for passed config_entry in HA."""
    device_manager = config_entry.runtime_data
    new_entities = []

    devices: list[ToshibaAcDevice] = await device_manager.get_devices()
    for device in devices:
        for entity_description in _SWITCH_DESCRIPTIONS:
            if entity_description.is_supported_by(device):
                new_entities.append(ToshibaAcSwitchEntity(device, entity_description))
            else:
                _LOGGER.debug(
                    "AC device %s does not support %s",
                    device.name,
                    entity_description.key,
                )

    if new_entities:
        _LOGGER.info("Adding %d switch entities", len(new_entities))
        async_add_devices(new_entities)


# ---------------------------
#   ToshibaAcSwitchEntity
# ---------------------------
class ToshibaAcSwitchEntity(ToshibaAcStateEntity, SwitchEntity):
    """Provides a switch entity based on a ToshibaAcSwitchDescription."""

    entity_description: ToshibaAcSwitchDescription
    _attr_has_entity_name = True

    def __init__(
        self, device: ToshibaAcDevice, entity_description: ToshibaAcSwitchDescription
    ) -> None:
        """Initialize the switch."""
        super().__init__(device)

        self.entity_description = entity_description
        self._attr_unique_id = f"{device.ac_unique_id}_{entity_description.key}"
        self.update_attrs()

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not self.entity_description.requires_ac_on:
            return super().available
        return (
            super().available
            and self._device.ac_status == ToshibaAcStatus.ON
            and self.entity_description.is_supported(
                self._device.supported.for_ac_mode(self._device.ac_mode)
            )
        )

    @property
    def is_on(self) -> bool | None:
        """Return True if the switch is on."""
        is_on: bool = self.entity_description.is_on(self._device)
        return is_on

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self.entity_description.async_turn_off(self._device)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self.entity_description.async_turn_on(self._device)
