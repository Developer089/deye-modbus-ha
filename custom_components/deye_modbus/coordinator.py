"""DataUpdateCoordinator that polls a Deye hybrid inverter over Modbus.

Deye three-phase hybrid inverters expose *all* live measurements through the
holding-register space (function code 0x03), including the 500+ real-time block.
Unlike some vendors there is no separate "read once" settings space here, so
every mapped register is polled each cycle. Contiguous registers are batched
into single Modbus reads to keep the poll cheap.
"""
from __future__ import annotations
import asyncio
import inspect
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, List

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

try:
    from pymodbus.client import AsyncModbusTcpClient, AsyncModbusSerialClient
except Exception as exc:  # pragma: no cover
    _LOGGER.error("pymodbus import failed: %s", exc)
    raise

try:  # pymodbus >= 3.7
    from pymodbus import FramerType
    _RTU_FRAMER = FramerType.RTU
except Exception:  # noqa: BLE001
    try:  # pymodbus 3.5 / 3.6
        from pymodbus import Framer
        _RTU_FRAMER = Framer.RTU
    except Exception:  # noqa: BLE001 - fall back to the serial client
        _RTU_FRAMER = None

# Name of the "which Modbus unit" keyword, newest pymodbus first.
_UNIT_KWARGS = ("device_id", "slave", "unit")


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
    entity_category: str | None = None       # "diagnostic" for informational sensors


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
        self._unit_kw: str | None = None

    def _addr(self, addr: int) -> int:
        return int(addr) - self._addr_off if self._addr_off else int(addr)

    # ------------------------------------------------------------------ client
    def _build_client(self):
        if self._transport != "rtutcp":
            try:
                return AsyncModbusTcpClient(self._host, port=self._port, timeout=5)
            except TypeError:
                return AsyncModbusTcpClient(self._host, port=self._port)
        # RTU frames tunnelled through a raw TCP socket (transparent RS485
        # gateways such as the Elfin EW11). A TCP client with the RTU framer is
        # the supported way to do this; the serial line settings live in the
        # gateway, not here.
        if _RTU_FRAMER is not None:
            try:
                return AsyncModbusTcpClient(self._host, port=self._port, framer=_RTU_FRAMER, timeout=5)
            except TypeError:
                pass
        return AsyncModbusSerialClient(
            port=f"socket://{self._host}:{self._port}",
            baudrate=int(self._serial_params.get("baudrate", 9600)),
            bytesize=int(self._serial_params.get("bytesize", 8)),
            parity=str(self._serial_params.get("parity", "N")),
            stopbits=int(self._serial_params.get("stopbits", 1)),
            timeout=5,
        )

    async def _ensure_client(self):
        if self._client is None:
            self._client = self._build_client()
        if not bool(getattr(self._client, "connected", False)):
            try:
                await self._client.connect()
            except Exception as e:  # noqa: BLE001
                raise UpdateFailed(f"Modbus connect failed: {e}") from e
        return self._client

    def _unit_kwargs(self, method) -> dict[str, int]:
        """Return the unit-id keyword this pymodbus version expects.

        pymodbus renamed it twice (unit -> slave -> device_id). Guessing by
        catching TypeError silently fell back to pymodbus' default unit, so the
        signature is inspected instead.
        """
        if self._unit_kw is None:
            try:
                params = inspect.signature(method).parameters
            except (TypeError, ValueError):
                params = {}
            self._unit_kw = next((k for k in _UNIT_KWARGS if k in params), "")
            if not self._unit_kw:
                _LOGGER.warning("pymodbus exposes no unit-id argument; using its default unit")
        return {self._unit_kw: self._unit_id} if self._unit_kw else {}

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
        # The address offset is applied once, in _call_read.
        raw = await fn(start, end - start)
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
        return await method(self._addr(address), count=count, **self._unit_kwargs(method))

    async def _read_input(self, address, count):
        rr = await self._call_read("read_input_registers", address, count)
        return None if rr is None or (getattr(rr, "isError", None) and rr.isError()) else getattr(rr, "registers", None)

    async def _read_holding(self, address, count):
        rr = await self._call_read("read_holding_registers", address, count)
        return None if rr is None or (getattr(rr, "isError", None) and rr.isError()) else getattr(rr, "registers", None)

    # ------------------------------------------------------------------ writes
    async def _write_locked(self, method_name: str, address: int, payload, what: str) -> bool:
        """Run one Modbus write. The caller must hold ``self._lock``.

        All requests share a single connection; without the lock a write can
        interleave with a poll, which pymodbus reports as a transaction_id
        mismatch and which can drop the write. Failures are logged instead of
        being swallowed so that a rejected write is visible in the HA log.
        """
        try:
            client = await self._ensure_client()
            method = getattr(client, method_name)
            rr = await method(self._addr(address), payload, **self._unit_kwargs(method))
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Modbus %s to register %s failed: %s", what, address, err)
            return False
        if rr is None or getattr(rr, "isError", lambda: False)():
            _LOGGER.warning("Modbus %s to register %s rejected by inverter: %s", what, address, rr)
            return False
        _LOGGER.debug("Modbus %s to register %s OK: %s", what, address, payload)
        return True

    async def _write_call(self, method_name: str, address: int, payload, what: str) -> bool:
        async with self._lock:
            ok = await self._write_locked(method_name, address, payload, what)
        if ok:
            await self.async_request_refresh()
        return ok

    async def _write_single_locked(self, address: int, value: int) -> bool:
        wire = int(value) & 0xFFFF
        if self._write_mode == "fc06":
            return await self._write_locked("write_register", address, wire, "FC06 write")
        return await self._write_locked("write_registers", address, [wire], "FC16 write")

    async def write_single_register(self, address: int, value: int) -> bool:
        """Write one 16-bit holding register using the configured function code.

        With write_mode == "fc16" (default) this sends FC16 "Write Multiple
        Registers" with a single value, which Deye inverters accept. With
        "fc06" it sends FC06 "Write Single Register" (legacy behaviour).
        """
        async with self._lock:
            ok = await self._write_single_locked(address, value)
        if ok:
            await self.async_request_refresh()
        return ok

    async def write_register_bits(self, address: int, mask: int, value: int) -> bool:
        """Change only the ``mask`` bits of a packed register (read-modify-write).

        Used for bitfield registers such as 146 (time-of-use enable + weekdays).
        The register is re-read under the lock right before writing so that the
        other bits come from the inverter, not from a stale poll.
        """
        mask = int(mask) & 0xFFFF
        async with self._lock:
            try:
                current = await self._read_holding(address, 1)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Reading register %s before a bit write failed: %s", address, err)
                return False
            if not current:
                _LOGGER.warning("Reading register %s before a bit write returned no data", address)
                return False
            new = (int(current[0]) & ~mask & 0xFFFF) | (int(value) & mask)
            ok = True if new == int(current[0]) else await self._write_single_locked(address, new)
        if ok:
            await self.async_request_refresh()
        return ok

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
                # close() is a coroutine in old pymodbus and a plain call in new.
                result = self._client.close()
                if inspect.isawaitable(result):
                    await result
        except Exception:  # noqa: BLE001
            pass
