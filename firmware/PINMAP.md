# Pin map — Freenove ESP32-WROVER, Phase 0 data logger

Proposed 2026-11-05; flow sensor on GPIO35 and the pump driver added 2026-11-21. The code reads every pin from one header,
`firmware/libraries/PvDaq/src/pv_config.h`; change a pin there and in this table together.

Board: Freenove ESP32-WROVER (ESP32-D0WD-V3 rev 3.1, 4 MB flash, 4 MB PSRAM), camera removed,
on the Freenove GPIO extension board. Facts about the GPIOs are from the ESP-IDF GPIO guide
(docs.espressif.com, "GPIO & RTC GPIO — ESP32"):
- strapping pins: GPIO0, 2, 5, 12, 15 — their level at reset selects boot mode / flash voltage;
- GPIO6–11 and 16–17 belong to the SPI flash and the PSRAM on WROVER — never use;
- GPIO34–39 are input-only and have no internal pull-up or pull-down;
- ADC2 pins cannot be used while Wi-Fi is on → analog inputs on ADC1 (GPIO32–39) only;
- GPIO1/3 are UART0, the USB-serial link (CH340) for flashing and Serial.

## Assignments

| Function | GPIO | Direction | Why this pin |
|---|---|---|---|
| I2C SDA | **21** | I/O | Arduino-ESP32 default `Wire` pins; not strapping; any GPIO works, but defaults make examples and tests match. |
| I2C SCL | **22** | O (open drain) | As above. |
| 1-Wire DQ (DS18B20 ×5 + water/tank) | **4** | I/O | Plain bidirectional GPIO, not strapping, not used by anything else. Needs an external 4.7 kΩ pull-up to 3.3 V. |
| SD SCK | **18** | O | Default pins of the VSPI controller (`SPI` object): hardware SPI without pin remapping. |
| SD MISO | **19** | I | VSPI default. |
| SD MOSI | **23** | O | VSPI default. |
| SD CS | **13** | O | The VSPI default CS is GPIO5, a strapping pin → moved to 13 (not strapping, free on this board). |
| RS485 TX → MAX3485 (DI / "TXD") | **25** | O | UART2 is remapped here because its default pins (16/17) are the PSRAM pins. Not strapping. |
| RS485 RX ← MAX3485 (RO / "RXD") | **26** | I | As above. |
| PYR20 0–2 V analog | **34** | I (ADC1_CH6) | ADC1 (works with Wi-Fi); input-only pin is fine for an analog input and cannot be driven by mistake. |
| Pump MOSFET gate (PWM) | **32** | O (LEDC channel 2) | Not strapping; high-impedance during reset, so the gate pull-down (10 kΩ) keeps the pump OFF until the firmware drives it LOW. Driven only with `ENABLE_PUMP 1` (`pv_pump.h`: soft start, cut-off from the pump INA226); held LOW otherwise. |
| Flow sensor pulse output | **35** | I (PCNT) | Added 2026-11-21. Input-only pin, so it cannot be driven by mistake; the pulse counter (PCNT) accepts any GPIO. **No internal pull-up or pull-down**: an open-collector output needs an external pull-up to 3.3 V (≈ 10 kΩ); a sensor that drives 5 V needs a divider to ≤ 3.3 V. Sensor: **YF-S401** (2026-11-21). Vendor sheet (DFRobot SEN0216): 5–12 V supply, high level > 4.5 V and low < 0.5 V at 5 V, duty 50 ± 10 %, 0.3–6 L/min, "1 L = 5880 pulses" (≈ 590 Hz at 6 L/min, far below the counter's limits). The sheet does **not** say whether the output is open-collector or driven, and gives no internal pull-up value → measure before wiring (BRINGUP §11). `FLOW_PULSES_PER_L` stays 0 until our own bucket calibration. Counted only with `HAS_FLOW 1`; with nothing wired the pin floats and would count noise. |
| ~~Measurement-load branch B gate driver~~ — **not fitted: the PWM load was dropped 2026-11-21**; pin held LOW, reserved | **14** | O (LEDC) | Decision 2026-11-12 (Asim). The only free output pin. It is a JTAG pin (MTMS) and toggles during boot, so the driver input has a **10 kΩ pull-down**; a toggle would at most connect one 1.5 Ω branch for a few ms `[EST]`. Checked on the scope in bench step B1. |
| **LAMP_RELAY** — reserved 2026-11-21 (was measurement-load branch A, dropped) | **33** | O | Switches the lamp (80–100 W) ON in the solar-noon window only, about ± 2 h, and only in strong sun with the battery above its cut-off (decision 2026-11-21; rules and energy balance in BRINGUP §13). Switch rated ≥ 10 A DC. HIGH = lamp on. Not strapping; floats during reset, so the switch input needs a **10 kΩ pull-down** and must be **active-HIGH**: many relay modules are active-LOW and would switch the lamp ON at every reset and while the pin is held LOW. A 5 V relay coil must not be driven from the pin itself (module with its own transistor, or a logic-level MOSFET module, which also suits a 5–7 A DC lamp better than small relay contacts `[REC]`). Every sketch holds the pin LOW at boot; **no code drives it yet**. The never-flashed `HAS_LOAD` variant still uses this pin as `LOAD_A_PWM`: one more reason not to flash it. |
| test_marker button (to GND) | **27** | I, internal pull-up | Needs an internal pull-up (34–39 have none); not strapping. Press = LOW. |

Spare pins (kept free on purpose):

| GPIO | Note |
|---|---|
| 36 (VP), 39 (VN) | ADC1, input-only: anemometer (pulse or analog, if approved) or a second analog channel. Need external pull-ups for a reed/pulse output. GPIO35 went to the flow sensor (2026-11-21), so two input-only pins remain and **no free output pin**. |
| 0, 2, 5, 12, 15 | Strapping — avoided. GPIO12 HIGH at reset selects 1.8 V flash and stops the board from booting. |

## I2C bus

```
ESP32 (21/22) ── [level shifter if the M5 side idles near 5 V — measure first] ── PaHub V2.1 (0x70)
   │                                                                              ├─ port "0" = ch0: INA226-10A, panel (0x41)
   │                                                                              ├─ port "1" = ch1: INA226-10A, pump  (0x41), read only with ENABLE_PUMP 1
   │                                                                              ├─ port "2" = ch2: INA226-10A, battery (0x41) — RESERVED, unit not bought, not read yet
   │                                                                              └─ port "3" = ch3: INA226-10A, reference cell (0x41) — RESERVED, Stage 2
   ├─ DS3231 RTC (0x68) + its AT24C32 EEPROM (0x57)
   └─ BME280 (0x76 or 0x77, set by SDO on the breakout)
```

- The two INA226-10A units both have the fixed address 0x41 (M5Stack docs), so each sits on its own PaHub channel; the firmware selects a channel before talking to that unit and deselects afterwards. PaHub channel selection follows the PCA9548A command byte (TI/NXP datasheet: bit *n* = 1 enables channel *n*).
- **PaHub channel plan (2026-11-21).** `pv_config.h`: `PAHUB_CH_PANEL` 0, `PAHUB_CH_PUMP` 1, `PAHUB_CH_BATTERY` 2, `PAHUB_CH_REFCELL` 3; ports "4" and "5" free. Every M5Stack INA226-10A has the same fixed address **0x41**, so a third and a fourth unit need nothing but their own port; the PaHub itself stays at 0x70. No GPIO is used.

  | Port / channel | Unit | Measures | Status |
  |---|---|---|---|
  | 0 | INA226-10A #1 | module → charge controller (V, I, P of the module) | fitted, read in every row |
  | 1 | INA226-10A #2 | pump supply line (protection, pump power) | owned; read with `ENABLE_PUMP 1` |
  | 2 | INA226-10A #3 | battery: voltage at the terminals, charge / discharge current (the unit reads both signs) → bulk / float for γ Method C (`docs/paper/02` §9) | **to buy**; channel reserved, no code yet |
  | 3 | INA226-10A #4 | reference cell (Stage 2) | later |

  Battery unit, to check before wiring `[OPEN]`: the unit's range is ± 10 A and 0–30 V. Put the shunt in the battery lead itself, between the battery terminal and the point where controller and lamp meet: it then reads the battery's NET current (charge minus lamp), positive when charging. Lamp 80–100 W (decision 2026-11-21) = 6.7–8.3 A at 12 V `[EST]`: the worst case is the lamp on with no sun, −8.3 A, inside ± 10 A; in the window the net current is near zero, and charging adds at most ≈ 7 A `[EST]`. Fuse at the battery terminal.
- **Port label = chip channel.** Bench check 2026-11-05, moving one unit between ports and then testing the second unit alone: the PaHub port printed "*n*" is PCA9548A channel *n* ("0" → 0, "1" → 1). Both INA226-10A units answer. The scan in `tests/ina226` covers all 8 chip channels; M5 brings out ports 0–5.
- RTC and BME280 are on the main (upstream) bus, always reachable; none of 0x57, 0x68, 0x76/0x77 collide with 0x41 or 0x70.
- 100 kHz bus clock (long leads, several modules with their own pull-ups).
- **Logic level (open):** M5Stack units and the PaHub run from 5 V on the HY2.0 port; the docs do not state the I2C pull-up voltage. Before connecting to GPIO21/22, power the PaHub alone and measure idle SDA and SCL to GND. Near 5 V → use a bidirectional 3.3 V ↔ 5 V I2C level shifter (likely the supervisor's "I2C converter"). Near 3.3 V → direct connection is acceptable.

## Power and grounds

- ESP32 board from USB (bench) or from the 5 V buck (field). M5 units, PaHub: 5 V. DS3231, BME280, DS18B20, MAX3485: 3.3 V from the ESP32 board. microSD module: see BRINGUP.md (depends on whether it has a regulator).
- PYR20: 3.9–30 V DC (manufacturer manual) → from the 12 V adapter (field) or 5 V (bench). Its ground must be common with the ESP32 ground for the 0–2 V input.
- All grounds meet at one point (see the load-grounding note in PROJECT_STATE §4).

## PWM outputs

LEDC, 20 kHz, 11-bit resolution since 2026-11-13 (20 kHz is above hearing; 20 kHz × 2¹¹ = 40.96 MHz is within the 80 MHz APB clock, 12 bits would not be; `pv_config.h` checks this at compile time). All three outputs (pump 32, load A 33, load B 14) are driven LOW (duty 0) at boot by every sketch in this folder.

| Output | GPIO | LEDC channel (timer) | Driven when |
|---|---|---|---|
| Load branch A | 33 | 0 (timer 0) | never: load dropped 2026-11-21, `HAS_LOAD` stays 0. The pin is now `LAMP_RELAY` (plain on/off, no PWM) |
| Load branch B | 14 | 1 (timer 0) | never, as above |
| Pump | 32 | 2 (timer 1) | `ENABLE_PUMP 1`, and only after a typed `PUMP` command |

As flashed today (`HAS_LOAD 0`, `ENABLE_PUMP 0`, `HAS_FLOW 0`) none of them is ever turned on. The loop watchdog (15 s, every build) resets the chip if the loop stalls; a reset releases the pins and the gate pull-downs switch everything off.

## Internal flash (no pins)

| Part of the 4 MB flash | Used for |
|---|---|
| `spiffs` partition (0x160000 B = 1408 kB, core default partition table) | LittleFS: rows the SD card did not take wait in `/rb/<number>.csv` and are copied back to the card (`pv_flashring.h`). Created at the first boot of this firmware; anything else in that partition is erased. |
| `nvs` | namespace `pvdaq`, key `t_rom`: the T1..T5 map saved with the `TROM` commands. Lost by "erase flash". |
