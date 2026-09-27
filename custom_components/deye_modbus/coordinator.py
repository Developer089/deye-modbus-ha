"""DataUpdateCoordinator that polls a Deye hybrid inverter over Modbus.

Deye three-phase hybrid inverters expose *all* live measurements through the
holding-register space (function code 0x03), including the 500+ real-time block.
Unlike some vendors there is no separate "read once" settings space here, so
every mapped register is polled each cycle. Contiguous registers are batched
into single Modbus reads to keep the poll cheap.
"""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, List

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

try:
    from pymodbus.client import AsyncModbusTcpClient, AsyncModbusSerialClient
except Exception as exc:  # pragma: no cover
    _LOGGER.error("pymodbus import failed: %s", exc)
    raise


@dataclass
class RegisterDef:
    """One decoded value read from the inverter."""

    name: str
    unique_id: str
    register_type: str = "holding"          # "holding" (0x03) or "input" (0x04)
    address: int = 0
    count: int = 1                           # 1 = 16-bit, 2 = 32-bit
    scale: float = 1.0
    offset: float = 0.0                      # raw is (value - offset) before scaling
    unit_of_measurement: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    signed: bool = False
    word_order: str = "high_low"             # 32-bit word order: high_low | low_high
    options: dict[int, str] | None = None    # enum mapping for sensors (0 -> "text")
    mask: int | None = None                  # bitmask applied to raw before sign/scale
    icon: str | None = None
    enabled_default: bool = True             # entity_registry_enabled_default
    precision: int | None = None             # suggested_display_precision (decimals)


class DeyeModbusCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls all mapped registers each cycle and decodes them."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        unit_id: int,
        registers: List[RegisterDef],
        scan_interval: int,
        transport: str = "tcp",
        serial_params: dict | None = None,
        address_offset: int = 0,
        write_mode: str = "fc16",
    ) -> None:
        super().__init__(
            hass, _LOGGER, name="deye_modbus coordinator",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._host, self._port, self._unit_id = host, port, int(unit_id)
        self._registers: List[RegisterDef] = registers
        self._transport = (transport or "tcp").lower()
        self._serial_params = serial_params or {}
        self._client = None
        self._lock = asyncio.Lock()
        self._addr_off = int(address_offset or 0)
        self._write_mode = (write_mode or "fc16").lower()

    def _addr(self, addr: int) -> int:
        return int(addr) - self._addr_off if self._addr_off else int(addr)

    # ------------------------------------------------------------------ client
    async def _ensure_client(self):
        if self._client is None:
            if self._transport == "rtutcp":
                url = f"socket://{self._host}:{self._port}"
                params = {
                    "method": "rtu", "port": url,
                    "baudrate": int(self._serial_params.get("baudrate", 9600)),
                    "bytesize": int(self._serial_params.get("bytesize", 8)),
                    "parity": str(self._serial_params.get("parity", "N")),
                    "stopbits": int(self._serial_params.get("stopbits", 1)),
                    "timeout": 5,
                }
                self._client = AsyncModbusSerialClient(**params)
            else:
                try:
                    self._client = AsyncModbusTcpClient(self._host, port=self._port, timeout=5)
                except TypeError:
                    self._client = AsyncModbusTcpClient(self._host, port=self._port)
        if not bool(getattr(self._client, "connected", False)):
            try:
                await self._client.connect()
            except Exception as e:  # noqa: BLE001
                raise UpdateFailed(f"Modbus connect failed: {e}") from e
        return self._client

    # ------------------------------------------------------------------ polling
    async def _async_update_data(self) -> dict[str, Any]:
        async with self._lock:
            try:
                result: dict[str, Any] = {}
                inputs = [r for r in self._registers if r.register_type == "input"]
                holdings = [r for r in self._registers if r.register_type != "input"]
                if holdings:
                    await self._read_grouped(holdings, result, self._read_holding)
                if inputs:
                    await self._read_grouped(inputs, result, self._read_input)
                return result
            except Exception as err:  # noqa: BLE001
                raise UpdateFailed(err) from err

    async def _read_grouped(self, regs: list[RegisterDef], out: dict[str, Any], fn):
        regs = sorted(regs, key=lambda r: r.address)
        GAP = 8          # merge registers within this many words into one read
        MAX_WORDS = 110  # keep each Modbus frame comfortably below the 125 limit
        start = end = None
        acc: list[RegisterDef] = []
        for r in regs:
            if start is None:
                start, end, acc = r.address, r.address + r.count, [r]
                continue
            if r.address <= end + GAP and (r.address + r.count - start) <= MAX_WORDS:
                end = max(end, r.address + r.count)
                acc.append(r)
            else:
                await self._read_window(fn, out, start, end, acc)
                start, end, acc = r.address, r.address + r.count, [r]
        if start is not None:
            await self._read_window(fn, out, start, end, acc)

    async def _read_window(self, fn, out, start, end, regs):
        raw = await fn(self._addr(start), end - start)
        for r in regs:
            out[r.unique_id] = self._decode(r, raw, start)

    def _decode(self, r: RegisterDef, raw, start) -> Any:
        if not raw:
            return None
        off = r.address - start
        chunk = raw[off:off + r.count]
        if len(chunk) < r.count:
            return None
        if r.count == 1:
            v = chunk[0]
            if r.mask is not None:
                v &= r.mask
            if r.signed and v >= 0x8000:
                v -= 0x10000
            return (v - r.offset) * r.scale
        if r.count == 2:
            if r.word_order == "low_high":
                low, high = chunk[0], chunk[1]
            else:
                high, low = chunk[0], chunk[1]
            v = (high << 16) | low
            if r.mask is not None:
                v &= r.mask
            if r.signed and v >= 0x80000000:
                v -= 0x100000000
            return (v - r.offset) * r.scale
        return None

    # ------------------------------------------------------------------ reads
    async def _call_read(self, method_name, address, count):
        client = await self._ensure_client()
        method = getattr(client, method_name)
        a = self._addr(address)
        # pymodbus 3.x -> slave=, pymodbus 2.x -> unit=, older -> positional.
        for kwargs in (
            {"count": count, "slave": self._unit_id},
            {"count": count, "unit": self._unit_id},
            {"count": count},
        ):
            try:
                return await method(a, **kwargs)
            except TypeError:
                continue
        try:
            return await method(a, count)
        except TypeError:
            return None

    async def _read_input(self, address, count):
        rr = await self._call_read("read_input_registers", address, count)
        return None if rr is None or (getattr(rr, "isError", None) and rr.isError()) else getattr(rr, "registers", None)

    async def _read_holding(self, address, count):
        rr = await self._call_read("read_holding_registers", address, count)
        return None if rr is None or (getattr(rr, "isError", None) and rr.isError()) else getattr(rr, "registers", None)

    # ------------------------------------------------------------------ writes
    async def _write_call(self, method_name: str, address: int, payload, what: str) -> bool:
        """Run one Modbus write, serialised with polling on the shared client.

        All requests share a single TCP connection; without the lock a write can
        interleave with a poll, which pymodbus reports as a transaction_id
        mismatch and which can drop the write. Failures are logged instead of
        being swallowed so that a rejected write is visible in the HA log.
        """
        a = self._addr(address)
        ok = False
        async with self._lock:
            client = await self._ensure_client()
            method = getattr(client, method_name)
            # pymodbus 3.x -> slave=, pymodbus 2.x -> unit=, older -> positional.
            for variant in (
                lambda: method(a, payload, slave=self._unit_id),
                lambda: method(a, payload, self._unit_id),
                lambda: method(a, payload, unit=self._unit_id),
                lambda: method(a, payload),
            ):
                try:
                    rr = await variant()
                except TypeError:
                    continue
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Modbus %s to register %s failed: %s", what, address, err)
                    return False
                if getattr(rr, "isError", lambda: False)():
                    _LOGGER.warning("Modbus %s to register %s rejected by inverter: %s", what, address, rr)
                    return False
                ok = True
                break
            else:
                _LOGGER.warning("Modbus %s to register %s: no compatible pymodbus call signature", what, address)
        if ok:
            _LOGGER.debug("Modbus %s to register %s OK: %s", what, address, payload)
            await self.async_request_refresh()
        return ok

    async def write_single_register(self, address: int, value: int) -> bool:
        """Write one 16-bit holding register using the configured function code.

        With write_mode == "fc16" (default) this sends FC16 "Write Multiple
        Registers" with a single value, which Deye inverters accept. With
        "fc06" it sends FC06 "Write Single Register" (legacy behaviour).
        """
        wire = int(value) & 0xFFFF
        if self._write_mode == "fc06":
            return await self.write_register_fc06(address, wire)
        return await self._write_call("write_registers", address, [wire], "FC16 write")

    async def write_register_fc06(self, address: int, value: int) -> bool:
        """Always use FC06, regardless of write_mode (for the raw service)."""
        return await self._write_call("write_register", address, int(value) & 0xFFFF, "FC06 write")

    async def write_multiple_registers(self, address: int, values: list[int]) -> bool:
        vals = [int(v) & 0xFFFF for v in values]
        return await self._write_call("write_registers", address, vals, "FC16 write")

    async def write_u32(self, base_address: int, value: int, word_order: str = "high_low") -> bool:
        v = int(value) & 0xFFFFFFFF
        hi, lo = (v >> 16) & 0xFFFF, v & 0xFFFF
        values = [hi, lo] if word_order == "high_low" else [lo, hi]
        return await self.write_multiple_registers(base_address, values)

    async def write_coil(self, address: int, value: int) -> bool:
        return await self._write_call("write_coil", address, bool(value), "FC05 write")

    async def async_close(self):
        try:
            if self._client:
                await self._client.close()
        except Exception:  # noqa: BLE001
            pass
