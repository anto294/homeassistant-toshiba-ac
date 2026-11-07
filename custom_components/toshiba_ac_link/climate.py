"""Platform for climate integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from toshiba_ac_community.device import (
    ToshibaAcDevice,
    ToshibaAcFanMode,
    ToshibaAcHorizontalSwingMode,
    ToshibaAcMeritA,
    ToshibaAcMode,
    ToshibaAcPowerSelection,
    ToshibaAcSelfCleaning,
    ToshibaAcStatus,
    ToshibaAcSwingMode,
    ToshibaAcVerticalSwingMode,
)
from toshiba_ac_community.utils import pretty_enum_name

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature

from .entity import ToshibaAcStateEntity
from .feature_list import get_feature_by_name, get_feature_list

_LOGGER = logging.getLogger(__name__)

NORMAL_MIN_TEMP = 17
NORMAL_MAX_TEMP = 30
HEATING_8C_MIN_TEMP = 5

TOSHIBA_TO_HVAC_MODE = {
    ToshibaAcMode.AUTO: HVACMode.AUTO,
    ToshibaAcMode.COOL: HVACMode.COOL,
    ToshibaAcMode.HEAT: HVACMode.HEAT,
    ToshibaAcMode.DRY: HVACMode.DRY,
    ToshibaAcMode.FAN: HVACMode.FAN_ONLY,
}

HVAC_MODE_TO_TOSHIBA = {v: k for k, v in TOSHIBA_TO_HVAC_MODE.items()}

# NONE means no particular position, which is the off state of the horizontal vane
HORIZONTAL_SWING_TO_NAME = {
    mode: "Off" if mode == ToshibaAcHorizontalSwingMode.NONE else pretty_enum_name(mode)
    for mode in ToshibaAcHorizontalSwingMode
}
NAME_TO_HORIZONTAL_SWING = {v: k for k, v in HORIZONTAL_SWING_TO_NAME.items()}

# Legacy presets that only describe the vertical vane. On units with independent
# axes the legacy value also means "horizontal none", so these go through the
# per-axis call, which sends the current horizontal position again.
SWING_TO_VERTICAL = {
    ToshibaAcSwingMode.OFF: ToshibaAcVerticalSwingMode.NONE,
    ToshibaAcSwingMode.SWING_VERTICAL: ToshibaAcVerticalSwingMode.SWING,
    ToshibaAcSwingMode.FIXED_1: ToshibaAcVerticalSwingMode.FIXED_1,
    ToshibaAcSwingMode.FIXED_2: ToshibaAcVerticalSwingMode.FIXED_2,
    ToshibaAcSwingMode.FIXED_3: ToshibaAcVerticalSwingMode.FIXED_3,
    ToshibaAcSwingMode.FIXED_4: ToshibaAcVerticalSwingMode.FIXED_4,
    ToshibaAcSwingMode.FIXED_5: ToshibaAcVerticalSwingMode.FIXED_5,
}


async def async_setup_entry(hass, config_entry, async_add_devices):
    """Add climate entities for passed config_entry in HA."""
    device_manager = config_entry.runtime_data

    devices = await device_manager.get_devices()
    new_entities = [ToshibaClimate(device) for device in devices]

    if new_entities:
        _LOGGER.info("Adding %d climate entities", len(new_entities))
        async_add_devices(new_entities)


class ToshibaClimate(ToshibaAcStateEntity, ClimateEntity):
    """Provides a Toshiba climates."""

    # This is the main entity for the device
    _attr_has_entity_name = True
    _attr_name = None

    _attr_supported_features = (
        ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_target_temperature_step = 1
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(self, toshiba_device: ToshibaAcDevice):
        """Initialize the climate."""
        super().__init__(toshiba_device)

        self._attr_unique_id = f"{self._device.ac_unique_id}_climate"
        self._attr_fan_modes = get_feature_list(self._device.supported.ac_fan_mode)
        self._attr_swing_modes = get_feature_list(self._device.supported.ac_swing_mode)

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return the features, with horizontal swing once the unit reports it.

        2026 Shorai Curve units encode the two vanes independently. That is only
        visible once the unit has reported that encoding, which can happen after
        startup, so this cannot be decided once.
        """
        features = self._attr_supported_features
        if self._device.supports_independent_swing:
            features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
        return features

    @property
    def is_on(self):
        """Return True if the device runs at the user's request.

        During self cleaning the AC reports ON, because the fan dries the heat
        exchanger, but the user turned it off and the Toshiba app shows it as off.
        Turning it on goes through set_ac_status(ON), which ends the cleaning.
        """
        return (
            self._device.ac_status == ToshibaAcStatus.ON
            and self._device.ac_self_cleaning != ToshibaAcSelfCleaning.ON
        )

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        set_temperature = kwargs[ATTR_TEMPERATURE]

        set_temperature = max(self.min_temp, min(self.max_temp, set_temperature))

        await self._device.set_ac_temperature(set_temperature)

    # PRESET MODE / POWER SETTING

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode, e.g., home, away, temp.

        Requires SUPPORT_PRESET_MODE.
        """
        if self._device.ac_self_cleaning == ToshibaAcSelfCleaning.ON:
            return "cleaning"

        if not self.is_on:
            return None

        return pretty_enum_name(self._device.ac_power_selection)

    @property
    def preset_modes(self) -> list[str] | None:
        """Return a list of available preset modes.

        Requires SUPPORT_PRESET_MODE.
        """
        return get_feature_list(self._device.supported.ac_power_selection)

    async def async_turn_on(self) -> None:
        """Turn device on."""
        await self._device.set_ac_status(ToshibaAcStatus.ON)

    async def async_turn_off(self) -> None:
        """Turn device off."""
        await self._device.set_ac_status(ToshibaAcStatus.OFF)

    async def async_toggle(self) -> None:
        """Toggle device status."""
        state = self._device.ac_status
        if state == ToshibaAcStatus.OFF:
            await self.async_turn_on()
        else:
            await self.async_turn_off()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        _LOGGER.info("Toshiba Climate setting preset_mode: %s", preset_mode)

        feature_list_id = get_feature_by_name(
            list(ToshibaAcPowerSelection), preset_mode
        )
        if feature_list_id is not None:
            await self._device.set_ac_power_selection(feature_list_id)

    @property
    def hvac_mode(self) -> HVACMode | str | None:
        """Return hvac operation ie. heat, cool mode."""
        if not self.is_on:
            return HVACMode.OFF

        return TOSHIBA_TO_HVAC_MODE[self._device.ac_mode]

    @property
    def hvac_modes(self) -> list[HVACMode] | list[str]:
        """Return the list of available hvac operation modes."""
        available_modes = [HVACMode.OFF]
        for toshiba_mode, hvac_mode in TOSHIBA_TO_HVAC_MODE.items():
            if toshiba_mode in self._device.supported.ac_mode:
                available_modes.append(hvac_mode)
        return available_modes

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        _LOGGER.info("Toshiba Climate setting hvac_mode: %s", hvac_mode)

        if hvac_mode == HVACMode.OFF:
            await self._device.set_ac_status(ToshibaAcStatus.OFF)
        else:
            if not self.is_on:
                await self._device.set_ac_status(ToshibaAcStatus.ON)
            await self._device.set_ac_mode(HVAC_MODE_TO_TOSHIBA[hvac_mode])

    async def async_set_fan_mode(self, fan_mode):
        """Set new target fan mode."""
        _LOGGER.info("Toshiba Climate setting fan_mode: %s", fan_mode)
        if not self.is_on:
            await self._device.set_ac_status(ToshibaAcStatus.ON)
        fan_mode = fan_mode.title().replace("_", " ")
        feature_list_id = get_feature_by_name(list(ToshibaAcFanMode), fan_mode)
        if feature_list_id is not None:
            await self._device.set_ac_fan_mode(feature_list_id)

    @property
    def fan_mode(self) -> str | None:
        """Return the fan setting."""
        return pretty_enum_name(self._device.ac_fan_mode)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set new target swing operation."""
        swing_mode = swing_mode.title().replace("_", " ")
        feature_list_id = get_feature_by_name(list(ToshibaAcSwingMode), swing_mode)
        if feature_list_id is None:
            return
        vertical = SWING_TO_VERTICAL.get(feature_list_id)
        if vertical is not None and self._device.supports_independent_swing:
            await self._device.set_ac_vertical_swing_mode(vertical)
        else:
            await self._device.set_ac_swing_mode(feature_list_id)

    @property
    def swing_mode(self) -> str | None:
        """Return the swing setting."""
        return pretty_enum_name(self._device.ac_swing_mode)

    @property
    def swing_horizontal_modes(self) -> list[str] | None:
        """Return the horizontal swing positions."""
        if not self._device.supports_independent_swing:
            return None
        return list(NAME_TO_HORIZONTAL_SWING)

    @property
    def swing_horizontal_mode(self) -> str | None:
        """Return the horizontal swing setting."""
        if not self._device.supports_independent_swing:
            return None
        return HORIZONTAL_SWING_TO_NAME[self._device.ac_horizontal_swing_mode]

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        """Set the horizontal swing position."""
        mode = NAME_TO_HORIZONTAL_SWING.get(swing_horizontal_mode)
        if mode is not None:
            await self._device.set_ac_horizontal_swing_mode(mode)

    @property
    def native_current_temperature(self) -> float | None:
        """Return the current temperature."""
        return self._device.ac_indoor_temperature

    @property
    def native_target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        return self._device.ac_temperature

    @property
    def _heating_8c_active(self) -> bool:
        """Return True if frost protection is on, not just supported."""
        return self._device.ac_merit_a == ToshibaAcMeritA.HEATING_8C

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        return HEATING_8C_MIN_TEMP if self._heating_8c_active else NORMAL_MIN_TEMP

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature.

        In frost protection, model 3 units (such as the Shorai Edge) accept up to
        16 and model 2 units (such as the Shorai+) up to 13.
        """
        if not self._heating_8c_active:
            return NORMAL_MAX_TEMP
        return 16 if self._device.ac_model_id == "3" else 13

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Return entity specific state attributes.

        Implemented by platform classes. Convention for attribute names
        is lowercase snake_case.
        """
        return {
            "merit_a_feature": self._device.ac_merit_a.name,
            "merit_b_feature": self._device.ac_merit_b.name,
            "air_pure_ion": self._device.ac_air_pure_ion.name,
            "self_cleaning": self._device.ac_self_cleaning.name,
            "outdoor_temperature": self._device.ac_outdoor_temperature,
        }
