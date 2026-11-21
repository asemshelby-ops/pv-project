# Logger robustness — audit, fixes, CSV v2

Status 2026-11-21: written **without the board**. Everything below was checked in two
ways only: (a) the logger compiles for the ESP32 (`python firmware/build_all.py`), and
(b) its row logic runs on the PC against **fake** drivers (`tests/test_logger_robustness.py`).
Nothing here has run on hardware. What only the board can confirm is listed in §8; the
procedure for it is §7.

Why: logging starts at the end of November and a lost row cannot be measured again, so a
failed sensor must cost its own fields for its own rows and nothing else.

## 1. Audit: where one failed read could block, crash, reset or corrupt a row

State of `main` before this work. "Library" = the pinned third-party library, read in
its source under `Arduino15`.

### Can block

| # | Where | What happens | Now |
|---|---|---|---|
| A1 | BME280 library `init()`, called by `PvBme280::begin()` at boot and at every re-start of a lost sensor | `while (isReadingCalibration()) delay(10);` has **no time limit**. A read that fails returns the register address `0xF3`, whose bit 0 is set, so the loop never ends if the sensor drops off the bus during `begin()`. The no-load logger had no watchdog: it would hang until someone pressed reset | The library's `begin()` runs once per chip; a later `begin()` only writes the sampling mode. The one remaining call is covered by the watchdog (Task 2) |
| A2 | BMP280 library `takeForcedMeasurement()`, every row if the board carries a BMP280 | `while (read8(STATUS) & 0x08) delay(1);` has no time limit: a chip that keeps reporting "measuring" blocks for ever | Not called any more: `PvBme280::read()` triggers the measurement and waits in its own loop, 100 ms at most |
| A3 | No watchdog on the loop unless `HAS_LOAD` is 1 | Any block of the kind above = a dead logger, no data until a manual reset | Task 2 |
| A4 | I2C time-out left to the core's default | 50 ms per transaction, but only by default | Set explicitly: `Wire.setTimeOut(I2C_TIMEOUT_MS)`, 50 ms |
| A5 | SD mount of a missing card | Bounded by the core (each SD command waits 0.5–1 s), length unknown | Unchanged in Task 1; measured in §7 |

Bounded already, checked in the source: DS18B20 conversion wait (library limit 750 ms),
1-Wire reset (≈ 0.25 ms), Modbus reply wait (300 ms), BME280 `takeForcedMeasurement`
(2 s), RTC second-edge search (1.1 s), INA226 triggered read (0.45 s, `HAS_LOAD` only).

### Can crash or reset

No path was found where one failed read crashes or resets the board. Two weaknesses:

| # | Where | What happens | Now |
|---|---|---|---|
| B1 | The row was built with Arduino `String` (heap) at every row | Weeks of small allocations can fragment the heap; a failed allocation gives a **shortened row**, silently | The row is built with `snprintf` into a fixed buffer (`pvlog_format_row`); too small a buffer is refused, not truncated |
| B2 | Once a watchdog is on, every block of the list above becomes a reset | — | The blocks are removed first; the watchdog is the last resort |

### Can corrupt a row (a wrong value that looks measured)

| # | Where | What happens | Now |
|---|---|---|---|
| C1 | `PvBme280::read()` | The Adafruit libraries ignore the result of every I2C read. With the BME280 **unplugged**, `takeForcedMeasurement()` still returned `true` and `readTemperature()` computed a temperature from an unfilled buffer: a wrong `T_ambient` and `humidity` in **every row, status "ok", no flag**. `T_ambient` is a detector input | The chip ID is read before and after the measurement; values outside −40…85 °C or 0…100 %RH are refused |
| C2 | `PvIna226::read()` / `fetch()` | The INA226 library returns **0** for a register it could not read: a unit that drops out mid-read gave `0.0000` V, A, W as if measured | The library's error code is checked after each of the four reads |
| C3 | INA226 unit re-powered while the ESP32 stays on (PaHub cable touched, brown-out of the 5 V line) | The unit restarts with an empty calibration register: `current` and `power` read exactly 0 with a correct `voltage`, **from then on** | Detected (current register 0 while the shunt voltage shows > 50 mA); `begin()` writes the calibration again at the next row |
| C4 | `PvDs3231::read()` | RTClib `now()` ignores a failed read and builds a date from its buffer; it often passes `isValid()`. A row could carry a wrong time **and go to a wrong day file** | The seven time registers are read with the byte count checked and every field range-checked; in addition the logic refuses a time that is more than 60 s away from "last accepted time + elapsed uptime" until a second read confirms it |
| C5 | T1..T5 by bus-scan order while `T_ROM` is empty (today) | A probe missing at boot shifts every later column: T3's column holds T4's sensor, with no error | A column is the probe with the ROM of its `T_ROM` row, or empty. There is no scan-order mode any more |
| C6 | SD `appendLine()` | `File::println()` only fills a buffer; the write happens in `close()`, whose result the Arduino `File` API drops. A row could be **reported as written when it was not** | Task 2 (the store writes with `fwrite`/`fflush`/`fsync`/`fclose`, each checked) |
| C7 | A line cut off by a power loss | The next row is appended to the same line: two rows merged | The loader drops and counts lines with a wrong field count (`rows_malformed`); Task 2: the store starts a fresh line |
| C8 | A day file begun by older firmware | New rows under an old header: columns misread | Every block of rows starts with `# nasim-logger-csv v2` and its own header; the loader reads block by block |
| C9 | Markers, `/rom_scan.txt` lines | Written only `if (sd.ok())`: a button press during an SD outage is **lost** | Task 2 (markers go through the same store as rows) |

### Not read at all

| # | What | Now |
|---|---|---|
| D1 | PYR20 analog output on GPIO 34: only the test sketch read it. No second irradiance value when RS485 fails | Read every row (64 ADC samples, a few ms, cannot block) into the new column `pyr_mV` |
| D2 | DS18B20 probes 6 and 7 | Columns `T6`, `T7` |
| D3 | Pump INA226 (PaHub channel 1) | Not read: `ENABLE_PUMP 0`. Pump code was not touched |

### Retry and reporting

| # | Before | Now |
|---|---|---|
| E1 | A missing device was started again only every 6th row (60 s). A failed read was repeated at the next row but **without** `begin()`, so a chip that had lost its settings kept failing or returned stale values | Every failed device gets `begin()` + read at the **next row** |
| E2 | No counters. `TEMP_FAIL` did not say which probe. Nothing recorded when a sensor started to fail | Column `sens_fail` (one bit per sensor), one fail counter per sensor, and `/events.csv` |

## 2. How long a row can take

`pv_log_logic.c` calls each driver function at most once per row: one `begin()` (only if
the device failed at the row before) and one read. So a row cannot take longer than the
sum of the drivers' own limits. Those limits, counted from the driver code (`[EST]`: the
count of I2C transactions times the 50 ms time-out; **not measured**):

| Call | Limit `[EST]` | What sets it |
|---|---|---|
| DS3231 `begin` / read | 100 ms / 100 ms | 2 transactions each |
| BME280 `begin` (after its first) / read | 350 ms / 2.7 s | 7 transactions / the library's 2 s wait, our 100 ms wait, 8 transactions |
| PaHub `begin` / check | 100 ms / 50 ms | 2 / 1 transactions |
| INA226 `begin` / read | 600 ms / 550 ms | 12 / 11 transactions |
| PYR20 Modbus `begin` / read | 350 ms / 350 ms | 300 ms reply time-out + the 8-byte request |
| PYR20 analog | a few ms | 64 ADC samples |
| DS18B20 conversion | 750 ms | library limit |
| DS18B20 read, per probe | ≈ 12 ms, 7 probes | 1-Wire timing |
| 1-Wire bus scan (every 6th row) | ≈ 0.6 s | search of 7 probes |

Sum, everything at its limit in the same row: ≈ 6.8 s `[EST]`, below the 10 s period and
far below the 30 s watchdog. A normal row is ≈ 0.9 s `[EST]` (dominated by the 750 ms
conversion). The real figures are in every row from now on: column `cycle_ms`.

## 3. Rules the logger now follows

Implemented in `firmware/libraries/PvDaq/src/pv_log_logic.c` (plain C, no Arduino), which
the sketch connects to the real drivers through a table of functions (`pvlog_io_t`,
the `io…` functions in `logger.ino`). The PC tests connect the same file to fake drivers.

1. **A failed read empties its own fields only.** Empty field = no reading. No other
   field moves; the row is always written, every 10 s.
2. **Retry at the next row.** A device that failed is started again (`begin()`) and read
   again 10 s later, every row, for as long as it fails. `begin()` also restores what a
   power glitch erases (INA226 calibration, BME280 sampling mode).
3. **Counted.** Each sensor has a counter of failed rows since boot (columns `f_T1` …
   `f_sd`) and a bit in `sens_fail`.
4. **Reported once.** After `FAIL_REPORT_AFTER` = 3 failed rows in a row: one line in
   `/events.csv` and on Serial (`sensor_fail`). One more when it is back (`sensor_ok`).
   Nothing in between.
5. **PaHub and INA226.** Without the PaHub the INA226 is not tried and not counted
   (`f_hub` counts, `f_ina` does not). When the PaHub is back the INA226 gets `begin()`
   first.
6. **DS18B20 by ROM.** One conversion for the whole bus, then each column asks for the
   ROM of its `T_ROM` row. A probe that is missing leaves its column empty. A `T_ROM` row
   left at zero means "no probe for this column": empty, not counted; for T1..T5 the row
   also carries `TROM_UNMAPPED` and the detectors do not run. The bus is searched at boot
   and every 60 s **only** to find ROMs that are in no table: each is reported once
   (`unknown_rom`), with a ready-made `T_ROM` row in the boot scan.
7. **Time.** A time outside 2025–2099, or more than 60 s away from the last accepted time
   plus the uptime that passed, is not used (row without date/time, as for a missing
   RTC). A real change of the clock is accepted at the second read that agrees
   (`rtc_jump`).
8. **I2C bus.** If RTC, BME280 and PaHub are all silent for 3 rows, the I2C driver is
   started again (`i2c_restart`). `[ASSUMPTION]` that this frees a stuck bus.

## 4. CSV v2

The 21 columns written since 2026-11-12 are unchanged, in the same order and format.
20 columns are appended:

```
date,time,T1,T2,T3,T4,T5,T_ambient,humidity,irradiance,voltage,current,power,wind,p,alarm,D,load_mode,sweep_id,uptime_ms,status,
T6,T7,pyr_mV,sens_fail,f_T1,f_T2,f_T3,f_T4,f_T5,f_T6,f_T7,f_env,f_rtc,f_hub,f_ina,f_pyr,f_sd,ring_rows,ring_dropped,cycle_ms
```

| Column | Meaning |
|---|---|
| `T6`, `T7` | the two further DS18B20, °C; empty = no reading, or no ROM in `T_ROM` |
| `pyr_mV` | PYR20 0–2 V output on GPIO 34, raw millivolts (manual: 1 mV = 1 W/m²). The ESP32 ADC is usable from ≈ 150 mV (ESP-IDF docs), so small values are not irradiance. Not validated; a pin with nothing connected reads noise |
| `sens_fail` | bit field, decimal: bit *n* = sensor *n* failed at this row |
| `f_T1` … `f_sd` | failed rows since boot, one counter per sensor, same order as the bits |
| `ring_rows`, `ring_dropped` | LittleFS buffer (Task 2); 0 until then |
| `cycle_ms` | how long the row **before** this one took, ms (0 in the first row) |

Sensor numbers (`pv_status.h` `PV_SENSOR_*` = `data/schema.py` `SENSOR_BITS`; a test
compares them):

| Bit | Value | Name | Fails when |
|---|---|---|---|
| 0–6 | 1–64 | `T1` … `T7` | its probe gave no valid reading (no answer, CRC, 85.0, out of −55…125 °C) |
| 7 | 128 | `env` | BME280/BMP280 |
| 8 | 256 | `rtc` | DS3231: no answer, time not set, or a time not accepted |
| 9 | 512 | `hub` | PaHub: no answer |
| 10 | 1024 | `ina` | panel INA226 itself |
| 11 | 2048 | `pyr` | PYR20 over RS485 |
| 12 | 4096 | `sd` | the row did not reach the card at once |

`status`: bits 0–8 keep their numbers. Changed meaning of bit 5 (`TROM_UNMAPPED`): in
CSV v2 the unmapped column is **empty**; in older files T1..T5 followed the scan order.
New bit 9 (`SD_BUFFERED`, Task 2).

**Version line.** Every block of rows starts with the line `# nasim-logger-csv v2` and
the header line. The logger writes such a block at the top of a new file and with the
first rows after every boot, so a file may contain several blocks, also of different
formats (a firmware update at midday). Files without the line are older: 16 columns
(version 0) or 21 (version 1). `analysis/ingest.py` reads block by block and gives every
row its `csv_version`; a line whose field count does not match its block's header is left
out and counted (`df.attrs["rows_malformed"]`).

`/events.csv` (`date,time,uptime_ms,event,detail`, UTC): `sensor_fail`, `sensor_ok`,
`unknown_rom`, `rtc_jump`, `i2c_restart`, `config`, `events_missed`.

## 5. PC tests with fake drivers

`firmware/tools/logger_sim.py` holds the fake drivers; each can be told to **fail** (a
bad read), to **disappear** (no answer) or to be **slow** (each call takes as long as its
driver allows). `tests/test_logger_robustness.py`, 57 tests: the row text (every field,
empty fields, date arithmetic against Python), the ROM table (scan order, missing probe,
zero rows, unknown ROMs once), retry at the next row, counters and the one report, the
PaHub rule, every sensor gone at once, the slowest possible row, the time rules, the
`millis()` wrap after 49.7 days, and loading the simulated card with `analysis/ingest.py`.

What they show: the decisions are right whatever the drivers return. What they do not
show: that the real drivers return within their limits on the real buses (§8).

Scenarios (`python firmware/tools/logger_sim.py`; **simulation, made-up values**):

One DS18B20 (T3) missing for 6 rows:

```
time,T1,T2,T3,T4,voltage,current,status,sens_fail,f_T3,f_hub,f_ina,cycle_ms
06:00:00,40.0000,41.0000,42.0000,43.0000,18.0000,5.0000,0,0,0,0,0,0
06:00:10,40.0000,41.0000,42.0000,43.0000,18.0000,5.0000,0,0,0,0,0,1060
06:00:20,40.0000,41.0000,42.0000,43.0000,18.0000,5.0000,0,0,0,0,0,912
06:00:30,40.0000,41.0000,,43.0000,18.0000,5.0000,2,4,1,0,0,912
06:00:40,40.0000,41.0000,,43.0000,18.0000,5.0000,2,4,2,0,0,912
06:00:50,40.0000,41.0000,,43.0000,18.0000,5.0000,2,4,3,0,0,912
06:01:00,40.0000,41.0000,,43.0000,18.0000,5.0000,2,4,4,0,0,912
06:01:10,40.0000,41.0000,,43.0000,18.0000,5.0000,2,4,5,0,0,1002
06:01:20,40.0000,41.0000,,43.0000,18.0000,5.0000,2,4,6,0,0,912
06:01:30,40.0000,41.0000,42.0000,43.0000,18.0000,5.0000,0,0,6,0,0,912
06:01:40,40.0000,41.0000,42.0000,43.0000,18.0000,5.0000,0,0,6,0,0,912
06:01:50,40.0000,41.0000,42.0000,43.0000,18.0000,5.0000,0,0,6,0,0,912
-- /events.csv
2026-11-25,06:00:50,55912,sensor_fail,T3 failed 3 rows in a row: no valid reading from its ROM
2026-11-25,06:01:30,95912,sensor_ok,T3 back after 6 failed rows
```

`status` 2 = `TEMP_FAIL`; `sens_fail` 4 = bit 2 = T3. T4 never moves into T3's column.

PaHub absent for 6 rows:

```
time,T1,T2,T3,T4,voltage,current,status,sens_fail,f_T3,f_hub,f_ina,cycle_ms
06:00:20,40.0000,41.0000,42.0000,43.0000,18.0000,5.0000,0,0,0,0,0,912
06:00:30,40.0000,41.0000,42.0000,43.0000,,,16,512,0,1,0,912
06:00:40,40.0000,41.0000,42.0000,43.0000,,,16,512,0,2,0,907
06:00:50,40.0000,41.0000,42.0000,43.0000,,,16,512,0,3,0,907
06:01:00,40.0000,41.0000,42.0000,43.0000,,,16,512,0,4,0,907
06:01:10,40.0000,41.0000,42.0000,43.0000,,,16,512,0,5,0,1012
06:01:20,40.0000,41.0000,42.0000,43.0000,,,16,512,0,6,0,907
06:01:30,40.0000,41.0000,42.0000,43.0000,18.0000,5.0000,0,0,0,6,0,907
-- /events.csv
2026-11-25,06:00:50,55907,sensor_fail,hub failed 3 rows in a row: no answer
2026-11-25,06:01:30,95916,sensor_ok,hub back after 6 failed rows
```

`status` 16 = `PANEL_FAIL`; `sens_fail` 512 = bit 9 = PaHub. `f_ina` stays 0.

## 6. Flash and RAM

`python firmware/build_all.py logger`, esp32:esp32 3.3.8, FQBN `esp32:esp32:esp32wrover`
(app partition 1 310 720 B, RAM 327 680 B):

| Build | Flash | RAM (globals) |
|---|---|---|
| `main` before this work (`51d18aa`) | 461 974 B (35 %) | 30 792 B (9 %) |
| after Task 1 | 475 334 B (36 %) | 33 344 B (10 %) |
| change | +13 360 B | +2 552 B |

With `-DHAS_LOAD=1`: 481 761 B / 31 048 B before, 495 933 B / 33 592 B after Task 1.
The RAM is the event queue (16 × 128 B) and the counters.

## 7. On-board test (to do; ≈ 20 min)

Before: `T_ROM` filled in (boot scan prints the rows), RTC set, card in, serial monitor
open at 115200, the pump unplugged. Let the logger write 6 clean rows first. Press the
marker button before and after each step. `[EXPECT]` = what the PC simulation gives;
times in `cycle_ms` are to be read off, not expected.

**Step 1 — unplug one DS18B20 for 60 s** (say the probe of T3), then plug it back.

| Where | Expect |
|---|---|
| CSV | 6 rows (± 1) with `T3` empty; `T1,T2,T4..T7` filled and in their usual range; `status` gains 2; `sens_fail` = 4; `f_T3` counts 1, 2, … 6 and then stays; first row after re-plugging has `T3` again |
| CSV | `cycle_ms` stays near its normal value (a missing probe costs nothing) |
| Serial | one line `# event: …,sensor_fail,T3 failed 3 rows in a row: …` about 30 s after unplugging; one `…,sensor_ok,T3 back after 6 failed rows`; **no** line per row |
| `/events.csv` | the same two lines |
| Not expected | a reset (boot banner), any other column empty, `T4` appearing under `T3` |

If the whole 1-Wire bus drops while the plug is moved (all of T1..T7 empty for a row),
note it: that is the connector, not the firmware.

**Step 2 — unplug the PaHub (its cable to the ESP32) for 60 s**, then plug it back.

| Where | Expect |
|---|---|
| CSV | `voltage,current,power` empty for ≈ 6 rows; `status` gains 16; `sens_fail` = 512; `f_hub` counts, `f_ina` stays; everything else filled (RTC and BME280 are on the main bus) |
| CSV, after | first row after re-plugging: V, I, P back. **`current` not 0** (the INA226 was re-powered: its calibration must have been written again). Compare with a meter |
| Serial | `sensor_fail,hub failed 3 rows in a row: …` once, `sensor_ok,hub back after …` once |
| Not expected | a reset; `T_ambient` or the time going empty (if they do: hot-plugging disturbed the main bus — note which, and whether `i2c_restart` appears) |

Step 3 (SD card) is added with Task 2.

## 8. Needs board

Cannot be confirmed without hardware; none of it is claimed.

1. **Time-outs on the real buses.** That each driver call returns within the limits of
   §2 with its device unplugged, and with the bus disturbed (SDA or SCL held low; 1-Wire
   line shorted to GND). Read `cycle_ms` in the rows of §7.
2. **INA226 after re-powering** (step 2): that the "calibration lost" check sees it and
   that `current` is right after `begin()`. Also that the check never fires on a healthy
   unit at night (current really 0).
3. **BME280/BMP280 read path**: the new sequence (chip ID, trigger, bounded wait, read,
   chip ID) gives the same values as before on the real chip; on a BMP280, that writing
   the sampling mode triggers one measurement as the datasheet says.
4. **DS3231 own register read**: same time as RTClib on the real chip
   (`tests/ds3231` uses the same driver: compare with the PC clock as in BRINGUP step 2).
5. **DS18B20 read by ROM** with 7 probes on the real cable: no reading lost by the extra
   bus scan every 60 s; time of the scan.
6. **Hot-plugging**: whether pulling one probe or the PaHub disturbs the other devices on
   our wiring (seen once on the bench, BRINGUP 4a step 6).
7. **I2C restart** (`i2c_restart`): whether it frees a bus that a device holds low.
8. **`pyr_mV`**: whether GPIO 34 is wired to the PYR20 analog output in the final rig,
   and how it compares with the Modbus value (the analog output is not validated).
9. **Heap over days**: the SD and device re-starts allocate and free; watch for a slow
   loss (Task 2 logs the free heap).
10. **GPIO 32 at reset**: that the pump gate stays LOW through power-up and reset (scope,
    with the 10 kΩ pull-down fitted). The pump stays unplugged for this phase anyway.
