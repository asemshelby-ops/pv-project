# SCADA roadmap

Everything in sections 2 to 4 is **FUTURE**: not built, not designed in detail, and not
to be started before the paper is submitted. Each item names what it would change, so the
hard rule of today (logger and load firmware and analysis untouched) is visibly lifted
only where it has to be.

## 1. Next small steps (no firmware change)

These read more of what the logger already writes. None is built yet.

| Step | What it adds | Reads |
|---|---|---|
| Sweep summaries | P_mp, V_oc of the last I-V sweep as tags and a chart; sweep flags (e.g. NOT_SETTLED) as alarms | `/SSYYYYMMDD.csv` |
| Load log | mode changes and the reason of a TRIP in the journal | `/load_log.csv` |
| Logger messages | the `#` lines of the serial stream (device status, period overrun) in a message list | serial |
| No-data alarm | "no row for N minutes" in the alarm list, not only in the top bar | PC clock |
| Alarms for backfilled rows | evaluate old rows that arrive after newer ones, without touching acknowledgements | historian |
| Reference logger | the commercial data logger's export next to the ESP32 values | its export file |
| Start with Windows | a scheduled task that starts `scada.run` at logon | — |
| Backup | copy `historian.sqlite` daily | — |

First with real data: check the serial input on the board (README, section 3), replace
the display ranges by ranges from measurements, and review the alarm priorities and the
3-row clear delay against real fault patterns.

## 2. FUTURE: MQTT from the ESP32

**Why.** Today a live view needs a USB cable to the logger, and the card is copied by
hand. With MQTT the logger would publish each row over Wi-Fi and any number of clients
could listen.

**What it changes.** The logger firmware (Wi-Fi, an MQTT client, a new failure mode to
handle). This is the first step that breaks today's hard rule, so it belongs after the
paper and needs its own bench tests.

**Sketch, to be designed:**

- A broker on the lab PC (for example Mosquitto).
- The logger publishes the **same row text** it writes to the card, one message per row,
  on a topic such as `pvrig/<device>/row`; markers and `#` messages on their own topics.
  Keeping the row text means `RowParser` is reused as it is: MQTT becomes a third input
  next to the folder and serial.
- The SD card stays the record of truth. Wi-Fi drops; a row that was not published is
  still on the card, and the historian already ignores a row it has (same UTC second).
- A retained "last will" message from the logger gives the no-data alarm for free.

**Open questions:** Wi-Fi reach at the rooftop stand; whether Wi-Fi activity disturbs the
10 s schedule, the 20 kHz load PWM or the INA226 readings (to measure, not assume);
flash and RAM left on the board (logger uses 35 % flash, 9 % RAM today,
`docs/paper/07` sec. 7); credentials not in git.

**When InfluxDB / Grafana would be reconsidered:** several devices, or rates well above
one row per 10 s. One logger does not justify it (architecture.md, sections 5 and 6).

## 3. FUTURE: remote access

**Why.** The supervisor and the team want to look at the rig without being at the lab PC.

**Today.** The server listens on `127.0.0.1` only and has no login. It must not simply be
opened to the network: anyone who reaches it could acknowledge alarms.

**Options, to be decided with the university's IT rules:**

| Option | How | Note |
|---|---|---|
| A. VPN to the lab PC | a private network (for example WireGuard or Tailscale), page unchanged | least code; depends on what the campus network allows |
| B. Reverse proxy with login | a web server in front, HTTPS and passwords | needs a host name and a certificate |
| C. Read-only copy | the historian file is copied to another machine that serves a view without acknowledge | no way in to the lab PC at all |

Needed in every case before it is used: user accounts with two roles (viewer, operator),
the acknowledging user taken from the login instead of a typed name, and HTTPS.

## 4. FUTURE: the cooling actuator

**Why.** The project after the paper adds the spray loop: a PWM-driven pump, the
controller's decision rule on the ESP32, and water temperature and pump measurements
(CLAUDE.md, "Cooling" and the proposed CSV columns `pump_V`, `pump_I`, `pump_P`,
`pump_duty`, `flow`, `T_water`).

**Principle.** The ESP32 stays the controller. Detection and the decision to spray run on
the board (TinyML on the ESP32 is the supervisor's requirement) and must keep working
with the PC switched off. The SCADA becomes supervisory: it shows, records, and may send
a small set of commands that the board is free to refuse.

**Monitoring first (no command path).** When the firmware logs the pump columns, they
become tags through `data/schema.py` as the others did; the page gets a pump lane in the
state strip and tiles for duty, pump power and water temperature. Candidate alarms, to be
defined from bench tests and not before: pump drawing no current while commanded on
(dry run or open circuit), water too warm for the decision rule to spray, tank level low.

**Commands later, and only with these in place:**

1. Interlocks on the board, not in the SCADA: maximum burst length, minimum pause,
   no spray when the water is warmer than the module allows, pump off on any fault. A
   command from the PC can ask; the board decides.
2. A command path that is authenticated and written to the journal (who, when, what, the
   board's answer). This is why remote access (section 3) with real user accounts comes
   first.
3. A small command set: enable / disable automatic cooling, one manual burst for tests,
   change of a setpoint within limits fixed in the firmware.
4. A defined behaviour when the PC or the link is gone: the board carries on by itself.

**Research value.** The journal of pump bursts together with the historian gives, per
burst, the temperature drop, the energy recovered and the pump energy: the numbers the
strategy comparison of Phase 3P needs (no control / temperature-rise rule / AI with the
net-power rule / fixed threshold).

**What this changes.** Logger firmware (pump driver, new columns, a command parser beyond
today's `GAMMA` / `LOAD OFF` / `LOAD ON`), `data/schema.py`, and the SCADA server (the
first write path to the rig). Each needs its own review; none is started.
