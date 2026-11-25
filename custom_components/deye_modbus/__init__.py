"""The Deye Modbus integration."""
from __future__ import annotations
import logging

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, CONF_HOST, CONF_PORT
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN, CONF_UNIT_ID, CONF_SCAN_INTERVAL, CONF_MODEL, DEFAULT_SCAN_SECONDS,
    DEFAULT_PORT, DEFAULT_UNIT_ID,
    CONF_TRANSPORT, DEFAULT_TRANSPORT, CONF_BAUDRATE, DEFAULT_BAUDRATE,
    CONF_BYTESIZE, DEFAULT_BYTESIZE, CONF_PARITY, DEFAULT_PARITY,
    CONF_STOPBITS, DEFAULT_STOPBITS, CONF_ADDR_OFFSET, DEFAULT_ADDR_OFFSET,
    MIN_SCAN_SECONDS, MAX_SCAN_SECONDS, CONF_WRITE_MODE, DEFAULT_WRITE_MODE,
)
from .coordinator import DeyeModbusCoordinator, RegisterDef
from .mapping import load_register_mapping

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH, Platform.SELECT, Platform.NUMBER]

# This integration is set up only via the UI config flow (no YAML config).
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Fields accepted by RegisterDef - anything else in the YAML is ignored.
_REGISTER_FIELDS = {
    "name", "unique_id", "register_type", "address", "count", "scale", "offset",
    "unit_of_measurement", "device_class", "state_class", "signed", "word_order",
    "options", "mask", "icon", "enabled_default", "precision",
}


def _ensure_uid(base: str | None, prefix: str, address: int) -> str:
    base = (base or "").strip()
    return base if base else f"{prefix}_{address}"


def _auto_inject_readbacks(sensors: list[dict], controls: list[dict]) -> tuple[list[dict], list[dict]]:
    """Give every writable control a hidden holding sensor to read its state back."""
    for s in sensors:
        s["unique_id"] = _ensure_uid(s.get("unique_id"), "s", int(s.get("address", 0)))
    sensor_uids = {s["unique_id"] for s in sensors}
    for c in controls:
        addr = c.get("address", c.get("base_address", 0))
        c["unique_id"] = _ensure_uid(c.get("unique_id"), c.get("type", "c"), int(addr))
        if c.get("type") in ("select32", "number32"):
            rb_uid = c.get("read_unique_id") or f"rb_{c['unique_id']}"
            if rb_uid not in sensor_uids:
                sensors.append({
                    "name": f"RB {c.get('name', c['unique_id'])}",
                    "unique_id": rb_uid, "register_type": "holding",
                    "address": int(c["base_address"]), "count": 2, "scale": 1.0,
                    "enabled_default": False,
                })
                sensor_uids.add(rb_uid)
            c["read_unique_id"] = rb_uid
            continue
        if not addr:
            continue
        if not c.get("read_unique_id"):
            rb_uid = f"rb_{c['unique_id']}"
            if rb_uid not in sensor_uids:
                sensors.append({
                    "name": f"RB {c.get('name', c['unique_id'])}",
                    "unique_id": rb_uid, "register_type": "holding",
                    "address": int(addr), "count": 1, "scale": 1.0,
                    "enabled_default": False,
                })
                sensor_uids.add(rb_uid)
            c["read_unique_id"] = rb_uid
            c.setdefault("read_factor", 1.0)
    return sensors, controls


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    unit_id = entry.data.get(CONF_UNIT_ID, DEFAULT_UNIT_ID)
    model_key = entry.data.get(CONF_MODEL)
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_SECONDS))
    scan_interval = max(MIN_SCAN_SECONDS, min(MAX_SCAN_SECONDS, int(scan_interval)))
    transport = entry.options.get(CONF_TRANSPORT, entry.data.get(CONF_TRANSPORT, DEFAULT_TRANSPORT))
    addr_offset = entry.options.get(CONF_ADDR_OFFSET, DEFAULT_ADDR_OFFSET)
    write_mode = entry.options.get(CONF_WRITE_MODE, DEFAULT_WRITE_MODE)
    serial_params = {
        "baudrate": entry.options.get(CONF_BAUDRATE, DEFAULT_BAUDRATE),
        "bytesize": entry.options.get(CONF_BYTESIZE, DEFAULT_BYTESIZE),
        "parity": entry.options.get(CONF_PARITY, DEFAULT_PARITY),
        "stopbits": entry.options.get(CONF_STOPBITS, DEFAULT_STOPBITS),
    }

    mapping = await hass.async_add_executor_job(load_register_mapping, model_key)
    sensors_cfg = list(mapping.get("sensors", []))
    controls_cfg = list(mapping.get("controls", []))
    sensors_cfg, controls_cfg = _auto_inject_readbacks(sensors_cfg, controls_cfg)
    _LOGGER.info("Deye map path: %s (%s sensors, %s controls)",
                 mapping.get("path"), len(sensors_cfg), len(controls_cfg))

    registers = [
        RegisterDef(**{k: v for k, v in r.items() if k in _REGISTER_FIELDS})
        for r in sensors_cfg
    ]
    coordinator = DeyeModbusCoordinator(
        hass, host, port, unit_id, registers, scan_interval,
        transport=transport, serial_params=serial_params, address_offset=addr_offset,
        write_mode=write_mode,
    )
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator, "registers": registers,
        "controls": controls_cfg, "model": model_key,
    }
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _svc_write_register(call: ServiceCall):
        ok = await coordinator.write_register_fc06(int(call.data["address"]), int(call.data["value"]))
        _LOGGER.info("write_register -> %s", ok)

    async def _svc_write_registers(call: ServiceCall):
        addr = int(call.data["address"])
        values = [int(v) for v in call.data["values"]]
        ok = await coordinator.write_multiple_registers(addr, values)
        _LOGGER.info("write_registers -> %s", ok)

    async def _svc_write_u32(call: ServiceCall):
        addr = int(call.data["address"])
        value = int(call.data["value"])
        word_order = call.data.get("word_order", "high_low")
        ok = await coordinator.write_u32(addr, value, word_order)
        _LOGGER.info("write_u32 -> %s", ok)

    hass.services.async_register(DOMAIN, "write_register", _svc_write_register)
    hass.services.async_register(DOMAIN, "write_registers", _svc_write_registers)
    hass.services.async_register(DOMAIN, "write_u32", _svc_write_u32)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: DeyeModbusCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    await coordinator.async_close()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
