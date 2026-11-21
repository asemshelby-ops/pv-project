# Bench checklist — logger hardening (written 2026-11-21, for the first run on the board)

Firmware: `python firmware/build_all.py logger`, upload from `out/firmware_build/logger`
(the default build: no load, no pump, no flow sensor). Serial monitor at 115200, line end =
newline. Run `set_rtc.py` first. Write down every result; none of this has run on a board yet.

## 0. Start (5 min)

| Do | Expect | Result |
|---|---|---|
| Power on, read the first lines | `# flash buffer: ok, 0 segments waiting, … of 1408 kB used` (the very first boot may take some seconds: the file system is created) | |
| | `# start: <date>,<time>,<code>,power on` (or `unknown` after a reset from the USB lines) | |
| | `# status:` every fitted device `ok` | |
| Wait 1 min | one row every 10 s; last field (`status`) is `32` (no T1..T5 map yet) or `0` | |
| Open `/boot_log.csv` on the card later | one line per start | |

## 1. SD card out for 1 h, then back (≈ 70 min, mostly waiting)

| Do | Expect | Result |
|---|---|---|
| Note the time. Pull the card while the logger runs | within two rows: `# flash buffer: SD not writable, rows are kept in flash (…)`; rows go on every 10 s on Serial | |
| Look at the `status` of the rows | has 512 (BUFFERED); the first row after pulling may also have 256 | |
| Check the row rhythm with the card out | still 10 s, no `# warning: sample period overrun`. **If rows come late, note by how much**: a failed SD mount is tried at every row | |
| After 60 min, type nothing, read the last `# status:` line or reset-free: wait | no `# start:` line appeared (= no watchdog reset during the hour) | |
| Put the card back | within one or two rows the copying starts; after about 2 min: `# flash buffer: all rows copied to the SD card (… 0 segments dropped for room since boot)` | |
| Note "kB used" from the line printed when the card was pulled and the peak you saw | ___ kB for 360 rows → fills in the capacity estimate (now ≈ 18 h `[EST]`) | |
| Card in the PC: open the day file | no gap: 360 rows for the hour, times in order, each 10 s after the last, `status` with 512 | |
| `python -c "from analysis import ingest; d=ingest.read_logger(r'<folder>'); print(len(d), d.attrs)"` | `rows_duplicate` 0, `rows_without_time` 0 | |

Optional (10 min), the known weak point: pull the card for 15 min, put it back and cut the
power while the segments are being copied. After the restart the day file may hold up to
60 rows twice; `read_logger` must report them in `rows_duplicate` and give one row per time.

## 2. Forced watchdog reset (2 min)

| Do | Expect | Result |
|---|---|---|
| Type `WDT TEST` | `# WDT TEST: loop stalled now; the watchdog must reset the chip in 15 s` | |
| Time it | after 15 s (± 1 s) the boot messages appear again by themselves | |
| Read the new `# start:` line | reason `task watchdog (the loop stalled)` | |
| Wait 1 min | rows continue; `/boot_log.csv` has the new line; the day file goes on (gap ≈ 20–30 s) | |

## 3. TROM commands (10 min, with the DS18B20s on the bus, not yet mounted)

| Do | Expect | Result |
|---|---|---|
| `TROM` | one line per sensor: index, ROM, `crc ok`, `genuine pattern` or `clone?`, column T1..T5 | |
| `TROM SAVE` | `# TROM: assignment saved`, then the list with `T1..T5 from the saved map (TROM command)`; next rows: `status` without 32 | |
| Press reset | the same five ROMs on the same columns | |
| Unplug the sensor on T3, press reset | T3 empty (`status` has 2), **T4 and T5 unchanged** — before, they moved up | |
| Plug it back | T3 returns within 60 s (the 1-Wire bus is re-scanned every 60 s) | |
| `TROM SET 2 <ROM of another sensor already on T4>` | refused: `that sensor is already T4` | |
| `TROM SET 2 28-00-00-00-00-00-00-00` | refused: wrong CRC | |
| `TROM CLEAR` | list with `T_ROM NOT filled in -> T1..T5 by scan order`; `status` has 32 again | |

## 4. Later, not tomorrow: fixed resistor load

When the two 1.5 Ω resistors and their plate exist: BRINGUP §12 (install ≥ 1.5 m away in shade, downwind; check R_total, cable drop, plate temperature). Controller, battery and lamp were cancelled on 2026-11-21.

About §3 — leave the map cleared: the final one is set after the sensors are mounted on the panel
(then `TROM SAVE` or `TROM SET`, and the five addresses with their positions go into
`T_ROM` in `logger.ino`).
