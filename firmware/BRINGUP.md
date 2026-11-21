# Phase 0 bring-up checklist

One device at a time: wire it, flash its test sketch, compare with "correct output",
then move on. Pins: [PINMAP.md](PINMAP.md). Example output lines below show the FORMAT
only — they are not measurements.

## Before logging

1. Set the RTC to UTC: flash `tests/ds3231`, close the serial monitor, then run
   `.venv/Scripts/python firmware/tools/set_rtc.py COM3` (step 2).
2. Confirm the RTC reads UTC: the ds3231 test must print Makkah time **minus 3 h**, matching
   `python -c "import datetime as d; print(d.datetime.now(d.timezone.utc))"` to the second.
3. Flash the logger and press the marker button once: Serial shows
   `# marker: <date>,<HH:MM:SS.mmm>,<millis>` with a UTC time, and `/markers.csv` gains that row (step 9).
4. **INA226 reset check (new 2026-11-13, not done yet):** the panel INA226 driver now resets
   the chip every time it starts it. Run step 4a below once with the logger that will go
   outdoors, before it goes.

## Soiling: cleaning schedule and markers (short form)

Full protocol and log sheet: `docs/paper/05-daily-logging-and-cleaning.md`.

- **Schedule `[ASSUMPTION]`:** module glass and PYR20 dome, once every day before 07:30
  local. Distilled or RO water, microfibre cloth, no detergent; dome wiped damp, then dry.
- **Markers:** press the button before and after every cleaning: two rows in
  `/markers.csv`, two lines on the day's sheet (`cleaning start`, `cleaning end`).
  A skipped day: write "not cleaned".
- **Dust storm:** press the marker when it is noticed and write `dust storm`. Do not
  clean in the middle of the day; clean the next morning as usual.
- **In the analysis:** days after a `dust storm` line are flagged until the next cleaning,
  and the power per irradiance at midday is compared before and after each cleaning
  (`soiling_check.csv`). A check only: soiling is not a topic of the study.

## Build and flash

```bash
python firmware/build_all.py                 # compile everything (no flashing)
arduino-cli upload -p COM3 firmware/tests/ds3231
arduino-cli monitor -p COM3 -c baudrate=115200
```

The logger needs the NASIM include paths, so build it with `build_all.py logger`, then
upload with `arduino-cli upload -p COM3 --input-dir out/firmware_build/logger firmware/logger`.

Every sketch drives the pump (GPIO32) and both load outputs (branch A GPIO33, branch B GPIO14) LOW first. Even so, fit
the 10 kΩ gate pull-downs before connecting any MOSFET.

Build switches in `pv_config.h` (decisions 2026-11-10): `ENABLE_PUMP 0` — cooling is deferred,
so pump code is compiled out (not deleted); `HAS_ANEMOMETER 0` — no anemometer in this phase,
so the logger writes `NaN` in the wind column; `HAS_LOAD 0` — no measurement-load hardware
yet: both load pins stay LOW and the rows carry `NaN,none,0`. The load code is in the
logger all the same and is build-checked as `logger_load` (`-DHAS_LOAD=1`); it is switched
on only after the bench steps in [tests/load_bench/BENCH.md](tests/load_bench/BENCH.md).

### Pinned build environment (every `sketch.yaml`)

| Item | Version | Used for |
|---|---|---|
| arduino-cli | 1.4.1 | build tool |
| esp32:esp32 core | 3.3.8 | board support (FQBN esp32:esp32:esp32wrover) |
| RTClib (Adafruit) | 2.1.4 | DS3231 |
| Adafruit BME280 Library | 2.3.0 | BME280 |
| Adafruit BMP280 Library | 3.0.0 | BMP280 fallback |
| Adafruit Unified Sensor | 1.1.15 | dependency of the two above |
| Adafruit BusIO | 1.17.4 | dependency of RTClib / Adafruit sensors |
| INA226 (Rob Tillaart) | 0.6.6 | INA226 |
| OneWire | 2.3.8 | 1-Wire bus |
| DallasTemperature | 4.0.6 | DS18B20 |
| PvDaq (this repo) | 0.1.0 | our drivers, `firmware/libraries/PvDaq` |

SD, SPI, Wire and the ADC come with the ESP32 core. Modbus RTU and the PaHub are
written by hand (no library).

## Order

Any I2C device that is not found: flash `tests/i2c_lines` first. It reports, without any
I2C traffic, whether SDA/SCL have pull-ups, float, are held low, or are shorted together.

1. I2C level check (before any I2C wiring) → 2. DS3231 → 3. BME280 → 4. PaHub + INA226 →
5. DS18B20 → 6. microSD → 7. PYR20 Modbus → 8. PYR20 analog → 9. button → 10. logger.

---

## 1. I2C logic level (supervisor's "I2C converter")

Power the PaHub from 5 V with nothing else connected. Measure SDA→GND and SCL→GND.

| Reading | Action |
|---|---|
| ≈ 3.3 V | Connect PaHub SDA/SCL directly to GPIO21/22. |
| ≈ 5 V | Use a bidirectional 3.3 V ↔ 5 V I2C level shifter between the ESP32 and the PaHub. GPIO pins are not 5 V tolerant. |

Do the same for each M5 unit if it is ever wired without the PaHub.

## 2. DS3231 RTC — `tests/ds3231`

| Module pin | ESP32 |
|---|---|
| VCC | 3.3 V |
| GND | GND |
| SDA | GPIO21 |
| SCL | GPIO22 |
| SQW, 32K | not connected |

Set the time: run the sketch, close the serial monitor, then
`.venv/Scripts/python firmware/tools/set_rtc.py COM3` (UTC from the PC clock). The RTC holds
UTC; every logged timestamp is UTC, and the logger adds +3 h (Makkah) only for NASIM's inputs.

Drift record: before it sets the clock, the script asks the sketch for the RTC time
(command `NOW`, answered at the RTC's second edge) and appends one line to
`firmware/tools/rtc_set_log.csv`: the offset RTC − PC and, from the second run on, the
drift in s/day and ppm since the previous setting. Re-run it weekly during the campaign
(this needs the ds3231 test sketch flashed, then the logger flashed again) and commit the
log file. Expect below ≈ 0.3 s/day (datasheet); an older ds3231 sketch without `NOW`
still sets the clock, and the log line says the offset was not measured.

Correct output: one line per second, the seconds counting up, die temperature near room
temperature, and no "time not set" note after setting.
`2026-11-05 14:30:01  die 27.25 C  [ok]`

| Symptom | Likely cause |
|---|---|
| `not found (no ACK at 0x68)`, scan shows nothing or a stray address (e.g. 0x0A) | Flash `tests/i2c_lines`. "HELD LOW" on both lines = module VCC not powered (its pull-ups then pull the lines to 0 V) or lines on GND; "FLOATING" = wire not on GPIO21/22. Seen on our bench 2026-11-05: module VCC unpowered. |
| `not found` but i2c_lines shows "external pull-up present" | SDA/SCL swapped |
| `time not set` after every power-up | Backup cell flat or missing. This board charges its cell → use an LIR2032, not a CR2032 (CLAUDE.md) |
| Time drifts by seconds per day | Not a DS3231 (fake chip) or bad crystal: compare with internet time after 24 h |

The module's AT24C32 EEPROM also answers at 0x57. That is normal.

## 3. BME280 — `tests/bme280`

| Breakout pin | ESP32 |
|---|---|
| VIN / VCC | 3.3 V (check the breakout: some have a regulator and accept 5 V) |
| GND | GND |
| SDA | GPIO21 |
| SCL | GPIO22 |
| SDO (if present) | GND → 0x76, 3.3 V → 0x77 |
| CSB (if present) | 3.3 V (selects I2C) |

Correct output: `chip ID 0x60 -> BME280`, then T within about 1–2 °C of a reference
thermometer, RH plausible, P a few percent below 1013 hPa (it depends on the site's
altitude and the weather; compare with a nearby weather station).

| Symptom | Meaning / cause |
|---|---|
| `chip ID 0x58 -> BMP280 (NO humidity sensor)` | The seller shipped a BMP280. The humidity column will stay empty; buy a real BME280 if humidity is needed. |
| `unknown chip ID 0x61` | A BME680 |
| `not found` | Wiring, or CSB floating on a 6-pin board |
| T a few degrees high | Self-heating or heat from the board/enclosure: mount it in the radiation shield, away from the ESP32 |

## 4. PaHub + 2× INA226-10A — `tests/ina226`

| Connection | Detail |
|---|---|
| ESP32 → PaHub input port | HY2.0-4P: black GND, red 5 V, yellow SDA → GPIO21, white SCL → GPIO22 (through the level shifter if step 1 said so) |
| PaHub port "0" (= chip channel 0) | INA226-10A **panel** unit |
| PaHub port "1" (= chip channel 1) | INA226-10A **pump** unit — deferred: the test reads it only when `ENABLE_PUMP` is 1 |
| Panel unit current path | In the + lead **between the module and the load capacitor** (CLAUDE.md) |

First test with a bench supply and a resistor (for example 12 V across a power resistor),
not with the panel.

Correct output:
- The scan lists the PCA9548A's 8 chip channels (0–7); port "n" is channel n (bench check
  2026-11-05). Upstream devices (0x70, and 0x68/0x57 when the RTC is connected) appear on
  every channel.
- The scan shows `0x70` on the main bus, `0x41` on channel 0 (and on channel 1 if the pump unit
  is plugged in), and nothing at 0x41 on the main bus. With `ENABLE_PUMP 0` only the panel unit
  is started and printed.
- Plug and unplug Grove cables with USB disconnected: hot-plugging glitched the bus on our bench,
  and a half-seated cable made one unit disappear from the scan.
- `IDs 0x5449 / 0x2260`; `current LSB 500.0 uA`. The INA226 library rounds 10 A / 32768 = 305 µA
  up to a round value; nothing is lost, because the shunt ADC step is 2.5 µV ÷ 5 mΩ = 0.5 mA
  (TI datasheet: shunt-voltage LSB 2.5 µV). Bench check 2026-11-05: 500 µA, as expected.
- With no current flowing, I ≈ 0 A (a few mA of offset is normal). Bus V should match a
  multimeter within about 1 %. The current sign is positive in the intended direction.

| Symptom | Cause |
|---|---|
| Only one 0x41, or 0x41 on the main bus | A unit plugged into the wrong port, or two channels selected at once |
| `IDs are not TI INA226` | Not an INA226 at that address |
| Current negative | Current path wired backwards: swap the leads, or accept the sign and document it |
| Bus V 0 while current flows | **TODO:** check which terminal the M5 unit senses VBUS on (not stated in the M5 docs) |
| Readings stuck or noisy | Default conversion time and averaging (not yet chosen; **TODO** from the TI datasheet, PROJECT_STATE §4) |

### 4a. INA226 reset check — with the logger as flashed for the field (`HAS_LOAD 0`)

Why: since 2026-11-13 `PvIna226::begin()` writes the chip's reset bit before it writes the
calibration (so that the chip always converts continuously, even if a load test left it
in single-shot mode). This runs in today's logger at every start and every 60 s re-start
of a lost unit. It compiles; it has **not been run on the board**. A reset that went
wrong would show as voltage that never changes, or as current and power stuck at 0.

Set-up: bench supply 12 V, current limit 2 A, a power resistor that draws ≈ 0.5–1 A, the
panel unit in the + lead, a multimeter for V at the unit and one in series for I.
Flash the logger (`build_all.py logger`, upload from `out/firmware_build/logger`).

| # | Do | Correct result |
|---|---|---|
| 1 | Read three rows (30 s). | `voltage` within ≈ 1 % of the meter; `current` within ≈ 1 % of the meter and **not 0**; `power` ≈ V × I; `status` without bit 16 (`PANEL_FAIL`). A current of exactly 0 with a correct voltage = the calibration was lost in the reset |
| 2 | Change the supply to 9 V, wait two rows, back to 12 V. | The rows follow within one row (10 s). Values that do not move = the chip is not converting |
| 3 | Press the ESP32 reset button (the unit stays powered). Repeat 1. | Same values as before the reset |
| 4 | Unplug USB and the supply, wait 5 s, power up again. Repeat 1. | Same values |
| 5 | The case the reset was added for: flash `tests/load_bench`, type `R` twice (this puts the chip in single-shot mode), then flash the logger again **without removing power**. Repeat 1 and 2. | Rows are correct and follow the supply. If they are frozen at the last `R` value, the reset does not work |
| 6 | Re-start path: while the logger runs, pull the panel unit's Grove cable, wait two rows, plug it back. (Hot-plugging glitched the bus on our bench once: if other devices drop out, note it and power-cycle.) | Rows without the unit: empty `voltage,current,power`, `status` with bit 16. Within 60 s after plugging back: values as in 1, bit 16 gone |
| 7 | Flash `tests/ina226`. | Still `IDs 0x5449 / 0x2260` and `current LSB 500.0 uA`; values update every second |

Record date, meter model, and the rows of steps 1–6. All seven must pass before the
logger is installed outdoors. If one fails: do not deploy; the fix is to remove the
`ina_.reset()` line in `pv_ina226.cpp` (the logger then behaves as before 2026-11-13) and
report it.

Range: ±10 A and 0–30 V bus (M5 docs). The 100 W module's Voc is 21.5 V and Isc 5.88 A,
inside the range. **After** the load capacitor, the pulses reach about Voc ÷ `R_BRANCH` =
21.5 V ÷ 1.5 Ω ≈ 14 A `[EST]` in each branch, ≈ 29 A in total while both branches conduct
during a sweep (`R_BRANCH`, `R_SWEEP` in `pv_config.h`, decision 2026-11-11) → the unit must
stay on the module side.

## 5. DS18B20 ×5 (+ water/tank) — `tests/ds18b20`

| Cable wire (typical) | ESP32 |
|---|---|
| Red | 3.3 V |
| Black | GND |
| Yellow (sometimes white) | GPIO4 |
| **4.7 kΩ** | between GPIO4 and 3.3 V. The resistor that was ordered is 4.7 Ω, the wrong part: buy 4.7 kΩ. |

Correct output: every sensor listed with `CRC ok`. Genuine Maxim parts read
`28-xx-xx-xx-xx-00-00-xx` ("pattern ok"). Temperatures update every second in 0.0625 °C
steps, and all sensors agree within about 0.5 °C in the same air.

Cross-calibration: all sensors in one stirred water bath (2–3 temperatures if possible),
log for 10 min each, compute per-sensor offsets. Then copy each mounted sensor's ROM
into `T_ROM` in `logger/logger.ino` (T1..T5 = positions on the back sheet). The logger
also prints every ROM at boot and appends the list to `/rom_scan.txt` on the card, so
the addresses can be read without flashing this test sketch. Fill in all five rows:
until then the logger takes T1..T5 in scan order and sets status bit 32 in every row.

| Symptom | Cause |
|---|---|
| `no sensor` | Pull-up missing (or the 4.7 Ω part fitted!), DQ on the wrong pin |
| Some sensors missing on a long cable | Weak pull-up on long cables: try a lower value (for example 2.2 kΩ) after checking the total cable length |
| `-127` → empty / "no valid reading" | Contact or cable fault |
| Exactly 85.0 → treated as invalid | The power-on value of the register (Maxim datasheet): the conversion did not run. Often a supply problem in parasite mode; use 3-wire (powered) mode. |
| "LIKELY CLONE" | Bytes 5–6 are not 00-00. The sensor still works; keep it in the calibration and note it. Clones can show larger offsets or self-heating. |

## 6. microSD — `tests/sdcard`

| Module pin | ESP32 |
|---|---|
| VCC | 5 V if the module has a regulator (AMS1117) and level shifter, which most "Micro SD card adapter" boards have; 3.3 V if it has none. **Check the board before powering it.** |
| GND | GND |
| CS | GPIO13 |
| SCK | GPIO18 |
| MOSI | GPIO23 |
| MISO | GPIO19 |

Card: FAT32 (or FAT16 on small cards), ≤ 32 GB. Our bench card: 1 GB SDSC, shown as 960 MB.
Recommended: a 10 kΩ pull-up from CS (GPIO13) to 3.3 V, so the card stays deselected while
the ESP32 resets (see the first-mount note below).

Correct output: `card SDSC` or `SDHC/SDXC, N MB (mounted on attempt 1 or 2)`,
`write/read test: PASS`, then `append ...: ok` every 5 s. Pull the card: the next append shows a
failure, and after reinserting it mounts again.

No PC card reader? A card with no valid FAT volume can be formatted from the ESP32: run this
test and type `FORMAT` in the serial monitor. It ERASES the card, and only acts when the mount
fails with "no valid FAT volume" (a readable card is never touched). Used once on 2026-11-05.

First mount after a reset (bench 2026-11-05): with the card staying powered, the first mount
after an ESP32 reset failed in alternating resets, then in every reset; a second attempt
always worked (14/14). The driver therefore retries once after 100 ms. Suspected cause, not
proven: CS floats during the reset. A CS pull-up should make attempt 1 succeed.

| Symptom | Cause |
|---|---|
| `mount failed` | exFAT / NTFS format, card > 32 GB, wiring, or a module without a regulator fed from 5 V |
| Debug log `f_mount failed: (13) There is no valid FAT volume` | The card answers (wiring OK) but has no FAT file system: format it (FAT32/FAT), or use `FORMAT` above. Build with `--board-options DebugLevel=debug` to see this log. |
| Works on USB power, fails in the field | Supply dips during writes: add 100 µF near the module |
| MISO shared with another SPI device | Some adapters' level shifters do not release MISO. Keep the SD alone on this bus. |

## 7. PYR20 Modbus — `tests/pyr20_modbus`

| Connection | Detail |
|---|---|
| PYR20 red (V+) | 5–12 V (manual: 3.9–30 V DC) |
| PYR20 black (G) | GND, common with the ESP32 |
| PYR20 yellow (RS485 A / T+) | MAX3485 module A |
| PYR20 white (RS485 B / T−) | MAX3485 module B |
| MAX3485 VCC / GND | 3.3 V / GND (MAX3485 is a 3.3 V part; check the module) |
| MAX3485 TXD (DI) | GPIO25 |
| MAX3485 RXD (RO) | GPIO26 |

Wire colours are from the INFWIN manual for the RS485-only version. **TODO:** our unit is
the dual-output version (RS485 + 0–2 V); read the colours on its label or tag.

Correct output:
- `CRC self-test`: three `PASS` (frames from the manual). This runs without the sensor.
- `settings: address 1, baud code 3 (3 = 9600) ...`
- `FC 4: SR raw N -> N W/m2 [ok]` and the same value with FC 3. RX frame
  `01 04 02 hh ll crc crc`. Indoors N is small; shade the sensor and N drops, put a lamp on it and N rises.

| Symptom | Cause |
|---|---|
| `no reply` | A/B swapped (the most common), TXD/RXD swapped (module labels differ — swap GPIO25/26 wires), no sensor power, ground not common |
| `CRC mismatch` | Noise or a baud mismatch: check the settings line, use shielded twisted pair, add 120 Ω termination only on long cables |
| `Modbus exception code 2` | Register address not supported with that function code |
| `[echo of the request removed]` | The module echoes the request. Harmless; the driver strips it. |

Calibration: compare with a reference on a clear day (CLAUDE.md). The register is
1 W/m² per count (manual).

## 8. PYR20 analog — `tests/pyr20_analog`

| Connection | Detail |
|---|---|
| PYR20 analog output (blue, O1, per manual) | GPIO34, through a 1 kΩ series resistor (protection) |
| PYR20 ground | ESP32 GND (same point as the sensor's power ground) |

Correct output: mV ≈ the Modbus W/m² value (SR = 1000 × V), within a few % in steady
light. Below about 150 mV it reads "outside the 150–2450 mV ADC range": the ESP32 ADC
is not accurate there (ESP-IDF docs). Use Modbus for low light.

| Symptom | Cause |
|---|---|
| Random values with nothing connected | Normal: a floating input. The analog driver cannot detect the sensor. |
| Analog ≠ Modbus by a constant offset | Ground offset between the sensor and the ESP32: return the analog ground to the ESP32 GND |

## 9. test_marker button

| Button | ESP32 |
|---|---|
| One leg | GPIO27 (internal pull-up) |
| Other leg | GND |

Check with the logger: each press prints `# marker: date,time,millis` and appends the same
row to `/markers.csv` (header `date,time,millis`), for example
`2026-11-05,07:12:34.567,1234567`:

- `date`, `time` — UTC, time with milliseconds (`HH:MM:SS.mmm`), for alignment with an
  external data logger.
- `millis` — ESP32 uptime in ms at the press (taken in the interrupt).

The DS3231 counts whole seconds, so the logger notes `millis()` at the moment the RTC second
changes (after the first sample, then every 60 s) and times each press from that edge.
Accuracy is a few ms `[EST]`: check it against the external logger. A press before the
first edge is found, or with the RTC missing, writes `,,millis`. 200 ms debounce.

The 10 s CSV schema has no test_marker column yet: it is a proposed addition awaiting
confirmation (CLAUDE.md), so presses go to their own file. Rows written before 2026-11-10
hold local time with whole seconds.

## 10. Logger — `firmware/logger`

Correct output on Serial:

```
# PV Phase 0 logger. NASIM-GBT (no wind, double) trained on SYNTHETIC data.
# alarm = 1 NASIM-GBT (p >= 0.980, 2 rows) + 2 threshold (rise >= 2.70 C in 10 min) + 4 CUSUM (kappa 0.020 C, h 2.00 C); settings from SYNTHETIC data
# timestamps are UTC (DS3231)
# boot ROM scan, UTC 2026-11-05 06:59:48: 5 DS18B20, T_ROM NOT filled in -> T1..T5 by scan order
# 0,28-xx-xx-xx-xx-00-00-xx,crc ok,genuine pattern,T1
# ... (one line per sensor; the same lines go to /rom_scan.txt)
date,time,T1,T2,T3,T4,T5,T_ambient,humidity,irradiance,voltage,current,power,wind,p,alarm,D,load_mode,sweep_id,uptime_ms,status
# status: DS3231=ok; BME280=ok; PaHub=ok; INA226 panel=ok; DS18B20=ok; PYR20 Modbus=ok; microSD=ok;
2026-11-05,07:00:00,41.1250,...,812,18.0120,4.8810,87.9134,NaN,,0,NaN,none,0,12345,32
```

(The values in this example are illustrative, not measurements.)

- Date and time are UTC (10:00 in Makkah = 07:00 in the log).
- One row every 10 s; the time column steps by 10 s with no drift (millis schedule).
- Missing devices → empty fields, and the logger keeps running. RTC, BME280, PaHub and INA226 are started again at the next 10 s row; the DS18B20 scan, the PYR20 and the SD mount every 60 s.
- `# start: date,time,code,reason` at boot (also in `/boot_log.csv`): why the chip started. `task watchdog` = the loop stalled for 15 s and the chip reset itself; `brown-out` = the supply dipped.
- `# flash buffer: ok, 0 segments waiting, …` at boot. If the card is pulled or fails, rows go to the ESP32's flash (status bit 512) and are copied to the day files, in order, when the card is back (`# flash buffer: all rows copied to the SD card`). Room for roughly 18 h `[EST, not measured]`; beyond that the oldest 10 min go first. Markers, sweeps and logs are NOT buffered.
- Serial commands (115200, line end = newline): `TROM` lists the DS18B20 and their columns; `TROM SAVE` fixes today's assignment for good; `TROM SET 3 28-xx-xx-xx-xx-xx-xx-xx` puts one sensor on T3 (used once all five are set); `TROM CLEAR` forgets it. The saved map lives in the ESP32 (NVS): copy the five addresses into `T_ROM` in `logger.ino` as well, so they are in git.
- Read order in a row: time, then irradiance at once, then air, panel V/I, and the
  DS18B20 conversion last (so the temperatures are ≈ 0.1–0.9 s after the timestamp `[EST]`).
- `p` is empty outside 06:00–18:00 Makkah time (03:00–15:00 UTC in the log), during the first 10 min of data each day (warm-up),
  and when T1–T5, ambient, irradiance or the time is missing.
- `wind` is `NaN` (not measured) while `HAS_ANEMOMETER` is 0; an empty field elsewhere means a failed read.
- SD files: `/YYYYMMDD.csv` per UTC day (03:00 to 03:00 Makkah time), with a header line.
- Last five columns: `D,load_mode,sweep_id` are `NaN,none,0` until the load exists;
  `uptime_ms` is `millis()`; `status` is a bit field (table in
  `docs/paper/07-load-firmware-spec.md` §7). `32` = `T_ROM` not filled in; `0` = clean.
- One failed sensor read costs one row of `p` (status bit 64), not the detector state:
  NASIM is reset only after 5 such rows in a row (bit 128, and a `# detector reset` line).
- A row without RTC time is still written (date and time empty, status bit 1).
- A day file started by the older firmware keeps its old 16-column header: after flashing
  this version, start with a fresh day file (rename or move the old one off the card).

| Symptom | Cause |
|---|---|
| Date/time empty, status odd | RTC missing or not set (step 2). The rows are kept; times are rebuilt from `uptime_ms` |
| status has 256 | SD write needed a retry or rows were lost: check the card and its wiring |
| status has 512 | the row waited in the ESP32's flash because the card did not take it; it is in the day file, nothing to repair. Check the card |
| the same rows twice in a day file | power was cut between copying a flash segment and deleting it (≤ 60 rows): drop exact duplicate lines |
| several `task watchdog` lines in `/boot_log.csv` | a device blocks the loop for more than 15 s: look at the status line before each reset |
| Time 3 h ahead of UTC | RTC still on Makkah local time: re-run `set_rtc.py` (Before logging) |
| `# warning: sample period overrun` | A device is blocking for too long: look at the status line |
| T1..T5 in the wrong order, status has 32 | no T1..T5 map yet: `TROM SAVE` / `TROM SET` on Serial, then copy the addresses into `T_ROM` (step 5) |

## 11. Pump and flow sensor — `logger_pump` build (hardware not bought yet; compiled, never run)

Cooling is back in the paper (supervisor, 2026-11-14) and the second INA226 stays on the pump.
`ENABLE_PUMP` and `HAS_FLOW` are still 0 in `pv_config.h` until the pump is bought and the bucket
test has replaced the assumed thresholds: the logger as flashed today never drives
GPIO32 and does not count on GPIO35. The code exists so the bucket test can start the day the
pump arrives: `python firmware/build_all.py logger_pump`, upload from
`out/firmware_build/logger_pump`.

Wiring (PINMAP.md): pump INA226 unit in PaHub port "1", in the pump's supply line; GPIO32 →
100 Ω → gate of a logic-level N-MOSFET, 10 kΩ gate pull-down, flyback diode across the pump,
fuse in the pump line; flow sensor pulse output → GPIO35 with a pull-up to 3.3 V or a divider
(the pin has no internal pull).

| Command | Does |
|---|---|
| `PUMP 60` | soft start to 60 % duty (1 s from 0 to 100 %); below `PUMP_D_MIN` (50 %) is refused |
| `PUMP OFF` | stop now |
| `PUMP` | state, duty, last V and I, trip reason |
| `PUMP RESET` | clear a trip — after looking for the cause |

The pump is cut off, and stays off until `PUMP RESET`, when its INA226 reads: more than
`PUMP_I_TRIP_A` three times in a row (over-current); less than `PUMP_I_DRY_A` for 3 s once
10 s have passed since the start (dry run); or nothing three times in a row. **Every one of
these numbers is an assumption** (`pv_config.h`, pump block): the first job of the bucket
test is to replace them — current at each duty with water, while still drawing air, and the
lowest duty that starts the pump. Expect false dry-run trips until then.
Files: `/pump_log.csv` (commands, starts, stops, trips), `/PPYYYYMMDD.csv` (per 10 s row in
which the pump ran or water flowed: duty, V, I, P, state, `flow_pulses`, `flow_Lmin`).
`flow_Lmin` stays empty until `FLOW_PULSES_PER_L` is measured (pulses counted ÷ litres caught).
Nothing in the firmware starts the pump by itself.

**YF-S401 output, measure first (2 min, multimeter).** Its vendor sheet gives only the levels
(> 4.5 V high, < 0.5 V low at 5 V), not the output type. Power the sensor from 5 V, signal
wire connected to nothing, rotor still (blow gently until the reading is the high one):

| Signal wire to GND reads | Then add 10 kΩ from signal to GND | Meaning | Wiring to GPIO35 |
|---|---|---|---|
| ≈ 0 V or drifting, never a steady high | — | open collector, no pull-up inside | 10 kΩ pull-up to **3.3 V**, no divider |
| ≈ 5 V | stays > 4.5 V | driven output | divider 10 kΩ (top) / 20 kΩ (to GND): 3.3 V at the pin |
| ≈ 5 V | drops to V | pull-up inside: R = 10 kΩ × (5 − V) / V | pin sees 5 V × 20k / (R + 30k) through the 10k/20k divider: must be 2.6–3.3 V, i.e. R below ≈ 8 kΩ; otherwise change the divider |

Do not add a 10 kΩ pull-up to 5 V in front of the 10k/20k divider: the high level at the pin
would be 5 V × 20/40 = 2.5 V, at the ESP32's input threshold (0.75 × 3.3 V = 2.475 V).

## 12. Charge controller go/no-go — before ANY data is used for γ (Method C)

γ Method C (`docs/paper/02` §9) needs a controller that really tracks the maximum-power
point. A PWM-type unit (also when its label says "MPPT") holds the module near the battery
voltage and makes γ invisible. Do this on the first clear day with the controller, the
battery and the lamp connected, and again after any change of controller, battery or lamp.

Conditions: within ± 1 h of solar noon, clear sky, G ≥ 800 W/m², module cleaned, lamp ON,
logger running. Multimeter on the battery terminals (or the third INA226 once fitted).

| Step | Do | GO | NO-GO |
|---|---|---|---|
| 1 | Read module voltage (column `voltage`) and battery voltage, 10 min | module ≈ 15–18 V (the listing's `V_mp` is 18 V at 25 °C; a hot module sits lower `[EST]`) **and at least 2 V above the battery voltage** | module within ≈ 1 V of the battery voltage (≈ 12–14.5 V): the controller is switching, not tracking |
| 2 | Read module power against irradiance | power ÷ G roughly constant over the 10 min | power far below the listing's 100 W × G/1000 with the lamp on and the battery not full |
| 3 | Shade the module with the 20 % mesh for 2 min, remove it | module voltage stays in the same band while the current falls and returns | voltage collapses to the battery voltage, or stays pinned whatever the light |
| 4 | Switch the lamp OFF for 5 min with the battery near full, then ON | voltage rises toward `V_oc` (≈ 21 V listing) when the controller curtails, and comes back down with the lamp: this is what a curtailed row looks like — note the voltage, it sets the rejection rule | — |
| 5 | Disconnect the module at the controller for 10 s (controller's manual first) | `V_oc` on the logger, for the record | — |

Write the four voltages (module, battery, curtailed, `V_oc`), G and the back-sheet
temperature in the day's log sheet. **NO-GO in step 1 or 3 = no γ from this controller**:
tell the supervisor the same day; rows logged with it are still good for temperatures and
spike detection, not for power cost.

## 13. Lamp in the solar-noon window, switched by the ESP32 (decision 2026-11-21, replaces the earlier §13)

Plan, nothing bought or coded: lamp 12 V, **80–100 W**, ON only in a window of about ± 2 h
around solar noon (the same window as the row filter of γ Method C, `docs/paper/02` §9),
OFF the rest of the day so the module recharges the battery. Switched by the ESP32 on
GPIO33 (`pins::LAMP_RELAY`). Third INA226 in the battery lead (net current, PINMAP).

**Wiring.** GPIO33 → active-HIGH switch with a 10 kΩ pull-down at its input (logic-level
MOSFET module, or a relay module with its own driver transistor; not an active-LOW relay
board). Switch in the lamp's positive lead, rated ≥ 10 A DC (100 W at 12 V ≈ 8.3 A), fed
from the battery side of the shunt through a fuse. Battery shunt between the battery
terminal and the node where controller and lamp meet.

**Daily energy balance `[EST]` — every figure is an estimate from the seller listing and
round assumptions, none is measured.** Assumptions: clear day; module ≈ 100 W × G/1000 ×
0.86 (hot module, illustrative γ) × 0.95 (controller) ≈ 73 W at the window's mean
G ≈ 900 W/m²; ≈ 5.5 peak-sun hours in the plane per clear day; 85 % of the energy sent to
an AGM battery comes back; battery 26 Ah × 12 V = 312 Wh nominal, so 50 % = 156 Wh.

| | Lamp 80 W | Lamp 100 W |
|---|---|---|
| Lamp, 4 h window | 320 Wh | 400 Wh |
| Module in the window, clear | ≈ 290 Wh | ≈ 290 Wh |
| Taken from the battery in the window, clear | ≈ 30 Wh (10 %) | ≈ 110 Wh (35 %) |
| Module outside the window, clear (≈ 450 − 290) | ≈ 160 Wh → ≈ 135 Wh stored | same |
| Clear-day result | recharges fully; battery ≥ 90 % | just recharges (135 vs 110 Wh): **no margin** |
| Battery falls below 50 % in one window if the module averages less than | ≈ 41 W (≈ 55 % of a clear day) | ≈ 61 W (≈ 85 % of a clear day) |
| Hazy / cloudy window, module at 30 % (≈ 90 Wh), lamp left on | −230 Wh: battery to ≈ 25 % | −310 Wh: battery flat |

**So: yes, a 26 Ah AGM can fall below 50 % state of charge**, in one day, whenever the lamp
runs through a window with weak sun, and with the 100 W lamp also after two or three
merely average days, because the recharge has no margin. A blind timer is therefore not
enough. And the other way round: an 80 W lamp is below the module's peak (≈ 85–90 W at
1000 W/m² `[EST]`), so on the clearest noons a full battery would still be curtailed for a
while. Between the two, **≈ 100 W with the conditions below, or a larger battery**, is the
consistent choice `[REC]`; the usable capacity at a 3–4 h discharge is also below the
nominal (20 h) figure `[OPEN: battery datasheet, with its charge-current limit and cycle
life at 35–50 % depth]`.

**Rules the firmware needs (not coded; to write when the hardware exists):**
1. Window: solar noon ± 2 h from the RTC (solar noon computed for the date, as NASIM
   already does for its features), plus ≈ 10 min before, so the controller has settled
   when the first usable row comes.
2. Sun: ON only while irradiance ≥ ≈ 800 W/m² for some minutes; OFF below ≈ 700 W/m²
   `[ASSUMPTION, to tune]`. Rows below 850 W/m² are not used for γ anyway, so nothing is
   lost, and the lamp never drains the battery under cloud or dust. PYR20 not readable → OFF.
3. Battery: OFF below a voltage from the battery's datasheet (≈ 50 % under load;
   `[OPEN]`), read by the third INA226; that reading missing → OFF. Not ON again the same
   day after such a cut-off.
4. Safe state: OFF after every reset until 1–3 hold; a watchdog reset or a dead ESP32
   releases the pin and the pull-down switches the lamp off. A switch stuck ON is not
   covered: choose a controller whose load output or own cut-off protects the battery.
5. Every switch goes to a log file and into the 10 s rows (lamp state), with a few minutes
   around it excluded from γ.

**Side effect on the spike study — to tell the supervisor.** What the module is connected
to changes its temperature: a module that delivers power runs cooler than one the
controller has curtailed, by roughly G × 15 % ÷ (25 + 6.84 · v) ≈ 4 °C at 900 W/m² and
1 m/s `[EST, Faiman defaults; the 15 % is a round figure]`. That is the size of a thermal
spike. It will happen every time the battery fills outside the window (afternoon, lamp
off) and can happen at the window's edges. These rises are electrical, not wind: they must
be labelled from the lamp state and the battery state and kept out of the spike counts.
