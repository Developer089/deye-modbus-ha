"""Config and options flow for the Deye Modbus integration."""
from __future__ import annotations
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    SelectOptionDict,
)

from .const import (
    DOMAIN, CONF_UNIT_ID, CONF_SCAN_INTERVAL, CONF_MODEL,
    DEFAULT_SCAN_SECONDS, DEFAULT_PORT, DEFAULT_UNIT_ID,
    CONF_TRANSPORT, DEFAULT_TRANSPORT,
    CONF_BAUDRATE, DEFAULT_BAUDRATE, CONF_BYTESIZE, DEFAULT_BYTESIZE,
    CONF_PARITY, DEFAULT_PARITY, CONF_STOPBITS, DEFAULT_STOPBITS,
    CONF_ADDR_OFFSET, DEFAULT_ADDR_OFFSET, MIN_SCAN_SECONDS, MAX_SCAN_SECONDS,
    CONF_WRITE_MODE, DEFAULT_WRITE_MODE, WRITE_MODES,
)
from .models import model_options, get_model, DEFAULT_MODEL


def _model_selector() -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=key, label=label)
                for key, label in model_options().items()
            ],
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


def _user_schema() -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_MODEL, default=DEFAULT_MODEL): _model_selector(),
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_UNIT_ID, default=DEFAULT_UNIT_ID): int,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_SECONDS): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_SCAN_SECONDS, max=MAX_SCAN_SECONDS)
        ),
    })


class DeyeModbusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            unique = f"{host}:{user_input.get(CONF_PORT, DEFAULT_PORT)}:{user_input.get(CONF_UNIT_ID, DEFAULT_UNIT_ID)}"
            await self.async_set_unique_id(unique)
            self._abort_if_unique_id_configured()
            model = get_model(user_input.get(CONF_MODEL))
            title = f"Deye {model['model']} @ {host}"
            return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=_user_schema(), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return DeyeModbusOptionsFlow(config_entry)


class DeyeModbusOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    def _entry_default(self, key: str, fallback: Any) -> Any:
        options = self.config_entry.options or {}
        data = self.config_entry.data or {}
        return options.get(key, data.get(key, fallback))

    def _entry_int_default(self, key: str, fallback: int) -> int:
        value = self._entry_default(key, fallback)
        if value is None or str(value).strip() == "":
            return fallback
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    def _options_schema(self) -> vol.Schema:
        transport = str(self._entry_default(CONF_TRANSPORT, DEFAULT_TRANSPORT)).lower()
        if transport not in {"tcp", "rtutcp"}:
            transport = DEFAULT_TRANSPORT
        parity = str(self._entry_default(CONF_PARITY, DEFAULT_PARITY)).upper()
        if parity not in {"N", "E", "O"}:
            parity = DEFAULT_PARITY
        write_mode = str(self._entry_default(CONF_WRITE_MODE, DEFAULT_WRITE_MODE)).lower()
        if write_mode not in WRITE_MODES:
            write_mode = DEFAULT_WRITE_MODE
        return vol.Schema({
            vol.Optional(CONF_SCAN_INTERVAL, default=self._entry_int_default(CONF_SCAN_INTERVAL, DEFAULT_SCAN_SECONDS)): vol.All(
                vol.Coerce(int), vol.Range(min=MIN_SCAN_SECONDS, max=MAX_SCAN_SECONDS)
            ),
            vol.Optional(CONF_TRANSPORT, default=transport): vol.In(["tcp", "rtutcp"]),
            vol.Optional(CONF_WRITE_MODE, default=write_mode): vol.In(WRITE_MODES),
            vol.Optional(CONF_ADDR_OFFSET, default=self._entry_int_default(CONF_ADDR_OFFSET, DEFAULT_ADDR_OFFSET)): vol.Coerce(int),
            vol.Optional(CONF_BAUDRATE, default=self._entry_int_default(CONF_BAUDRATE, DEFAULT_BAUDRATE)): vol.Coerce(int),
            vol.Optional(CONF_BYTESIZE, default=self._entry_int_default(CONF_BYTESIZE, DEFAULT_BYTESIZE)): vol.Coerce(int),
            vol.Optional(CONF_PARITY, default=parity): vol.In(["N", "E", "O"]),
            vol.Optional(CONF_STOPBITS, default=self._entry_int_default(CONF_STOPBITS, DEFAULT_STOPBITS)): vol.Coerce(int),
        })

    async def async_step_init(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                user_input = self._options_schema()(user_input)
            except vol.Invalid:
                errors["base"] = "invalid_input"
            else:
                return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init", data_schema=self._options_schema(), errors=errors
        )
