# Pin map — Freenove ESP32-WROVER, Phase 0 data logger

Proposed 2026-11-05. The code reads every pin from one header,
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
| Pump MOSFET gate (PWM) | **32** | O (LEDC) | Not strapping; high-impedance during reset, so the gate pull-down (10 kΩ) keeps the pump OFF until the firmware drives it LOW. |
| Measurement-load MOSFET (PWM, ~20 kHz) | **33** | O (LEDC) | Same reasoning; the gate driver input also needs a pull-down so the load stays off during reset. |
| test_marker button (to GND) | **27** | I, internal pull-up | Needs an internal pull-up (34–39 have none); not strapping. Press = LOW. |

Spare pins (kept free on purpose):

| GPIO | Note |
|---|---|
| 35, 36 (VP), 39 (VN) | ADC1, input-only: anemometer (pulse or analog, if approved) or a second analog channel. Need external pull-ups for a reed/pulse output. |
| 14 | General purpose. Not chosen for the MOSFET gates: it is a JTAG pin (MTMS) and is reported to toggle during boot. |
| 0, 2, 5, 12, 15 | Strapping — avoided. GPIO12 HIGH at reset selects 1.8 V flash and stops the board from booting. |

## I2C bus

```
ESP32 (21/22) ── [level shifter if the M5 side idles near 5 V — measure first] ── PaHub V2.1 (0x70)
   │                                                                              ├─ port "0" = chip ch1: INA226-10A, panel (0x41)
   │                                                                              └─ port "1" = chip ch2: INA226-10A, pump  (0x41)  [assumed]
   ├─ DS3231 RTC (0x68) + its AT24C32 EEPROM (0x57)
   └─ BME280 (0x76 or 0x77, set by SDO on the breakout)
```

- The two INA226-10A units both have the fixed address 0x41 (M5Stack docs), so each sits on its own PaHub channel; the firmware selects a channel before talking to that unit and deselects afterwards. PaHub channel selection follows the PCA9548A command byte (TI/NXP datasheet: bit *n* = 1 enables channel *n*).
- **Port labels ≠ chip channels.** Bench check 2026-11-05: the PaHub port printed "0" is PCA9548A channel 1. The code uses chip channel numbers (`pv_config.h`), and the `tests/ina226` scan prints them. The pump unit's channel 2 for port "1" is an assumption (port *n* = channel *n*+1) until the scan confirms it.
- RTC and BME280 are on the main (upstream) bus, always reachable; none of 0x57, 0x68, 0x76/0x77 collide with 0x41 or 0x70.
- 100 kHz bus clock (long leads, several modules with their own pull-ups).
- **Logic level (open):** M5Stack units and the PaHub run from 5 V on the HY2.0 port; the docs do not state the I2C pull-up voltage. Before connecting to GPIO21/22, power the PaHub alone and measure idle SDA and SCL to GND. Near 5 V → use a bidirectional 3.3 V ↔ 5 V I2C level shifter (likely the supervisor's "I2C converter"). Near 3.3 V → direct connection is acceptable.

## Power and grounds

- ESP32 board from USB (bench) or from the 5 V buck (field). M5 units, PaHub: 5 V. DS3231, BME280, DS18B20, MAX3485: 3.3 V from the ESP32 board. microSD module: see BRINGUP.md (depends on whether it has a regulator).
- PYR20: 3.9–30 V DC (manufacturer manual) → from the 12 V adapter (field) or 5 V (bench). Its ground must be common with the ESP32 ground for the 0–2 V input.
- All grounds meet at one point (see the load-grounding note in PROJECT_STATE §4).

## PWM outputs

LEDC, 20 kHz, 10-bit resolution (20 kHz is above hearing; 80 MHz / 20 kHz allows up to 11 bits). Both outputs are driven LOW (duty 0) at boot by every sketch in this folder; no sketch in Phase 0 turns them on. The pump and sweep logic come later.
