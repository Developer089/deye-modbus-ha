"""Constants for the Deye Modbus integration."""
from __future__ import annotations
from typing import Final

DOMAIN: Final = "deye_modbus"

# --- Config / options keys ---
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"
CONF_UNIT_ID: Final = "unit_id"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_MODEL: Final = "model"
CONF_TRANSPORT: Final = "transport"
CONF_BAUDRATE: Final = "baudrate"
CONF_BYTESIZE: Final = "bytesize"
CONF_PARITY: Final = "parity"
CONF_STOPBITS: Final = "stopbits"
CONF_ADDR_OFFSET: Final = "address_offset"
CONF_WRITE_MODE: Final = "write_mode"

# --- Defaults ---
# Deye WiFi/LAN loggers expose Modbus TCP on 8899 by default. Third-party
# RS485<->TCP gateways (Elfin EW11, USR, ...) usually use 502.
DEFAULT_PORT: Final = 8899
DEFAULT_UNIT_ID: Final = 1
DEFAULT_SCAN_SECONDS: Final = 15
MIN_SCAN_SECONDS: Final = 5
MAX_SCAN_SECONDS: Final = 300
DEFAULT_TRANSPORT: Final = "tcp"
DEFAULT_ADDR_OFFSET: Final = 0

# Function code used when a control entity writes a single 16-bit register.
# Deye hybrid inverters (per the official Deye Modbus protocol document) only
# accept writes via FC16 "Write Multiple Registers"; FC06 requests are silently
# dropped by many firmware/logger combinations, which surfaces as a timeout.
# FC16 with a single register is a valid Modbus request, so it is the default.
WRITE_MODE_FC16: Final = "fc16"
WRITE_MODE_FC06: Final = "fc06"
WRITE_MODES: Final = [WRITE_MODE_FC16, WRITE_MODE_FC06]
DEFAULT_WRITE_MODE: Final = WRITE_MODE_FC16

# Serial params (only used when transport == "rtutcp")
DEFAULT_BAUDRATE: Final = 9600
DEFAULT_BYTESIZE: Final = 8
DEFAULT_PARITY: Final = "N"
DEFAULT_STOPBITS: Final = 1
