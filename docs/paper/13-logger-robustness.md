# Logger robustness — audit, fixes, CSV v2

Status 2026-11-21: written **without the board**. Everything below was checked in two
ways only: (a) the logger compiles for the ESP32 (`python firmware/build_all.py`), and
(b) its row logic runs on the PC against **fake** drivers (`tests/test_logger_robustness.py`).
Nothing here has run on hardware. What only the board can confirm is listed in §10; the
procedure for it is §9.

Why: logging starts at the end of November and a lost row cannot be measured again, so a
failed sensor must cost its own fields for its own rows and nothing else.

## 1. Audit: where one failed read could block, crash, reset or corrupt a row

State of `main` before this work. "Library" = the pinned third-party library, read in
its source under `Arduino15`.

### Can block

| # | Where | What happens | Now |
|---|---|---|---|
| A1 | BME280 library `init()`, called by `PvBme280::begin()` at boot and at every re-start of a lost sensor | `while (isReadingCalibration()) delay(10);` has **no time limit**. A read that fails returns the register address `0xF3`, whose bit 0 is set, so the loop never ends if the sensor drops off the bus during `begin()`. The no-load logger had no watchdog: it would hang until someone pressed reset | The library's `begin()` is not called any more: `PvBoundedBme280::beginBounded()` runs the same sequence through the library's protected members with the wait limited to 200 ms. It runs once per chip; a later `begin()` only writes the sampling mode |
| A2 | BMP280 library `takeForcedMeasurement()`, every row if the board carries a BMP280 | `while (read8(STATUS) & 0x08) delay(1);` has no time limit: a chip that keeps reporting "measuring" blocks for ever | Not called any more: `PvBme280::read()` triggers the measurement and waits in its own loop, 100 ms at most |
| A3 | No watchdog on the loop unless `HAS_LOAD` is 1 | Any block of the kind above = a dead logger, no data until a manual reset | Watchdog on the loop task in every build, 30 s (§6) |
| A4 | I2C time-out left to the core's default | 50 ms per transaction, but only by default | Set explicitly: `Wire.setTimeOut(I2C_TIMEOUT_MS)`, 50 ms |
| A5 | SD mount of a missing card | Bounded by the core (each SD command waits 0.5–1 s), length unknown | One mount (two attempts 100 ms apart) per row while the card is away, never more (§5); to be timed in §9 |

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
| C6 | SD `appendLine()` | `File::println()` only fills a buffer; the write happens in `close()`, whose result the Arduino `File` API drops. A row could be **reported as written when it was not** | The card is written with `fwrite`/`fflush`/`fsync`/`fclose`, each checked (`PvSdCard::appendBytes`); a row that did not reach the card goes to the LittleFS ring (§5) |
| C7 | A line cut off by a power loss | The next row is appended to the same line: two rows merged | The store reads the end of a file before its first write of a boot (and after any failed write): a fragment is left on its own line. The loader drops and counts such lines (`rows_malformed`) |
| C8 | A day file begun by older firmware | New rows under an old header: columns misread | Every block of rows starts with `# nasim-logger-csv v2` and its own header; the loader reads block by block |
| C9 | Markers, `/rom_scan.txt` lines | Written only `if (sd.ok())`: a button press during an SD outage is **lost** | Markers go through the same store as rows: a press while the card is away waits in the ring. `/rom_scan.txt` is still written only if the card is there (its content is also in `/events.csv` as `unknown_rom` and on Serial) |

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
| BME280 first `begin` for a chip (at boot, or when a sensor absent at boot appears) | ≈ 2.6 s | 200 ms wait, 110 ms of fixed delays, about 45 transactions |
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
| `ring_rows` | lines (rows, events, markers) waiting in the LittleFS ring when this row was built; 0 = the card is up to date |
| `ring_dropped` | lines the ring had to give up since it was last empty (§5); 0 = nothing lost |
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
Bit 8 (`SD_RETRY`) now reads "not on the card at the first attempt" (second attempt, or
through the ring). New bit 9 (`SD_BUFFERED`): the row waited in the ring (§5).

**Version line.** Every block of rows starts with the line `# nasim-logger-csv v2` and
the header line. The logger writes such a block at the top of a new file and with the
first rows after every boot, so a file may contain several blocks, also of different
formats (a firmware update at midday). Files without the line are older: 16 columns
(version 0) or 21 (version 1). `analysis/ingest.py` reads block by block and gives every
row its `csv_version`; a line whose field count does not match its block's header is left
out and counted (`df.attrs["rows_malformed"]`).

`/events.csv` (`date,time,uptime_ms,event,detail`, UTC): `boot`, `health`, `sensor_fail`,
`sensor_ok`, `unknown_rom`, `rtc_jump`, `i2c_restart`, `config`, `events_missed`, and from
the store `sd_lost`, `sd_back`, `ring_found`, `ring_flushed`, `ring_full`, `ring_error`,
`ring_formatted`.

## 5. Card missing or failing: the LittleFS ring

Implemented in `pv_store_logic.c` (plain C, tested on the PC with a fake card and a fake
flash); the card is reached through `PvSdCard`, the flash through `PvFlashRing`.

**Where a line goes.** Rows, events and markers all take the same path:

1. At every row the store first looks after the card (`pvstore_service`): if it was down,
   **one** mount is tried (the card is retried every 10 s, for as long as it is away); if
   it is up and lines wait in the ring, they are copied to it, oldest first.
2. Card up and nothing waiting: the line is written to the card. If that write fails, one
   re-mount and one more attempt (`SD_RETRY`).
3. Otherwise the line goes into the ring on the ESP32's own flash, with `SD_RETRY` +
   `SD_BUFFERED` in a row's `status` and the `sd` bit in `sens_fail`.
4. **While anything waits in the ring, new lines go to the ring too**, even if the card is
   back. That keeps every file in the order the lines were made. Copying is limited to 64
   lines and 1 s per row, so a long backlog drains over several rows without stopping the
   sampling.

**Exactly once.** A write can fail after its bytes reached the card, and power can go
between a write and the note that it was done. So before a line is written whose earlier
write failed, and before the first line copied after a restart, the **end of its file is
read**: if the line is already there it is not written again. Rows are compared by their
first 20 fields (date … `uptime_ms`), which do not change between attempts. In the same
read a file that ends in a fragment (a write cut off by a power loss) is noticed, and the
next line starts on a fresh line. Two faults at once can still duplicate rows: the flash
refusing to save the place **and** a restart during the copy; `analysis/ingest.py` drops
rows with a repeated time.

**The ring.** Segment files `/ring/NNNNNNNN.seg` of 32 lines each, written by appending
only (LittleFS appends cheaply but rewrites a file to change its middle), plus
`/ring/state.txt` with the place of the oldest line not yet on the card. Everything is on
flash: lines survive a watchdog reset and a power cut, and the next boot goes on
(`ring_found`).

**Full.** Capacity 128 segments = 4096 lines ≈ 11 h of rows, or less if the 1.4 MB
partition fills first. When full, the **oldest segment (32 lines) is given up** to make
room for the newest, and counted: column `ring_dropped`, event `ring_full` (once per
outage), and the total in `ring_flushed` when the card is back. Sampling never stops.
Copying a full ring back goes in **chunks, one per row** (follow-up of 2026-11-21): at
most 64 lines and 1 s per row, the row's mount attempt included, and a line is started
only if the time used so far plus the slowest line of this chunk still fits in the 1 s.
(Before, the check was only "1 s not yet over", so the last line could end past it: on a
card taking 300 ms per write the fake measured 1.24 s per chunk, now ≤ 0.95 s.) A card
that stalls on one write overshoots by that write only, and the stall then ends the
chunk. With the fake's card times a full ring (4096 lines) goes back in 186 rows, about
23 lines per row, ≈ 31 min `[EST]`, no chunk over 1 s and no row over 1.2 s; a card at
450 ms per write still gets 2 lines per row, so the backlog shrinks, slowly. Tests:
`test_a_full_ring_goes_back_…`, `test_a_record_that_might_not_fit_…`,
`test_a_card_that_stalls_…`, `test_full_ring_and_every_sensor_at_its_time_out_…`
(every sensor at its time-out and a full ring on a 250 ms card: 7.0 s in the fake, test limit 9 s). Rows made
meanwhile join the end of the ring; if the ring is still full when one arrives, the
oldest segment is given up as above. If
the flash itself cannot be used, a line that cannot reach the card is counted the same way
(`ring_error`) and the next row that reaches the card carries `SD_RETRY`.

**Never formatted.** The logger mounts the card with `PvSdCard::begin()`, which cannot
format; the store's driver table has no format function; a test checks both. The one
thing that is formatted, once, is the **internal** flash partition if it holds no LittleFS
(first boot of this firmware): event `ring_formatted`.

Not buffered: the load's files (`/SW*.csv`, `/SS*.csv`, `/load_log.csv`, only with
`HAS_LOAD 1`) and `/rom_scan.txt`. They are written with the checked writes, but straight
to the card.

## 6. Watchdog and reset reason

- The ESP32 task watchdog watches the Arduino loop task in **every** build, from the start
  of `setup()`: `LOOP_WDT_MS` = 30 s without the load (three row periods, and well above
  the slowest normal pass: ≈ 7 s of sensor time-outs `[EST]` + a failed card mount + the
  1 s flush + the 1.1 s RTC edge search). With `HAS_LOAD 1` the 15 s chosen on 2026-11-13
  is kept; bench B7 must check it again, because a pass can now also hold a card flush.
- If `loop()` does not come round in that time the chip resets and logging starts again by
  itself. Lines in the ring are still there.
- Every boot writes one `boot` event to `/events.csv` (and Serial) with the reset reason:
  `POWERON`, `TASK_WDT`, `PANIC`, `BROWNOUT`, `SOFTWARE`, `EXT_PIN`, `INT_WDT`, …
- After a watchdog, panic or software reset the event also names the **last stage** the
  program had entered and its uptime, e.g. `reset=TASK_WDT (6); before it: BME280 at
  uptime 123456 ms`. The stage is kept in RTC memory, which those resets do not clear (a
  power cut does).
- Once an hour a `health` event: free heap, lowest free heap, unused stack of the loop
  task, LittleFS use. A slow loss of memory shows there long before it matters.

## 7. PC tests with fake drivers

`firmware/tools/logger_sim.py` holds the fake drivers; each can be told to **fail** (a
bad read), to **disappear** (no answer) or to be **slow** (each call takes as long as its
driver allows). The fake card can be pulled and put back, can refuse a write, can write
half a line, and can report failure for a write that did land; the fake flash can be
full, refuse writes or be unusable; `rig.reboot()` is a restart with the card and the
flash as they were. `tests/test_logger_robustness.py`, 81 tests: the row text (every
field, empty fields, date arithmetic against Python), the ROM table (scan order, missing
probe, zero rows, unknown ROMs once), retry at the next row, counters and the one report,
the PaHub rule, every sensor gone at once, the slowest possible row, the time rules, the
`millis()` wrap after 49.7 days; for the store: card away and back, no card at boot, one
failed write, a write that failed but landed, a half-written line, order during a long
flush, the flush limits, ring full, flash full, flash unusable, a restart with lines in
the ring (also with a full ring), a power cut between the card write and the saved place, markers and events in
the ring, an outage over midnight, an old-format day file on the card; and loading the
simulated card with `analysis/ingest.py`. Four more pin the sketch's text: the watchdog,
the first statement of `setup()`, the boot event, and that nothing is written past the
store.

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

SD card missing for 12 rows, then back. First the rows as the logger made them (and
printed on Serial), with where each went first:

```
time,T1,voltage,uptime_ms,status,sens_fail,f_sd,ring_rows,ring_dropped,cycle_ms,_where
06:00:00,40.0000,18.0000,5000,0,0,0,0,0,0,sd
06:00:10,40.0000,18.0000,15000,0,0,0,0,0,1060,sd
06:00:20,40.0000,18.0000,25000,0,0,0,0,0,912,sd
06:00:30,40.0000,18.0000,35000,768,4096,1,0,0,912,ring
06:00:40,40.0000,18.0000,45000,768,4096,2,2,0,2412,ring
06:00:50,40.0000,18.0000,55000,768,4096,3,3,0,2392,ring
06:01:00,40.0000,18.0000,65000,768,4096,4,5,0,2402,ring
06:01:10,40.0000,18.0000,75000,768,4096,5,6,0,2497,ring
06:01:20,40.0000,18.0000,85000,768,4096,6,7,0,2392,ring
06:01:30,40.0000,18.0000,95000,768,4096,7,8,0,2392,ring
06:01:40,40.0000,18.0000,105000,768,4096,8,9,0,2392,ring
06:01:50,40.0000,18.0000,115000,768,4096,9,10,0,2392,ring
06:02:00,40.0000,18.0000,125000,768,4096,10,11,0,2392,ring
06:02:10,40.0000,18.0000,135000,768,4096,11,12,0,2497,ring
06:02:20,40.0000,18.0000,145000,768,4096,12,13,0,2392,ring
06:02:30,40.0000,18.0000,155000,0,0,12,0,0,2392,sd
06:02:40,40.0000,18.0000,165000,0,0,12,0,0,1765,sd
06:02:50,40.0000,18.0000,175000,0,0,12,0,0,912,sd
06:03:00,40.0000,18.0000,185000,0,0,12,0,0,912,sd
06:03:10,40.0000,18.0000,195000,0,0,12,0,0,1017,sd
```

`status` 768 = `SD_RETRY` + `SD_BUFFERED`; `sens_fail` 4096 = `sd`. `ring_rows` counts
lines, so it also counts the two events of the outage. `cycle_ms` ≈ 2.4 s while the card
is away is the fake's 1.5 s for a failed mount `[EST]`, not a measurement. After the card
is back, `/20261125.csv` on the card holds **the same 20 rows, in this order, each once**
(the test compares them text for text), and `/events.csv`:

```
date,time,uptime_ms,event,detail
2026-11-25,06:00:32,37402,sd_lost,card not answering or write failed: lines go to the LittleFS buffer
2026-11-25,06:00:52,57392,sensor_fail,sd failed 3 rows in a row: row not written to the card at once
2026-11-25,06:02:31,156675,sensor_ok,sd back after 12 failed rows
2026-11-25,06:02:31,156705,sd_back,card mounted; 14 lines wait in the buffer
2026-11-25,06:02:31,156735,ring_flushed,14 lines copied from the buffer to the card in order; 0 given up
```

## 8. Flash and RAM

`python firmware/build_all.py logger`, esp32:esp32 3.3.8, FQBN `esp32:esp32:esp32wrover`
(app partition 1 310 720 B, RAM 327 680 B):

| Build | Flash | RAM (globals) |
|---|---|---|
| `main` before this work (`51d18aa`) | 461 974 B (35 %) | 30 792 B (9 %) |
| after Task 1 (`b14f1dd`) | 475 334 B (36 %) | 33 344 B (10 %) |
| after Task 2 | 530 966 B (40 %) | 36 264 B (11 %) |
| change, both tasks | +68 992 B | +5 472 B |

With `-DHAS_LOAD=1`: 481 761 B / 31 048 B before, 495 933 B / 33 592 B after Task 1,
550 409 B / 36 528 B after Task 2. Task 1's RAM is the event queue (16 × 128 B) and
the counters; Task 2's flash is mostly the LittleFS library, its RAM the store's two work
buffers. 60 % of the app partition and 89 % of the RAM stay free.

## 9. On-board test (to do; ≈ 30 min)

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

**Step 3 — pull the SD card for 2 minutes**, then push it back in.

| Where | Expect |
|---|---|
| Serial, while out | rows keep coming every 10 s, each with `status` + 768 and `sens_fail` + 4096; once `# event: …,sd_lost,…`; after 3 rows once `…,sensor_fail,sd failed 3 rows in a row…`; a `# status:` line showing `microSD=not found` |
| Serial, `ring_rows` | grows by 1 per row (plus the event lines): about 14 after 2 minutes |
| Serial, `cycle_ms` | **read it off**: the normal value + the time of one failed mount. This is the number the watchdog margin rests on; it must stay far below 30 000 |
| Serial, after re-inserting | within one row: `…,sd_back,card mounted; N lines wait…` and `…,ring_flushed,N lines copied from the buffer to the card in order; 0 given up`; rows again with `status` without 768 and `ring_rows` 0 |
| Card, afterwards (on the PC) | the day file has **every** row of the two minutes: `time` steps by 10 s through the whole test with no gap and no repeat, the 12 or so rows of the outage carry 768 in `status`; `/events.csv` has the four lines; a marker pressed while the card was out is in `/markers.csv` in its place |
| PC check | `python -c "from analysis import ingest; d = ingest.read_logger('E:/'); print(d.attrs, d['t_utc'].diff().dt.total_seconds().value_counts())"` → `rows_malformed` 0 and only 10.0 s steps over the test |
| Not expected | a reset; a gap in `time`; the same row twice; any question about formatting the card |

Pull the card **between** two rows if possible (just after a row appears on Serial).
Pulling it during a write is the harder case, worth one extra try: a half-written line
may then be on the card; it must stand on its own line and the row must follow whole.

**Step 4 — watchdog** (it can only be seen with a deliberate hang, so on the bench only):
flash once with a test line `while (true) {}` at the end of `sample()` (do not commit
it). Expect: about 30 s after the first row a panic print on Serial
(`task_wdt`), a reboot, and as the first event of the new boot
`…,boot,reset=TASK_WDT (6); before it: between rows at uptime … ms`. Then flash the
normal build again. Also power-cycle once: `…,boot,reset=POWERON (1); heap …`.

**Step 5 — first boot of this firmware**: the Serial banner must show
`# watchdog on the loop task: 30000 ms; LittleFS ring: ready, 0 lines waiting`, and
`/events.csv` a `ring_formatted` line once (never again at later boots).

## 10. Needs board

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
9. **Heap and stack over days**: the SD and device re-starts allocate and free; the
   hourly `health` event logs free heap, its lowest value and the unused stack of the
   loop task (8 KB in total). Look at it after the first day.
10. **GPIO 32 at reset**: that the pump gate stays LOW through power-up and reset (scope,
    with the 10 kΩ pull-down fitted). The pump stays unplugged for this phase anyway.
11. **Watchdog** (step 4): that it fires at 30 s, that the chip restarts and logs
    `TASK_WDT` with the stage, and that no normal row comes near the limit (`cycle_ms`).
    That the stage survives the reset in RTC memory.
12. **Real SD removal** (step 3): how long a failed mount takes (two attempts), whether
    the SPI driver recovers when the card is re-inserted, whether a write to a pulled card
    is reported as failed by `fsync` on this core, and whether pulling during a write
    leaves a fragment as assumed. FAT itself is not power-safe: a cut during a write can
    damage more than the last line; nothing here changes that.
13. **LittleFS on the real flash**: mount and first format on this board (partition
    `spiffs`, 1.4 MB), time of one append and of one state save (they add to `cycle_ms`
    during an outage and a flush), behaviour when the partition is full, and that the
    files survive a reset and a power cut as the design assumes.
14. **A long outage**: a night without the card (≈ 4000 lines), then the copy back:
    duration, `cycle_ms` during it, and `ring_dropped` = 0.
15. **Load build** (`HAS_LOAD 1`, later): the 15 s watchdog against a pass that holds a
    card flush (bench B7); the load's own files are not buffered.
