"""Switch platform - on/off holding registers (or coils)."""
from __future__ import annotations
from typing import Any, Optional
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.core import HomeAssistant
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
    entities = [DeyeModbusSwitch(coord, entry, c) for c in data.get("controls", []) if c.get("type") == "switch"]
    _LOGGER.info("Adding %s Deye switch entities", len(entities))
    if entities:
        async_add_entities(entities)


class DeyeModbusSwitch(CoordinatorEntity[dict[str, Any]], SwitchEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: DeyeModbusCoordinator, entry: ConfigEntry, cfg: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._entry = entry
        self._cfg = cfg
        self._register_type = cfg.get("register_type", "holding")
        self._address = int(cfg["address"])
        self._on = int(cfg.get("on_value", 1))
        self._off = int(cfg.get("off_value", 0))
        self._state: Optional[bool] = None
        self._attr_name = cfg.get("name")
        uid = cfg.get("unique_id") or f"switch_{self._address}"
        self._attr_unique_id = f"{entry.entry_id}_{uid}"
        self._attr_device_info = build_device_info(entry)
        self._attr_entity_registry_enabled_default = bool(cfg.get("enabled_default", True))
        if cfg.get("icon"):
            self._attr_icon = cfg["icon"]
        ec = cfg.get("entity_category")
        if ec:
            try:
                self._attr_entity_category = EntityCategory(ec)
            except ValueError:
                _LOGGER.warning("Unknown entity_category %r", ec)
        self._read_uid: Optional[str] = cfg.get("read_unique_id")
        self._read_factor: float = float(cfg.get("read_factor", 1.0))
        self._sync_from_sensor()

    @property
    def is_on(self) -> bool | None:
        return self._state

    async def async_turn_on(self, **kwargs):
        if await self._write(self._on):
            self._state = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        if await self._write(self._off):
            self._state = False
            self.async_write_ha_state()

    async def _write(self, value: int) -> bool:
        if self._register_type == "coil":
            ok = await self._coordinator.write_coil(self._address, value)
        else:
            ok = await self._coordinator.write_single_register(self._address, value)
        if not ok:
            raise HomeAssistantError(
                f"Writing {self._attr_name} to the inverter failed (register {self._address}); see log"
            )
        return ok

    def _sync_from_sensor(self) -> None:
        if not self._read_uid:
            return
        raw = (self._coordinator.data or {}).get(self._read_uid)
        if raw is None:
            return
        try:
            v = int(round(float(raw) / self._read_factor))
            self._state = (v == self._on)
        except Exception:  # noqa: BLE001
            pass

    def _handle_coordinator_update(self) -> None:
        self._sync_from_sensor()
        self.async_write_ha_state()
