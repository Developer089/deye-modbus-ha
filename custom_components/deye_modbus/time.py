"""Time platform - holding registers that store a clock time as HHMM.

Deye keeps the six time-of-use slot boundaries as a decimal HHMM number
(2355 = 23:55) in registers 148-153.
"""
from __future__ import annotations
from datetime import time as dt_time
from typing import Any, Optional
import logging

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DeyeModbusCoordinator
from .device_helper import build_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coord: DeyeModbusCoordinator = data["coordinator"]
    entities = [DeyeModbusTime(coord, entry, c) for c in data.get("controls", []) if c.get("type") == "time"]
    _LOGGER.info("Adding %s Deye time entities", len(entities))
    if entities:
        async_add_entities(entities)


def hhmm_to_time(raw: int) -> dt_time | None:
    """2355 -> 23:55. Returns None for values that are not a valid clock time."""
    hours, minutes = divmod(int(raw), 100)
    if 0 <= hours <= 23 and 0 <= minutes <= 59:
        return dt_time(hours, minutes)
    return None


def time_to_hhmm(value: dt_time) -> int:
    return value.hour * 100 + value.minute


class DeyeModbusTime(CoordinatorEntity[dict[str, Any]], TimeEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: DeyeModbusCoordinator, entry: ConfigEntry, cfg: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._address = int(cfg["address"])
        self._attr_name = cfg.get("name")
        uid = cfg.get("unique_id") or f"time_{self._address}"
        self._attr_unique_id = f"{entry.entry_id}_{uid}"
        self._attr_device_info = build_device_info(entry)
        self._attr_entity_registry_enabled_default = bool(cfg.get("enabled_default", True))
        self._attr_icon = cfg.get("icon") or "mdi:clock-outline"
        ec = cfg.get("entity_category")
        if ec:
            try:
                self._attr_entity_category = EntityCategory(ec)
            except ValueError:
                _LOGGER.warning("Unknown entity_category %r for %s", ec, self._attr_name)
        # The inverter's own screen only offers 5-minute steps; round to that
        # unless the map says otherwise.
        self._step_minutes = max(1, int(cfg.get("step_minutes", 5)))
        self._read_uid: Optional[str] = cfg.get("read_unique_id")
        self._value: dt_time | None = None
        self._sync_from_sensor()

    @property
    def native_value(self) -> dt_time | None:
        return self._value

    async def async_set_value(self, value: dt_time) -> None:
        minutes = value.hour * 60 + value.minute
        minutes -= minutes % self._step_minutes
        target = dt_time(minutes // 60, minutes % 60)
        ok = await self._coordinator.write_single_register(self._address, time_to_hhmm(target))
        if not ok:
            raise HomeAssistantError(
                f"Writing {self._attr_name} to the inverter failed (register {self._address}); see log"
            )
        self._value = target
        self.async_write_ha_state()

    def _sync_from_sensor(self) -> None:
        if not self._read_uid:
            return
        raw = (self._coordinator.data or {}).get(self._read_uid)
        if raw is None:
            return
        try:
            parsed = hhmm_to_time(int(round(float(raw))))
        except Exception:  # noqa: BLE001
            return
        if parsed is not None:
            self._value = parsed

    def _handle_coordinator_update(self) -> None:
        self._sync_from_sensor()
        self.async_write_ha_state()
