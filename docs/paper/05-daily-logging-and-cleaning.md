# Daily logging and cleaning protocol

Status: first version, 2026-11-11. Applies from the first outdoor day. Times on the log
sheet are **local time read from a phone**; the logger and `/markers.csv` are in UTC
(local − 3 h). The analysis joins them through the marker number.

## 1. Why cleaning is in the protocol

Dust lowers the power and changes how the glass heats, and its drift over weeks is far
larger than the 1–2 % effect of a spike (literature doc §3.1). A module cleaned at a
known time every day keeps soiling out of the comparison between days. Cleaning also
cools the glass, so the minutes after it are excluded from the spike statistics.

Schedule `[ASSUMPTION]`: the module glass **and** the PYR20 dome, **once every day**,
before 07:30 local, plus nothing extra after a dust storm (the next morning's cleaning is
the answer to it). Daily is a choice for a dusty site, not a measured need; §9 gives the
check that can show later whether it is more than needed. Soiling itself is not studied.

## 2. Every morning (before 07:30 local, sun still low)

1. Look at the rig: stand, tilt, cables, sensor covers, PYR20 dome, load heatsink. Note
   anything that changed.
2. Check the logger: the status line shows every device `ok`; the last row's time is
   current; the SD file of today exists.
3. Press the **marker** button. Write its time and "cleaning start" on the log sheet.
4. Clean the module glass with distilled or RO water and a clean microfibre cloth: wet,
   wipe top to bottom, dry with a second cloth. No detergent, no abrasive, no pressure
   washer `[ASSUMPTION: no manufacturer cleaning guidance is available for this module]`.
   Do not wet the back sheet or the sensors.
5. Wipe the PYR20 dome with a damp cloth, then dry.
6. Press the marker again: "cleaning end".
7. Write the sky condition and anything unusual (dust storm overnight, dew, birds).

Data from the cleaning start until 30 min after the cleaning end is excluded from spike
statistics and detector scoring (`03-spike-definition-and-metrics.md` §4).

If a day is skipped, write "not cleaned" on that day's sheet. Never leave it blank.

**Dust storm.** When a dust storm or a visible dust film is noticed (during the day, or
in the morning for the night before): press the marker and write a line with the event
`dust storm` (notes: when it started, how it looks). Do **not** clean at once in the
middle of the day; clean as usual the next morning. The analysis flags that day and every
day after it up to the next logged cleaning (§9).

## 3. During the day

- Every deliberate action near the rig gets a marker press and a log-sheet line: a test
  starting or ending, touching a cable, a person standing in front of the module.
- One marker press = one line on the sheet. The marker number is counted from the first
  press of the day (1, 2, 3 …). No press-count coding: the meaning is on the sheet.
- If the button was pressed by mistake, still write the line: "accidental".

## 4. Controlled tests

Purpose: spikes of known cause, because natural ones may be rare in clear weather.
Between 09:00 and 15:00 local, on a day without natural clouds if possible. Leave at
least 45 min between the end of one test and the start of the next (about 7 thermal time
constants), so the module is back in equilibrium.

| Test | Procedure | Expected `[EST, CLAUDE.md]` | Marker lines |
|---|---|---|---|
| Mesh (irradiance) | ≈ 20 % shade mesh over the module **and** the PYR20, held ≥ 10 cm above the glass, 20 min; then remove in one movement | after removal: ≈ 3 °C in 6 min, ≈ 4 °C in 10 min | "mesh on", "mesh off" |
| Windbreak (non-irradiance) | board upwind of the module on a breezy day, not shading the module or the PYR20, 20 min; then remove | a rise while it is up, at flat irradiance; size depends on the wind | "windbreak on", "windbreak off" |
| Negative control | shade the module for < 1 min with a board, or wave a hand over the PYR20 | no spike, no alarm | "negative control" |
| γ run | `02-gamma-from-iv.md` §2 (opaque board, 25 min, then uncover) | ≈ 20 °C rise; not a spike test | "gamma shade on", "gamma uncover", "gamma end" |

Rules: write the wind as felt (calm / light / breezy) for every windbreak test, since
there is no anemometer. Never use an opaque cover for a spike test. Measure the mesh's
real attenuation once from the PYR20 reading (before / under the mesh) and record it.

Planning number: ≥ 50 spikes for training and ≥ 20 held out (PROJECT_STATE §3). At 3–4
tests a day this takes about four weeks of test days, spread over the campaign so that
train, validation and test days all contain tests.

## 5. Every evening (after 18:00 local)

1. Read the serial status or the last SD row: logger still running, no device missing.
2. Photograph the day's log sheet. File name: `logsheet_YYYY-MM-DD.jpg`.
3. If the SD card is taken out: power the logger off first, copy the day files and
   `/markers.csv`, put the card back, power on, check the status line.

## 6. Every week

| Check | How | Record |
|---|---|---|
| Copy the data | Copy all new `/YYYYMMDD.csv`, sweep files and `/markers.csv` to the PC and to a second place. Never edit the copies in `data/raw/`; write a SHA-256 list (`sha256sum * > SHA256SUMS`). | date, number of files |
| RTC offset | With the logger on USB, compare the UTC time in a fresh serial row with the PC's UTC clock (PC synced to internet time). | offset in seconds, to 1 s. Re-set the RTC with `set_rtc.py` only if the offset exceeds 5 s, and write the time of the re-set |
| Row count | Each full day should have ≈ 4320 rows between 06:00 and 18:00 local. | days below 95 % |
| Tilt and azimuth | Inclinometer on the module frame and on the PYR20 bracket; compass app corrected to true south. | degrees; flag any change above 1° |
| Sensors | Tape and covers of T1..T5 still tight; BME280 shield clean; PYR20 level with the module. | photo if anything was fixed |
| Load (once fitted) | Heatsink and resistor connections, no discoloration; cable lugs tight. | — |

## 7. Day validity (decided by the analysis script, not by hand)

A day enters the statistics when: the cleaning was logged; ≥ 95 % of rows are present
between 06:00 and 18:00 local; no sensor is missing for more than 5 min; the log sheet
exists. Days that fail are listed in the paper with the reason.

## 8. Log-sheet template (one page per day — paper, or the same columns in a phone note)

```
PV rig daily log                      Date (local): ____-__-__     Observer: __________

Morning check   time: __:__    all devices ok?  Y / N     notes: ____________________
Cleaning        start __:__  (marker #__)   end __:__  (marker #__)   or  NOT CLEANED
Sky at start    clear / haze / dust / scattered cloud / overcast
Wind as felt    calm / light / breezy

 #  | local time | event type                                   | notes
----+------------+----------------------------------------------+---------------------------
 1  |   __:__    | cleaning start                               |
 2  |   __:__    | cleaning end                                 |
 3  |   __:__    |                                              |
 4  |   __:__    |                                              |
 5  |   __:__    |                                              |
 6  |   __:__    |                                              |
 7  |   __:__    |                                              |
 8  |   __:__    |                                              |

Event types: cleaning start / cleaning end / dust storm / mesh on / mesh off /
windbreak on / windbreak off / negative control / gamma shade on / gamma uncover /
gamma end / sync event / maintenance / accidental / other (describe)

Sky during the day (clouds? dust? when?): ____________________________________________
Dust storm today or last night?  N / Y -> marker #__ , line "dust storm" above
PYR20 dome cleaned with the module?  Y / N
Evening check   time: __:__    logger running?  Y / N     SD copied?  Y / N
Anything unusual: _____________________________________________________________________
```

The `#` column is the marker number of the day: the first press is 1. The analysis script
reads `/markers.csv`, numbers the presses of each local day in order, and joins them to
the typed copy of this sheet (`data/logsheets/YYYY-MM-DD.csv`, columns
`marker,local_time,event,notes`). A mismatch between the number of presses and the number
of lines is reported, not guessed.

## 9. Soiling: what the analysis does with these lines (a check, not a result)

Added 2026-11-13. Code: `analysis/soiling.py`; constants `SOIL_*` in `config.py`, all
`[ASSUMPTION]`.

| What | Rule | Output |
|---|---|---|
| Cleaning | every `cleaning start` … `cleaning end` pair, as before: excluded until 30 min after the end | `events.csv`, detector scoring |
| Days after a dust storm | the local day of each `dust storm` line and every following day up to and including the day of the next `cleaning end` (its morning was still dusty). No cleaning since: flagged to the end of the data | column `after_dust` in `day_quality.csv`; a note in `summary.md`. **Flagged, not excluded** |
| Power before and after a cleaning | for each cleaning: median of `power / irradiance` between 11:30 and 12:30 local, on the last day before the cleaning and on the first day after it; rows with G ≥ 600 W/m², power > 1 W, load not off / tripped / sweeping; at least 60 rows each. `change` = after / before − 1; above 3 % it is marked `check` | `soiling_check.csv`; the count in `summary.md` |

How to read the check, and what it cannot show:
- It needs the load. Today (no load, module at open circuit) every line reads "no power".
- The dome and the module are cleaned together, so `power / irradiance` shows dust on the
  module **minus** dust on the dome. Equal dust on both cancels and the check sees nothing.
- With a cleaning every morning the "before" midday is normally a clean day too, so the
  change is one day of dust: expect ≈ 0. A storm that comes in the afternoon and is
  cleaned off the next morning never shows either. The check mainly catches a **skipped**
  cleaning and a slow problem (a dome that is not being wiped, a film that does not
  come off).
- Not corrected for temperature (γ of this module is unknown): `dT_module_C` is given
  next to each line; 5 °C between the two middays is ≈ 2 % by itself `[EST, illustrative
  γ]`.
- A day marked `after_dust` or a cleaning marked `check` is a reason to look at that day,
  nothing more. Whether flagged days leave the statistics is a decision for Asim, not made
  here.
