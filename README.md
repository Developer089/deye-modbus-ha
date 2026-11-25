# Deye Modbus for Home Assistant

Local Home Assistant integration for **Deye three-phase hybrid inverters** (SUN-\*K-SG04LP3 / SG05LP3 family, including the SUN-12K-SG05LP3). It talks Modbus TCP directly to the inverter's WiFi/LAN logger or an RS485-to-TCP gateway — no cloud, no SolarMAN account required.

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Integration](https://img.shields.io/badge/type-local__polling-brightgreen.svg)](#)

> This is a **custom repository** for HACS. It is not (yet) in the default HACS store, so you add it manually — see [Installation](#installation).

---

## Features

- 100% **local polling** over Modbus TCP — nothing leaves your network.
- Talks directly to the Deye WiFi/LAN logger (default port **8899**) or to an RS485↔TCP gateway (Elfin EW11, USR, etc. — usually port **502**).
- Guided **config flow**: pick your inverter model from a dropdown, then enter host, port, Modbus unit ID and scan interval. No YAML editing needed.
- Rich set of **sensors**: PV strings, battery, grid import/export, per-phase grid/inverter/load, temperatures, plus daily and lifetime energy counters (ready for the HA Energy dashboard).
- Writable **controls** as native HA entities: work mode, solar sell, grid/generator charge, battery current & SOC limits, max sell power, and full 6-slot time-of-use programming.
- Driven by a human-readable **YAML register map** — adding a model is just a new map file, no code changes.
- **Options flow** to tune scan interval, transport, address offset and serial parameters at any time.
- Low-level **services** for advanced/manual register access.
- `pymodbus` is pulled in automatically by Home Assistant via the manifest.

---

## Installation

### Via HACS (recommended)

1. In Home Assistant go to **HACS**.
2. Open the top-right menu **⋮ → Custom repositories**.
3. Add the repository URL `https://github.com/Developer089/deye-modbus-ha` and choose category **Integration**.
4. Find **Deye Modbus (Hybrid Inverter)** in the list and click **Download / Install**.
5. **Restart Home Assistant.**
6. Go to **Settings ▸ Devices & Services ▸ Add Integration** and search for **Deye Modbus**.

### Manual installation

1. Copy the folder `custom_components/deye_modbus` from this repository into your Home Assistant `/config/custom_components/` directory, so you end up with `/config/custom_components/deye_modbus/`.
2. **Restart Home Assistant.**
3. Add the integration via **Settings ▸ Devices & Services ▸ Add Integration → "Deye Modbus"**.

---

## Configuration

When you add the integration you get a single setup form:

| Field | Default | Notes |
|-------|---------|-------|
| **Inverter model** | SUN-12K-SG05LP3 | Dropdown; selects the bundled register map. |
| **Host / IP** | — | IP address of the inverter's logger or your RS485↔TCP gateway, e.g. `192.168.x.x`. |
| **Port** | `8899` | See the port guidance below. |
| **Modbus unit ID** | `1` | The Modbus slave/device address. Leave at `1` unless you changed it on a gateway. |
| **Scan interval (s)** | `15` | How often to poll, range **5–300** seconds. |

### Which port?

- **`8899`** — the Deye/SolarMAN **WiFi or LAN stick logger** exposes Modbus TCP on this port. This is the right choice for most people plugging into the inverter's own logger.
- **`502`** — a third-party **RS485-to-TCP gateway** (Elfin EW11, USR, etc.) wired to the inverter's Modbus/BMS RS485 port. These typically speak standard Modbus TCP on 502.

If you are unsure, try `8899` first; if no data appears, try `502`.

### Finding the inverter's IP

Look in your router's DHCP client list for the logger (often named `Solarman*` / `IGEN*` / the stick's serial), or use the SolarMAN app / a network scanner. Assigning it a static DHCP lease is recommended so the address never changes.

---

## Supported models

All of these share the three-phase low-voltage hybrid protocol (device type `0x0500`) and the same register map:

| Model key | Label |
|-----------|-------|
| `sun12k_sg05lp3` | Deye SUN-12K-SG05LP3 (3-phase hybrid) — default |
| `sun_sg05lp3_3ph` | Deye SUN-5/6/8/10/12K-SG05LP3 (3-phase hybrid) |
| `sun_sg04lp3_3ph` | Deye SUN-5/6/8/10/12K-SG04LP3 (3-phase hybrid) |

Rebadged units on the same platform (Sunsynk, etc.) will generally work too.

---

## Entities overview

### Sensors (selection)

| Area | Entities |
|------|----------|
| Status | Run State |
| PV | PV1/PV2 voltage, current, power (PV3/PV4 available, disabled by default) |
| Battery | SOC, voltage, current, power, temperature (corrected capacity disabled by default) |
| Grid / CT | Grid voltage L1–L3, frequency, per-phase power, total grid power (import +, export −) |
| Inverter | Inverter total power (per-phase voltages, frequency disabled by default) |
| Load | Total load power (per-phase load power/voltage disabled by default) |
| Backup / Generator | Backup power, generator voltage/power (disabled by default) |
| Temperatures | AC (radiator) temperature, DC temperature |
| Energy — today | PV production, battery charge/discharge, grid import/export, load consumption |
| Energy — total | PV production, battery charge/discharge, grid import/export, load consumption (32-bit lifetime counters) |

### Controls

| Type | Entities |
|------|----------|
| Switch | Inverter On/Off, Solar Sell (Export), Grid Charge Enable, Generator Charge Enable |
| Select | Work Mode (Selling First / Zero Export To Load / Zero Export To CT) |
| Number | Max Sell Power, Max Grid Output Power, Active Power Regulation, Battery Max Charge/Discharge Current, Grid Charge Current, Battery Shutdown/Restart/Low SOC, Battery Capacity, Grid Voltage High/Low limits |
| Time-of-use | 6 slots × Time / Power / SOC / Charge Source (Off / Grid / Generator / Grid+Generator) |

> **Disabled-by-default entities.** To keep the device tidy, some entities ship disabled: PV3/PV4, per-phase inverter/load detail, generator/backup, grid protection limits, and all time-of-use slots. Enable any of them under the device's **Entities** list → open the entity → **Settings** → toggle *Enabled*.

---

## Options

Open the integration and click **Configure** to change (no restart needed — the entry reloads):

- **Scan interval** — 5–300 s.
- **Transport** — `tcp` (standard Modbus TCP) or `rtutcp` (RTU frame over a raw TCP socket, for some gateways).
- **Address offset** — added to every register address, for gateways that shift the map.
- **Serial parameters** — baud rate, byte size, parity (N/E/O), stop bits (only relevant for `rtutcp`).
- **Write mode** — Modbus function code used when a control entity (number / select / switch) writes a single register:
  - `fc16` *(default)* — "Write Multiple Registers" with one value. This is what Deye's protocol document specifies, and many Deye inverters/loggers **silently ignore FC06**, which shows up as `No response received after 3 retries` and a value that never changes.
  - `fc06` — "Write Single Register" (behaviour of v0.1.1 and earlier). Only use this if your gateway does not support FC16.

---

## Services

For advanced/manual register access. Addresses are decimal and given **before** any configured address offset.

| Service | Purpose | Key fields |
|---------|---------|-----------|
| `deye_modbus.write_register` | Write one holding register (FC06). | `address`, `value` (0–65535) |
| `deye_modbus.write_registers` | Write a list of 16-bit values starting at an address (FC16). | `address`, `values` (list) |
| `deye_modbus.write_u32` | Write a 32-bit value across two registers (FC16). | `address`, `value` (0–4294967295), `word_order` (`high_low` default / `low_high`) |

Example:

```yaml
service: deye_modbus.write_register
data:
  address: 145      # Solar Sell enable
  value: 1
```

---

## How it works / Modbus notes

- **Everything is read with function code `0x03` (FC03, read holding registers)** — on this inverter both the live measurements and the writable settings live in the holding-register space. Writes use FC06 (single) / FC16 (multiple).
- **32-bit energy counters are stored low-word-first** (`word_order: low_high`, little-endian by word). The integration reassembles them automatically.
- **Temperatures** use a raw offset of 1000 and scale 0.1, i.e. `°C = (raw − 1000) × 0.1`.
- **Signed values** (battery/grid/load power and current) are decoded as two's-complement. Grid total power is **positive when importing, negative when exporting**.
- The register map is a plain YAML file (`custom_components/deye_modbus/maps/sun_3ph_hybrid.yaml`) derived from Deye's official three-phase energy-storage Modbus protocol document.

---

## Troubleshooting

- **No data / entities unavailable.** Double-check the **port** (`8899` for the Deye logger vs `502` for an RS485↔TCP gateway) and the **Modbus unit ID** (usually `1`). Confirm the IP is reachable from the HA host (`ping`), and that the logger firmware is recent.
- **Only one Modbus master at a time.** The inverter/logger accepts a **single** Modbus connection. This integration will **conflict with the SolarMAN cloud logging and with any other Modbus poller** (a second HA integration, Node-RED, etc.). If the SolarMAN app / cloud is actively connected you may see dropouts — expect to give up cloud logging, or poll via a separate RS485 gateway.
- **Setting a control has no effect / write timeouts.** Make sure *Write mode* in Options is `fc16`. Failed writes are logged by `custom_components.deye_modbus.coordinator` and shown as an error in the UI.
- **Intermittent timeouts.** Increase the **scan interval**, reduce competing connections, or improve WiFi signal to the logger. For flaky gateways try the `rtutcp` transport in Options.
- **Wrong or shifted values.** If a gateway shifts the address map, set an **address offset** in Options.

---

## Disclaimer

The control and service entities **write settings to your inverter**. Changing work mode, sell/charge behaviour, SOC and current limits, grid protection windows or time-of-use programming **can affect how your inverter operates**, including battery health and grid interaction. Use these features at your own risk; the authors accept no liability for any damage or loss. If in doubt, leave the write entities disabled and use the integration for monitoring only. This project is not affiliated with or endorsed by Deye.

---

## Credits

- Architecture modeled on a **Growatt Modbus** Home Assistant integration (coordinator + YAML-driven register map pattern).
- Register map derived from **Deye's official Modbus protocol documentation** for the three-phase energy-storage (hybrid) inverters.
- Built on [`pymodbus`](https://github.com/pymodbus-dev/pymodbus).

---

## Česky

**Deye Modbus pro Home Assistant** je lokální integrace pro **třífázové hybridní měniče Deye** (řada SUN-\*K-SG04LP3 / SG05LP3, včetně SUN-12K-SG05LP3). Komunikuje přímo přes Modbus TCP s WiFi/LAN loggerem měniče (výchozí port **8899**) nebo s převodníkem RS485↔TCP (obvykle port **502**). Nepotřebuje cloud ani účet SolarMAN — vše probíhá ve vaší síti.

### Instalace přes HACS

1. V **HACS** otevřete menu **⋮ → Custom repositories** (Vlastní repozitáře).
2. Přidejte adresu `https://github.com/Developer089/deye-modbus-ha` a jako kategorii zvolte **Integration**.
3. Najděte **Deye Modbus (Hybrid Inverter)** a nainstalujte.
4. **Restartujte Home Assistant.**
5. Přejděte do **Nastavení ▸ Zařízení a služby ▸ Přidat integraci** a vyhledejte **Deye Modbus**.

Ruční instalace: zkopírujte složku `custom_components/deye_modbus` do `/config/custom_components/` a Home Assistant restartujte.

### Konfigurace

Ve formuláři při přidání integrace zadáte:

- **Model měniče** — výběr z rozbalovacího seznamu (určuje mapu registrů).
- **Host / IP** — adresa loggeru nebo převodníku, např. `192.168.x.x`.
- **Port** — `8899` pro WiFi/LAN logger Deye, `502` pro převodník RS485↔TCP. Pokud si nejste jistí, zkuste nejdřív `8899`.
- **Modbus unit ID** — obvykle `1`.
- **Interval dotazování** — `15` s (rozsah 5–300 s).

IP adresu měniče najdete v seznamu klientů DHCP na routeru; doporučuje se nastavit statickou (rezervovanou) adresu.

### Entity a ovládání

Integrace zpřístupní senzory (FV panely, baterie, síť import/export, zátěž, teploty, denní i celková energie) a ovládací prvky (režim práce, prodej do sítě, nabíjení ze sítě/generátoru, limity proudu a SOC baterie, maximální prodejní výkon, programování podle času – TOU). Některé entity jsou ve výchozím stavu **vypnuté** (PV3/PV4, detaily po fázích, TOU sloty) — zapnete je v nastavení dané entity.

### Upozornění

Zápisové entity a služby **mění nastavení měniče** a mohou ovlivnit jeho chování i baterii. Používejte na vlastní riziko. Pozor: měnič/logger přijímá **jen jedno Modbus spojení** — integrace se proto vylučuje se SolarMAN cloudem i s jiným Modbus dotazovačem.
