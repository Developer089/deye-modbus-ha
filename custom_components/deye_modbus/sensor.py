"""Sensor platform - one entity per mapped read register."""
from __future__ import annotations
from typing import Any
import logging

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DeyeModbusCoordinator, RegisterDef
from .device_helper import build_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coord: DeyeModbusCoordinator = data["coordinator"]
    regs: list[RegisterDef] = data["registers"]
    entities = [DeyeRegisterSensor(coord, entry, r) for r in regs]
    _LOGGER.info("Adding %s Deye sensor entities", len(entities))
    if entities:
        async_add_entities(entities)


class DeyeRegisterSensor(CoordinatorEntity[dict[str, Any]], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: DeyeModbusCoordinator, entry: ConfigEntry, reg: RegisterDef) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._reg = reg
        self._options = reg.options or None
        uid = reg.unique_id or f"s_{reg.address}"
        self._attr_unique_id = f"{entry.entry_id}_{uid}"
        self._attr_name = reg.name
        self._attr_device_info = build_device_info(entry)
        self._attr_native_unit_of_measurement = reg.unit_of_measurement
        self._attr_entity_registry_enabled_default = bool(reg.enabled_default)
        if reg.precision is not None:
            self._attr_suggested_display_precision = int(reg.precision)
        if reg.icon:
            self._attr_icon = reg.icon
        if reg.entity_category:
            try:
                self._attr_entity_category = EntityCategory(reg.entity_category)
            except ValueError:
                _LOGGER.warning("Unknown entity_category %r for %s", reg.entity_category, reg.name)
        if reg.device_class:
            try:
                self._attr_device_class = SensorDeviceClass(reg.device_class)
            except Exception:  # noqa: BLE001
                self._attr_device_class = None
        if reg.state_class:
            try:
                self._attr_state_class = SensorStateClass(reg.state_class)
            except Exception:  # noqa: BLE001
                self._attr_state_class = None
        # Enum sensors must not carry a numeric device/state class.
        if self._options is not None:
            self._attr_device_class = None
            self._attr_state_class = None

    @property
    def native_value(self) -> Any:
        raw = (self.coordinator.data or {}).get(self._reg.unique_id)
        if self._options is not None and raw is not None:
            try:
                key = int(round(float(raw)))
            except Exception:  # noqa: BLE001
                return raw
            return self._options.get(key, str(key))
        return raw

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
