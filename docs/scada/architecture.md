# SCADA architecture

State: 2026-11-13, branch `scada-prep`. Runs today on synthetic data; the serial input has
not been tried on the board.

## 1. Scope and the one hard rule

The SCADA layer is supervisory **monitoring**: it collects the logger's rows, keeps them,
shows them, and manages alarms. It controls nothing (the pump is off in this phase,
`ENABLE_PUMP 0`).

Hard rule: the logger firmware, the load firmware and the analysis pipeline are not
modified. The SCADA reads only what the logger already writes:

| Input | What it carries |
|---|---|
| `/YYYYMMDD.csv` | one row every 10 s, UTC, one file per UTC day |
| `/nodate.csv` | rows of a boot that never had RTC time |
| `/markers.csv` | button presses and `LOAD OFF` / `LOAD ON` marks |
| serial, 115200 baud | the same rows as they are written, `#` status lines, marker lines |

Not read yet: `/SW*.csv`, `/SS*.csv` (I-V sweeps), `/load_log.csv`, `/rom_scan.txt`
(see [roadmap.md](roadmap.md)).

## 2. The parts

```
 ESP32 logger (unchanged)
   |  SD card: YYYYMMDD.csv, nodate.csv, markers.csv        serial: rows, '#' lines
   |  (copied to a PC folder)                                  (USB, read-only)
   v                                                           v
 +--------------------+     +-------------------+     +--------------------+
 | replay.py          |---->| FolderIngest      |     | SerialIngest       |
 | plays recorded     |     | polls the folder  |     | reads the port     |
 | files into a       |     | every second      |     | (--serial)         |
 | folder (demo only) |     +---------+---------+     +----------+---------+
 +--------------------+               |      RowParser           |
                                      |  (one parser for both)   |
                                      v                          v
                         +------------------------------------------------+
                         | historian.py: one SQLite file                  |
                         | samples, unplaced_rows, markers, ingest_files, |
                         | alarms, alarm_journal, meta                    |
                         +----------+---------------------+---------------+
                                    |                     ^
                   alarms.py: evaluate new rows           | acknowledge
                   (raise / clear / journal)              |
                                    v                     |
                         +------------------------------------------------+
                         | server.py: JSON API on 127.0.0.1:8050          |
                         +------------------------+-----------------------+
                                                  v
                         web/index.html + app.js + style.css (the HMI)
```

| File | Role |
|---|---|
| `scada/model.py` | tag list, bit decoding, alarm definitions, sensor health; generates `tag-list.md` |
| `scada/historian.py` | the SQLite schema and a few helpers |
| `scada/ingest.py` | `RowParser`, `FolderIngest`, `SerialIngest` |
| `scada/alarms.py` | alarm life cycle, acknowledge, history, journal |
| `scada/server.py` | the API and the static files |
| `scada/web/` | the HMI pages (module page, rooftop SIMULATION page with its 3D view) |
| `scada/web/vendor/three-0.160.0/` | Three.js, vendored for the 3D view only (MIT; source and hashes in its README) |
| `scada/replay.py` | feeds recorded files faster than real time |
| `scada/demo_data.py` | builds the synthetic demo set |
| `scada/run.py` | starts everything in one process |

One process, a few threads (folder ingest, optional serial, optional replay, the web
server). Each thread has its own SQLite connection; the file is in WAL mode so the page
can read while the ingest writes.

## 3. Tag list and data model

`scada/model.py` does not type column names or bit numbers. It imports them from
`data/schema.py` (`COLUMNS`, `LOGGER_EXTRA_COLUMNS`, `STATUS_BITS`, `ALARM_BITS`), the
table the analysis and the firmware already share, and adds units, display ranges, the
decoded tags (`ALM.NASIM`, `ST.RTC_INVALID`, ...), two derived tags (`T_module`,
`dT_module_ambient`) and the alarm definitions. The result is written to
[tag-list.md](tag-list.md) by `python -m scada.model`.

Three tests guard it: the 21-column format equals the `HEADER` string in
`firmware/logger/logger.ino` (read as text, not changed); every column and every bit has
a tag; the committed `tag-list.md` equals what the model generates.

Ranges are display scales for the plots `[ASSUMPTION]`, not sensor limits. Nothing is
alarmed on a range yet: with no real data there is no basis for limits.

## 4. Ingest

**Formats.** A row is read by the column names of its file's header, so three formats
load: new (21 columns, since 2026-11-12), old (16 columns) and the bare schema
(14 columns). A column a format does not have is stored as NULL = "not recorded".
On serial, if the SCADA joins after boot and never saw the header, the format is taken
from the number of fields. A row whose field count differs from its file's header (an old
file that newer firmware goes on writing) is read by its own count.

**Empty and NaN.** The logger writes an empty field for "read failed" and `NaN` for "not
fitted" (wind, D). SQLite has no NaN, so both become NULL. The difference is not lost:
the status bits say what failed, and `load_mode = none` says no load is fitted.

**Growing files.** Each file is read from the byte where the last poll stopped, and only
up to its last complete line, so a half-written row is never parsed. The position and
the parser state are stored in the historian (`ingest_files`): after a restart the ingest
continues instead of re-reading. A file that became shorter is read from the start.

**Duplicates.** `samples` is keyed by the UTC second of the row. The same row from the
card and from serial, or a file ingested twice, is stored once. This is the rule of
`analysis/ingest.py` (`drop_duplicates("t_utc")`).

**Rows without RTC time** (date and time empty, status bit `RTC_INVALID`) are never
dropped:

1. If the same boot (uptime_ms rising, same file) has a row with RTC time, the time is
   rebuilt: `time = that row's time - its uptime_ms + this row's uptime_ms`
   (`time_source = "uptime"`). Same rule and same result as the offline loader; a test
   compares the two on the same files.
2. On serial, the PC clock at reception is used (`time_source = "pc"`).
3. Otherwise the row waits in `unplaced_rows`. When the first timed row of that boot
   arrives, the waiting rows are placed. Rows of `/nodate.csv` normally stay there. They
   are counted on the page and raise the RTC alarm.

**Lines that are not rows** (`#` lines, repeated headers, a line cut by a power loss, a
field that is not a number) are skipped and counted (`bad_lines`), not guessed.

## 5. Historian: why SQLite and not InfluxDB

| | SQLite | InfluxDB |
|---|---|---|
| Install on Windows | none: part of Python | a server to install, run and keep running |
| Data volume here | 8 640 rows/day, about 3.2 million/year: small | built for far more than we produce |
| Alarm list with acknowledge | ordinary tables and transactions | not what a time-series store is for; a second store would be needed |
| Backup, move to another PC | copy one file | export / import |
| Use from the analysis code | `pandas.read_sql` on the same file | a client library and a query language to learn |
| Grafana | needs a community plugin | first-class |

Chosen: **SQLite**. One 10 s logger does not need a time-series server, and the alarm
state (acknowledged by whom, when) is relational data. The choice is revisited if several
devices publish at a faster rate (roadmap, MQTT).

Table layout: `samples` is wide (one column per logged tag, one row per logger row)
because the schema is fixed and every query is "these columns over this time range".

## 6. HMI: why a light web app and not Grafana

| | Light web app (chosen) | Grafana |
|---|---|---|
| Install | none; served by the same Python process | a service to install; plus a SQLite plugin |
| Alarm acknowledge and history | plain buttons writing to our tables | Grafana alerting has silences, not an operator acknowledge against our alarm table; a second app would be needed anyway |
| Status-bit decoding, "not recorded" for old files | a few lines of code | awkward in SQL panels |
| Works offline on the lab PC | yes: no CDN; one vendored library (Three.js), only for the 3D view of the simulation page | yes |
| Charts | hand-written, limited to what we need | much richer, zoom, ad-hoc queries |
| Explaining it at the defence | 3 short files | a product configuration |

Chosen: **light web app**, because the alarm list with acknowledge is a required part
and Grafana would not give it. What we give up is Grafana's exploration tools; for
exploring data the analysis pipeline remains the tool.

Design rules followed in the charts: one y-axis per chart (temperature, irradiance and
power are three charts, not one with two scales); the same time axis and margins in all
charts so a moment lines up vertically; status colours (red, amber) only for states and
always next to a text label; every value also readable without the pointer (tiles, table
view). Colours are the documented default palette of the charting guideline used; its
validator script was **not run** here (it needs Node, which is not installed).

The page asks the server once a second. Long ranges are grouped into time buckets on the
server (at most about 1 500 points per chart): values are averaged, while `alarm` and
`status` are OR-ed, so an alarm or a fault never disappears in a coarse view.

The server listens on `127.0.0.1` only and has no login. It must not be exposed to the
network as it is ([roadmap.md](roadmap.md), remote access).

## 7. Alarms

Definitions: `ALARM_DEFS` in `scada/model.py`, listed in [tag-list.md](tag-list.md).

| Group | Alarms | Source |
|---|---|---|
| detector | `DET_NASIM`, `DET_THRESHOLD`, `DET_CUSUM` | `alarm` bits 1, 2, 4 |
| sensor | `TEMP_FAIL`, `AMBIENT_FAIL`, `IRRAD_FAIL`, `PANEL_FAIL` | `status` bits |
| system | `RTC_INVALID`, `SD_ERROR`, `DET_RESET`, `TROM_UNMAPPED` | `status` bits |
| system | `LOAD_TRIP` | `load_mode` = `trip` |
| system | `NO_DATA` | PC clock: no new row received for 60 s |

`LOAD_TRIP` was added beyond the request because the column already carries it and a trip
is the load's fault state. `DET_SKIPPED` is not an alarm (it follows from a fault that
already alarms); it is shown in the health panel. Priorities are a first proposal.

Life cycle of one occurrence:

```
            condition on                    off for N rows
  (none) ----------------> ACTIVE, unacked ----------------> CLEARED, unacked
                               | acknowledge                     | acknowledge
                               v                                 v
                           ACTIVE, acked  ------------------> CLEARED, acked (history only)
                                            off for N rows
```

- N = 1 for the detector alarms (they last one row; the detectors themselves stay silent
  for 10 min after an alarm) and 3 for faults, so a sensor that fails every other row is
  one alarm, not a flood.
- An occurrence stays in the alarm list until it is both cleared and acknowledged. It
  then remains in the history. Every RAISED, CLEARED and ACKED is a line in the journal.
- Only the first acknowledgement counts; it stores the operator's name, an optional note
  and the PC time.
- Two clocks: raised and cleared times come from the **data** (the row's UTC time);
  the acknowledge time is the **PC** clock. In a replay they differ by design.
- A row that cannot tell (its `status` was not recorded: old file) neither raises nor
  clears a status alarm.

Limits to know:

- Rows are evaluated once, in time order, from the newest row already evaluated. Rows
  that arrive **later with an older time** (a card copied after live data was received)
  are stored and plotted but raise no alarms. Until that is built: ingest old cards
  before going live, or into a separate historian (`--db`).
- `NO_DATA` is the only alarm that does not come from a row. It is raised when no new
  row has been **received** for longer than `NO_DATA_LIMIT_S` (60 s `[ASSUMPTION]`,
  `scada/model.py`: six missed rows) and cleared by the next new row. It uses the PC
  clock on purpose: the rows' own time says nothing about a silent logger, and in a
  replay it runs faster than real time. Consequences: its "raised" time is a PC time
  (the page says so); with a copy of the SD card that is not growing it is raised 60 s
  after the last file was read, which is correct but expected; and a row that arrives a
  second time (already stored) does not count as new.
- The detector alarms are what the board logged. The detectors are trained and tuned on
  **synthetic** data; their alarms are information, not a trigger for anything.

## 8. Replay and demo data

`scada/replay.py` plays the part of the SD card being written: it appends the lines of
recorded files to a folder at N times real time, each line when its own timestamp comes
up. The ingest watches that folder exactly as it would a copy of the card, so a replay
exercises the whole chain. Timestamps are never changed; only a hole longer than 10 min
of data (the night) is skipped.

`scada/demo_data.py` exists because the synthetic logger files hold `alarm = 0` and an
empty `p` (they are the files before any detector ran). It copies chosen days, optionally
injects a fixed fault scenario with the status bits the firmware would set, and fills
`p` and `alarm` with the run's saved model and tuned settings using the pipeline's own
functions (imported, not modified). Faults are injected first and the detectors run
second, so a skipped row or a reset changes the alarms as on the board. The output folder
says in `SCADA_FOLDER.txt` that it is synthetic, and the page repeats it in the banner.

Safety of files: the replay empties only folders that contain its own `SCADA_FOLDER.txt`,
and only a historian whose `meta.mode` is `replay`.

## 9. What is tested, what is not

Tested (65 tests, `tests/test_scada_*.py`, all on synthetic or typed rows):

- ingest: the three formats, mixed files, growing files, restart, duplicates, replaced
  file, rows without RTC time (same times as the offline loader), unplaceable rows,
  markers, serial lines;
- alarms: each bit, the life cycle, the 3-row clear delay, restart in the middle of an
  alarm, old-format rows, rows without time, evaluation order, the no-data alarm
  (also in a replay that stops and resumes);
- tag model against the firmware header and the schema; replay; the fault scenario; the
  API including acknowledge; the separation of live and replay historians.

Checked by eye in a browser: the page during a replay of the two demo days (charts, state
strip, health, alarm list, acknowledge, history, journal, 7-day view, tooltip).

Not tested:

- anything with the real board: the serial port (DTR/RTS reset behaviour), real files
  from the SD card, real fault patterns;
- long runs (weeks of data in the historian, the page left open for days);
- two inputs feeding at the same time on real hardware.
