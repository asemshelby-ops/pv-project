# SCADA layer (post-paper work)

Branch `scada-prep`. This is preparation for the project **after** the paper; nothing here
is used by the paper, and nothing in `firmware/` or `analysis/` is changed by it.

The SCADA layer only **reads what the logger already writes**: the day files
`/YYYYMMDD.csv`, `/nodate.csv`, `/markers.csv`, and the serial output. It never sends
anything to the board.

| Document | Content |
|---|---|
| [architecture.md](architecture.md) | the parts, the data flow, the two design choices (SQLite, light web app), the alarm life cycle, known limits |
| [tag-list.md](tag-list.md) | generated: tags, units, ranges, status bits, alarm bits, alarm definitions |
| [roadmap.md](roadmap.md) | what comes later (MQTT from the ESP32, remote access, the cooling actuator), all marked FUTURE |

## How to run it (Windows, from the project folder)

No new packages are needed: the stack uses the Python standard library (`sqlite3`,
`http.server`) plus what `requirements.txt` already pins. `pyserial` is used only with
`--serial`.

### 1. Demo on synthetic data (works today)

Build the demo data set once. It needs the synthetic run of the paper pipeline
(`out/paper_demo/seed5`, made by `python -m analysis.demo_synth`):

```
.venv\Scripts\python -m scada.demo_data
```

This writes `out/scada/demo_source/`: two synthetic days in the logger's file format,
with the `p` and `alarm` columns filled by the pipeline's three detectors and a small
**injected** fault scenario on the first day (a T3 dropout, an irradiance dropout, 60 s
without RTC time, one SD retry, a panel INA226 dropout).

Start the stack in replay mode:

```
.venv\Scripts\python -m scada.run --replay --speed 120
```

Open <http://127.0.0.1:8050>. At speed 120 one logged day (12 h) takes 6 minutes; use
`--speed 600` for about one minute per day. The night between two days is skipped.
The page shows a yellow **REPLAY** banner the whole time. Stop with `Ctrl+C`.

Any folder of logger files can be replayed: `--replay D:\some_sd_copy`.

### 2. Live, from a copy of the SD card

```
.venv\Scripts\python -m scada.run --folder D:\sd_copy
```

The folder is polled every second. Files may still be growing; only complete lines are
read, and each file is continued from where the last run stopped.

### 3. Live, from the logger's serial port (not tested on the board yet)

```
.venv\Scripts\python -m scada.run --folder D:\sd_copy --serial COM5
```

The logger prints every row it writes, so the serial port gives the live view and the
card copy fills in afterwards; a row that arrives twice is stored once. Close the Arduino
serial monitor first: only one program can hold the port.
**To check on the bench:** that opening the port does not reset the ESP32 (the code holds
DTR and RTS low for this reason), and that the logger's commands (`GAMMA`, `LOAD OFF`,
`LOAD ON`) still work from another tool; the SCADA itself never writes to the port.

### Where the data goes

| File | Content |
|---|---|
| `out/scada/historian.sqlite` | the live historian (never emptied by the software) |
| `out/scada/replay.sqlite` | the replay historian, emptied at every replay start |
| `out/scada/replay_live/` | the folder the replay writes into |
| `out/scada/demo_source/` | the demo data set |

`out/` is not in git. Live and replay never share a file: a replay refuses to start on a
live historian and the other way round (`--db` selects another file).

### The page

- **Top bar:** LIVE or REPLAY, time of the newest row (Makkah and UTC), how long ago the
  last row was received, number of unacknowledged and active alarms. After 60 s without
  a new row the `NO_DATA` alarm is raised; in a replay this happens 60 s after the last
  replayed row, which shows the alarm working.
- **Range row:** 1 h to 7 d; "follow newest row", or an end time typed in local time.
- **Tiles:** module temperature (mean of T1..T5), ambient, their difference, irradiance,
  power, load mode, NASIM's probability, the row's status.
- **Charts:** module temperature against ambient (with a tick for every detector alarm),
  irradiance, power, and a strip with one lane each for the load mode, the three
  detectors, the marker button and every status bit. Moving the pointer shows the values
  of that moment in all charts.
- **Sensor health:** one line per sensor from the status bits of the newest row. For a
  file written before 2026-11-12 it says "unknown", not "OK".
- **Alarms:** the alarm list (active or not yet acknowledged) with an Acknowledge button,
  the history of all occurrences, and the journal of every event. Acknowledging needs the
  operator's name.
- **Table view:** the same rows as numbers.

### Tests

```
.venv\Scripts\python -m pytest tests/test_scada_ingest.py tests/test_scada_alarms.py tests/test_scada_stack.py
```

### Problems

| Message | Cause |
|---|---|
| `... is in use: another scada.run is still running` | stop the other instance (Task Manager: `python.exe`) |
| `... is a 'live' historian, not 'replay': not touched` | by design; give `--db` another file |
| `... is not empty and was not made by scada: not touched` | the replay only empties folders it created itself |
| page says "SCADA server not reachable" | the `scada.run` window was closed |
| port 8050 busy | `--port 8051` |
