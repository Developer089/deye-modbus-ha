"""Select platform - map holding-register values to friendly options."""
from __future__ import annotations
from typing import Any, Optional
import logging

from homeassistant.components.select import SelectEntity
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
    entities = []
    for c in data.get("controls", []):
        if c.get("type") == "select":
            entities.append(DeyeModbusSelect(coord, entry, c))
        elif c.get("type") == "select32":
            entities.append(DeyeModbusSelect32(coord, entry, c))
    _LOGGER.info("Adding %s Deye select entities", len(entities))
    if entities:
        async_add_entities(entities)


def _apply_entity_category(entity, cfg):
    ec = cfg.get("entity_category")
    if ec:
        try:
            entity._attr_entity_category = EntityCategory(ec)
        except ValueError:
            _LOGGER.warning("Unknown entity_category %r", ec)


class DeyeModbusSelect(CoordinatorEntity[dict[str, Any]], SelectEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: DeyeModbusCoordinator, entry: ConfigEntry, cfg: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._entry = entry
        self._cfg = cfg
        self._address = int(cfg["address"])
        opts = cfg.get("options", [])
        self._labels = [o["label"] for o in opts]
        self._value_by_label = {o["label"]: int(o["value"]) for o in opts}
        self._label_by_value = {int(o["value"]): o["label"] for o in opts}
        self._attr_name = cfg.get("name")
        uid = cfg.get("unique_id") or f"select_{self._address}"
        self._attr_unique_id = f"{entry.entry_id}_{uid}"
        self._attr_device_info = build_device_info(entry)
        self._attr_options = self._labels
        self._attr_entity_registry_enabled_default = bool(cfg.get("enabled_default", True))
        if cfg.get("icon"):
            self._attr_icon = cfg["icon"]
        _apply_entity_category(self, cfg)
        self._current_option: Optional[str] = None
        self._read_uid: Optional[str] = cfg.get("read_unique_id")
        self._read_factor: float = float(cfg.get("read_factor", 1.0))
        self._sync_from_sensor()

    @property
    def current_option(self) -> str | None:
        return self._current_option

    async def async_select_option(self, option: str) -> None:
        if option not in self._value_by_label:
            return
        ok = await self._coordinator.write_single_register(self._address, self._value_by_label[option])
        if not ok:
            raise HomeAssistantError(
                f"Writing {self._attr_name} to the inverter failed (register {self._address}); see log"
            )
        if ok:
            self._current_option = option
            self.async_write_ha_state()
            await self._coordinator.async_request_refresh()

    def _sync_from_sensor(self) -> None:
        if not self._read_uid:
            return
        raw = (self._coordinator.data or {}).get(self._read_uid)
        if raw is None:
            return
        try:
            v = int(round(float(raw) / self._read_factor))
            self._current_option = self._label_by_value.get(v, None)
        except Exception:  # noqa: BLE001
            pass

    def _handle_coordinator_update(self) -> None:
        self._sync_from_sensor()
        self.async_write_ha_state()


class DeyeModbusSelect32(CoordinatorEntity[dict[str, Any]], SelectEntity):
    """Select that edits only a bitfield inside a packed 32-bit register."""
    _attr_has_entity_name = True

    def __init__(self, coordinator: DeyeModbusCoordinator, entry: ConfigEntry, cfg: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._entry = entry
        self._cfg = cfg
        self._base = int(cfg["base_address"])
        self._order = cfg.get("word_order", "high_low")
        self._mask = int(str(cfg.get("mask")), 0) if "mask" in cfg else 0xFFFFFFFF
        self._shift = int(cfg.get("shift", 0))
        opts = cfg.get("options", [])
        self._labels = [o["label"] for o in opts]
        self._value_by_label = {o["label"]: int(o["value"]) for o in opts}
        self._label_by_value = {int(o["value"]): o["label"] for o in opts}
        self._attr_name = cfg.get("name")
        uid = cfg.get("unique_id") or f"select32_{self._base}"
        self._attr_unique_id = f"{entry.entry_id}_{uid}"
        self._attr_device_info = build_device_info(entry)
        self._attr_options = self._labels
        self._attr_entity_registry_enabled_default = bool(cfg.get("enabled_default", True))
        if cfg.get("icon"):
            self._attr_icon = cfg["icon"]
        _apply_entity_category(self, cfg)
        self._current_option: Optional[str] = None
        self._read_uid: Optional[str] = cfg.get("read_unique_id")
        self._sync_from_sensor()

    @property
    def current_option(self) -> str | None:
        return self._current_option

    def _get_u32(self) -> Optional[int]:
        if not self._read_uid:
            return None
        raw = (self._coordinator.data or {}).get(self._read_uid)
        if raw is None:
            return None
        try:
            return int(raw)
        except Exception:  # noqa: BLE001
            return None

    def _recompute_option_from_u32(self, v: int) -> None:
        field = (v & self._mask) >> self._shift
        self._current_option = self._label_by_value.get(field, None)

    async def async_select_option(self, option: str) -> None:
        if option not in self._value_by_label:
            return
        old = self._get_u32()
        if old is None:
            return
        field_val = self._value_by_label[option] & (self._mask >> self._shift)
        new = (old & (~self._mask)) | ((field_val << self._shift) & self._mask)
        ok = await self._coordinator.write_u32(self._base, new, self._order)
        if ok:
            self._recompute_option_from_u32(new)
            self.async_write_ha_state()
            await self._coordinator.async_request_refresh()

    def _sync_from_sensor(self) -> None:
        v = self._get_u32()
        if v is None:
            return
        self._recompute_option_from_u32(v)

    def _handle_coordinator_update(self) -> None:
        self._sync_from_sensor()
        self.async_write_ha_state()
