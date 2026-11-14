# SCADA layer (post-paper work)

## For teammates (Windows, no git needed)

1. **Python 3.12 or newer, 64-bit.** Download the "Windows installer (64-bit)" from
   <https://www.python.org/downloads/windows/>. On the installer's first screen tick
   **"Add python.exe to PATH"**. (3.10 and 3.11 do not work: the pinned packages have no
   Windows versions for them.)
2. **Get the project.** On the GitHub page of the repository choose branch `twin-sim`, then
   **Code → Download ZIP** (you must be signed in with an account that has access). Right-click
   the ZIP → **Extract All...**; you get a folder like `pv-project-twin-sim`. Do not run
   anything from inside the ZIP.
3. **Double-click `setup-windows.bat`** in that folder. It checks Python, creates `.venv`,
   installs `requirements.txt` (needs internet, several minutes) and puts **PV SCADA** on
   your Desktop. If Windows says "Windows protected your PC", click *More info → Run anyway*
   (it does that for any file downloaded from the internet).
4. **Double-click PV SCADA** on the Desktop. The first start asks to build the demo data:
   answer `Y` and wait a few minutes. The dashboard opens in the browser; close the black
   window to stop it. The demo data are SYNTHETIC and the rooftop page is a SIMULATION.

To update: download and extract a new ZIP and run `setup-windows.bat` there; the Desktop
shortcut then points to the new folder (copy `scada-live.cfg` across if you made one).
If something fails, the window says why and what to do; the table under
[Problems](#problems) covers the SCADA itself.

Branch `scada-prep`. This is preparation for the project **after** the paper; nothing here
is used by the paper, and nothing in `firmware/` or `analysis/` is changed by it.

The SCADA layer only **reads what the logger already writes**: the day files
`/YYYYMMDD.csv`, `/nodate.csv`, `/markers.csv`, and the serial output. It never sends
anything to the board.

| Document | Content |
|---|---|
| [architecture.md](architecture.md) | the parts, the data flow, the two design choices (SQLite, light web app), the alarm life cycle, known limits |
| [tag-list.md](tag-list.md) | generated: tags, units, ranges, status bits, alarm bits, alarm definitions |
| [telegram.md](telegram.md) | phone alerts through a Telegram bot: what is sent, rate limit, daily heartbeat, BotFather setup, chat ID |
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

### 4. The rooftop twin: a SIMULATION, on its own page

**Everything in one server (Windows):** double-click `start-scada.bat` in the project folder
(`make-desktop-shortcuts.bat` puts a "PV SCADA" shortcut on the Desktop; `setup-windows.bat` does
the whole first-time setup, see "For teammates" above). It runs
`scada.run <real-module source> --sim`: the real module's source and this simulation in one
process, still each with its own file and API. The real-module source is LIVE when a file
`scada-live.cfg` exists in the project folder (one line of arguments, e.g.
`--folder D:\sd_copy --serial COM5`; ignored by git), else the replay demo at 600x (built
once if missing, after asking). It switches to `twin-sim` only when no tracked file has
uncommitted changes (otherwise it warns and starts nothing; a "Download ZIP" copy without
`.git` skips git entirely), opens the dashboard once the
server answers, and keeps its window open: close the window to stop. A bar at the top of both
pages switches between "Real module · dashboard" and "Rooftop twin (SIMULATION)".

```
.venv\Scripts\python -m scada.run --sim                  only the simulation
.venv\Scripts\python -m scada.run --replay --sim nasim   next to a replay (or to live data)
```

Open <http://127.0.0.1:8050/rooftop>. The page shows the digital twin of `twin/`: 120
simulated modules in 6 strings, one spray zone and valve per string, a shared pump and
tank, run by one controller (`predictive`, `nasim`, `threshold`, `continuous` or `none`).
`--sim-speed` sets the speed (default 120: one simulated day in 6 minutes) and
`--sim-days` the number of days (default 2). `nasim` needs the model of the paper
pipeline's synthetic run (`python -m analysis.demo_synth`).

**Nothing on that page is measured.** The real module stays the only real data source:

- the simulation has its own file, `out/scada/simulation.sqlite`, emptied at every start.
  It never opens the live or the replay historian and never writes a row to `samples`;
- its API is separate (`/api/sim/...`) and every answer carries the label;
- its alarm codes all start with `SIM_` and every alarm text with `SIMULATION:`; they are
  listed and acknowledged on the rooftop page only;
- the page has an orange `SIMULATION` badge and banner at all times, and the time shown
  is the simulated time of the weather day, not the clock.

On the page: a 3D view of the rooftop (below); tiles (irradiance, string power against the same rooftop without cooling,
pump and valve power, net energy and water of the simulated day, tank temperature); one
row per string with its valve, its 20 modules coloured by temperature (a dot marks the
five modules that carry the string's sensors), sensor temperature, power, voltage and
current; charts of string temperature, power, and valves and detector alarms per zone;
the simulation's alarm list, history and journal. The spike alarms come from the
controller's detector, or, for controllers without one, from a detector that only watches.
Model, assumptions and placeholders: `docs/twin/`.

**3D view** (`scada/web/rooftop3d.js`, Three.js 0.160.0 vendored in
`scada/web/vendor/three-0.160.0/`, so it works without internet). A flat roof with the 6
strings of 20 modules tilted 20 deg facing south, the pump and tank, the manifold, and one
branch, valve and spray pipe per string. The layout is a drawing, not a plant design: our
module's size (1200 x 540 mm, portrait), rows 2.6 m apart.

- **Two module looks** (buttons on the view, remembered by the browser): **Heat map**, every
  module in the shared colour scale of the page (20 to 70 degC, the same legend as the string
  table, unlit so a colour always means the same temperature), and **Realistic**: dark blue
  cells with busbars, an aluminium frame and sky reflections on the glass. The cell grid is an
  illustration (4 x 9): our module's cell count is not known. In the realistic look the
  temperatures are on a click and in the table; both follow the simulation once a second.
- **Scene (drawing only):** a concrete roof with a parapet and coping, an access stair house
  with its door, three AC units, the building and the ground. The sun stands where it stood
  over Makkah at the SIMULATED time of the newest step (low-precision almanac, within 1 deg of
  pvlib; checked by the test), dimmed when the simulated irradiance drops; the sky gradient,
  the reflections and the shadows follow it, and at night the scene is dark.
- **Spray:** a string whose valve is open shows its valve lit in the valve colour, droplets
  that glint in the sun, a light mist, and a wet sheen on the glass that dries in about 25 s
  after the valve closes (drawing only, not the model's evaporation). Its label says
  "spraying"; an open alarm adds "▲ spike / hot / bypass".
- **Camera:** a short intro flight on opening (skipped with `?intro=0` or when the system
  asks for reduced motion; any drag stops it), **Presentation** (a three-quarter view of the
  roof with our real module in the frame) and **Overview**. Drag to orbit, scroll to zoom,
  right-drag to pan. Clicking a module shows its temperature, sensor, soiling and wind factor
  and its string's values; a string's label, valve or pipe shows the string; the pump or tank
  shows pump, flow and water.
- A `SIMULATION` badge sits on the view at all times; every value in the side panel is
  tagged SIMULATED.
- **Our real module** stands apart, on its own stand on the ground south-west of the
  building, with a dark tag "the only measured one". It is the one object that reads
  `/api/state` (the real module's newest row, read-only); in the heat map it is coloured by
  the measured module temperature (mean of T1..T5) on the same scale, grey when no row
  exists. Clicking it shows the measured values, LIVE or "REPLAY ...: not live measurements"
  as on the module page. Nothing of it enters the simulation, and no simulated value is drawn
  on it.
- **Speed:** three quality tiers, chosen in the `quality` list or with `?quality=`:

  | Tier | Pixel ratio (max) | Shadows | Droplets per string |
  |---|---|---|---|
  | high | 2 | soft, 2048 px map | 360 |
  | medium | 1.25 | soft, 1024 px map | 220 |
  | low | 1 | none | 110 |

  `auto` (default) starts at medium and measures the frame time between animation frames
  (which includes the graphics card's work) and the time of each draw call. It steps down
  when the median frame is above 45 ms, i.e. below about 22 frames/s (or a draw call above
  22 ms; a steady 30 frames/s, also Chrome's energy-saver cap, stays), and steps up once, at
  the start, only when 90 % of the frames are under 20 ms; the corner shows the tier and the
  frame time. The 120 modules are instanced, all textures are drawn once on canvases (no
  image files, no internet), a frame is drawn only when something changes or moves, about
  30 per second while a zone sprays, and none while the view is off screen or the tab is
  hidden. Without WebGL the view says so and the table shows the same values.

### 5. Phone alerts (Telegram)

With live data (`--folder` / `--serial`) the SCADA sends a Telegram message when an alarm
of priority 1 or 2 is raised or cleared (at most one per alarm code every 30 min, grouped
into one message), and a heartbeat every day at 08:00 Makkah time. Never for a replay or
the simulation. It needs `scada/telegram.local.json` (git-ignored); without it alerts are
off. Setup with BotFather and the chat ID: [telegram.md](telegram.md).

```
.venv\Scripts\python -m scada.notify --test
```

### 6. Open the page from teammates' laptops (`--lan`, trusted network only)

By default the server listens on `127.0.0.1`: only the PC that runs it can open the page.
Add `--lan` to any start command to listen on the local network instead:

```
.venv\Scripts\python -m scada.run --replay --speed 120 --sim --lan
```

It prints the address to give teammates on the same Wi-Fi, e.g.
`Teammates on this network open: http://192.168.1.23:8050`, and a warning: **there is no
login**. Anyone who can reach the PC can read the data and acknowledge alarms (the page
cannot reach the logger or change anything else). Use it only on a network you trust (the
lab, a phone hotspot), never on public or university-wide Wi-Fi. Windows asks once whether
Python may accept connections: allow it for **Private** networks only. If teammates still
cannot connect, the Wi-Fi may keep devices apart (common on guest networks): use a phone
hotspot. Real remote access with accounts is on the [roadmap](roadmap.md#3-future-remote-access).

### Where the data goes

| File | Content |
|---|---|
| `out/scada/historian.sqlite` | the live historian (never emptied by the software) |
| `out/scada/replay.sqlite` | the replay historian, emptied at every replay start |
| `out/scada/replay_live/` | the folder the replay writes into |
| `out/scada/demo_source/` | the demo data set |
| `out/scada/simulation.sqlite` | the rooftop SIMULATION, emptied at every start; never real data |
| `out/scada/historian.telegram.json` | the phone alerts' state: last journal event read, pending events, last message per code, last heartbeat |

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
.venv\Scripts\python -m pytest tests/test_scada_ingest.py tests/test_scada_alarms.py tests/test_scada_stack.py tests/test_scada_sim.py tests/test_scada_rooftop3d.py tests/test_scada_together.py tests/test_scada_notify.py
```

`test_scada_rooftop3d.py` also loads the page in headless Chromium (load, live updates,
colours, spray, clicks, real module, no drawing off screen). That part needs Node.js with
Playwright (in the repository folder: `npm install --no-save playwright`, then `npx playwright install chromium`); without them it
is skipped and says why.

### Problems

| Message | Cause |
|---|---|
| `... is in use: another scada.run is still running` | stop the other instance (Task Manager: `python.exe`) |
| `... is a 'live' historian, not 'replay': not touched` | by design; give `--db` another file |
| `... is not empty and was not made by scada: not touched` | the replay only empties folders it created itself |
| page says "SCADA server not reachable" | the `scada.run` window was closed |
| port 8050 busy | `--port 8051` |
| `... is a 'live' historian, not a simulation file: not touched` | by design: the simulation only empties its own file |
| rooftop page says the simulation is not running | start `scada.run` with `--sim` |
| `Telegram alerts: ...` | see [telegram.md](telegram.md), section 4; `--no-telegram` starts without alerts |
