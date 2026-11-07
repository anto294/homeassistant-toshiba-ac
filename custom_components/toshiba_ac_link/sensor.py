"""Platform for sensor integration."""

from __future__ import annotations

from datetime import date, datetime
import logging

from toshiba_ac_community.device import (
    ToshibaAcDevice,
    ToshibaAcDeviceEnergyConsumption,
)

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfTemperature
from homeassistant.helpers.typing import StateType

from .entity import ToshibaAcEntity, ToshibaAcStateEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_devices):
    """Add sensor entities for passed config_entry in HA."""
    device_manager = config_entry.runtime_data
    new_entities = []

    devices: list[ToshibaAcDevice] = await device_manager.get_devices()
    for device in devices:
        if device.supported.ac_energy_report:
            new_entities.append(ToshibaEnergySensor(device))
        else:
            _LOGGER.debug(
                "AC device %s does not support energy monitoring", device.name
            )

        # The outdoor value can be None while the outdoor unit is off
        new_entities.append(ToshibaTempSensor(device, "indoor_temperature"))
        new_entities.append(ToshibaTempSensor(device, "outdoor_temperature"))

    if new_entities:
        _LOGGER.info("Adding %d sensor entities", len(new_entities))
        async_add_devices(new_entities)


class ToshibaEnergySensor(ToshibaAcEntity, SensorEntity):
    """Report the energy that the unit used since the start of the year."""

    _attr_has_entity_name = True
    _attr_translation_key = "energy_consumption"
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _ac_energy_consumption: ToshibaAcDeviceEnergyConsumption | None = None

    def __init__(self, toshiba_device: ToshibaAcDevice) -> None:
        """Initialize the sensor."""
        super().__init__(toshiba_device)
        self._attr_unique_id = f"{self._device.ac_unique_id}_sensor"

    def _update_energy_consumption(self) -> None:
        """Read the latest energy consumption from the device."""
        self._ac_energy_consumption = self._device.ac_energy_consumption
        self._attr_last_reset = (
            self._ac_energy_consumption.since if self._ac_energy_consumption else None
        )

    async def state_changed(self, _dev: ToshibaAcDevice) -> None:
        """Write the state after the device reported new values."""
        self._update_energy_consumption()
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Subscribe to updates, and read what arrived before the entity was added.

        The device manager fetches energy as soon as it finds the devices, and the
        next fetch is an hour later, so a first update that is missed here would
        leave the sensor unknown for that long.
        """
        self._device.on_energy_consumption_changed_callback.add(self.state_changed)
        self._update_energy_consumption()

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from updates."""
        self._device.on_energy_consumption_changed_callback.remove(self.state_changed)

    @property
    def native_value(self) -> StateType | date | datetime:
        """Return the value reported by the sensor."""
        if self._ac_energy_consumption:
            return self._ac_energy_consumption.energy_wh
        return None


class ToshibaTempSensor(ToshibaAcStateEntity, SensorEntity):
    """Provides a Toshiba Temperature Sensors."""

    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True

    def __init__(self, device: ToshibaAcDevice, key: str) -> None:
        """Initialize the sensor of the given temperature."""
        super().__init__(device)
        self._key = key
        self._attr_unique_id = f"{device.ac_unique_id}_{key}"
        self._attr_translation_key = key

    @property
    def _temperature(self) -> int | None:
        """Return the temperature from the device."""
        value: int | None = getattr(self._device, f"ac_{self._key}")
        return value

    @property
    def available(self) -> bool:
        """Return True if the unit reports this temperature."""
        return self._temperature is not None and super().available

    @property
    def native_value(self) -> int | None:
        """Return the value reported by the sensor."""
        return self._temperature
