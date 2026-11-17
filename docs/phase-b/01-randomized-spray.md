# Phase B, part 1 — randomized spray on a NASIM alarm (firmware)

Status: prepared 2026-11-17, **software only, not adopted**. No pump, nozzle, flow sensor
or tank exists; the code has **never run on a board** and, on the machine where it was
written, could not be compiled for the ESP32 (no toolchain there). It is switched off:
`PHASE_B_ENABLED 0` in `firmware/libraries/PvDaq/src/pv_config.h`.

Files: `pv_spray_logic.c/.h` (every decision, plain C, tested on the PC),
`pv_spray.cpp/.h` (the ESP32 side: PWM, pulse counter, pump INA226, stored counters,
cut-off timer), the `#if PHASE_B_ENABLED` blocks of `firmware/logger/logger.ino`.
Values: `config.py` → `python -m export.phase_b_header` →
`export/c/generated/phase_b_config.h`. Tests: `tests/test_spray_logic.py`.

## 1. Why a randomized trial

"Did the spray help?" cannot be answered by looking before and after one spray: the
module was in the middle of a spike, the sun may be changing, and every cooling is
followed by a re-heating. So the logger itself decides, by chance, whether a spike is
sprayed or left alone. The spikes that were left alone are then the comparison for the
ones that were sprayed, and the only systematic difference between the two groups
("arms") is the water.

## 2. What happens at a NASIM alarm

1. **Is this alarm allowed to open an event?** Decided from the row alone, *before* the
   arm is drawn and never depending on it, so both arms hold the same kind of events.
   Reasons for "no" (each is written to `/spray_log.csv` as `refused`):

   | Reason | Meaning |
   |---|---|
   | `event open` | an event is already open: the alarm belongs to it |
   | `disarmed` | switched off with `SPRAY OFF` |
   | `lockout` | less than `PHASE_B_LOCKOUT_MIN` since the last window closed |
   | `fault latched` | a pump fault ended the last spray; needs `SPRAY RESET` |
   | `sensor` | a sensor of this row gave no reading (RTC, T1–T5, ambient, irradiance, panel) |
   | `storage` | the row before did not reach the card, or the event counter cannot be stored |
   | `late` | the window would run past the logged day (after 18:00 local) |
   | `low light` | irradiance below `SPRAY_MIN_G_WM2`, or unknown |
   | `load` | a load is fitted and is not holding the module: no power to compare |
   | `pump sensor`, `tank sensor`, `water warm`, `thermal shock` | wet runs only: no pump-rail reading; no tank temperature; `T_module − T_water` below `SPRAY_MIN_DT_WATER_C`; above `SPRAY_MAX_DT_WATER_C` |
   | `bad config` | the values of `phase_b_config.h` cannot be used |

2. **The event gets its number.** The counter is first stored in the ESP32's flash (NVS,
   namespace `phaseb`); only then does the event open. A restart therefore never gives
   the same number twice, and a number that was stored but lost in a crash shows as a gap
   (the analysis reports gaps).
3. **The arm is drawn**: spray (`1`) or no-spray (`0`), from the seeded sequence (§3).
4. **Spray arm:** the pump runs at `PUMP_DUTY` until a stop condition (§4).
   **No-spray arm:** nothing is switched.
5. **Both arms:** the event stays open for the same window, `PHASE_B_EVENT_WINDOW_MIN`
   (30 min) from the alarm row. Every row of the window carries the event's `spike_id` and
   `spray_assign`. Then a lock-out of `PHASE_B_LOCKOUT_MIN` (10 min), so that the next
   event's 10-minute baseline lies outside this window.

States: `idle` → (`spray` →) `window` → `lockout` → `idle`. Window and lock-out are
counted in 10 s rows, so an event always starts exactly on a row and has 180 rows.

## 3. The random sequence: blocks of four, one seed

- Events are taken in **blocks of 4**; inside every block exactly 2 are sprayed and 2 are
  not, in a random order. After any number of events the arms differ by at most 2. With
  plain coin tosses and 12 events, a split of 9 against 3 or worse would happen about once
  in seven trials; with blocks it cannot.
- The order inside block *b* comes from a fixed function of (`PHASE_B_SEED`, *b*): a
  32-bit integer hash ("lowbias32") drives a Fisher–Yates shuffle, with rejection sampling
  so that no order is favoured. No random-number state is kept, so nothing can be lost in
  a restart: `spray_alloc(seed, block, n)` gives the arm of event *n*, always.
- The same function exists in Python (`analysis.phase_b.allocation`); a test compares
  the two over thousands of events. The analysis recomputes every arm from the seed and
  the event number and lists any event whose logged arm differs.
- **The seed is logged at every boot** (first line of `/spray_log.csv` and on the serial
  port), together with the block size, both event counters, dry or wet run, the duty and
  the times.
- **Dry run** (`PHASE_B_DRY_RUN 1`, the default): events are opened, numbered and drawn
  exactly as in a wet run, but with their own sequence and **negative** numbers
  (−1, −2, …), and the pump output is never attached. It is for commissioning, for
  measuring the spread between events (`docs/paper/03` §12), and as an A/A check: with no
  water, the two arms must not differ.

A known weakness of blocks: whoever knows the first three arms of a block knows the
fourth. Here nobody chooses when a spike comes, so this matters little; it is stated for
completeness.

## 4. When a spray stops

| Reason | Checked | Latches? |
|---|---|---|
| `max time`: `SPRAY_MAX_S` reached | every loop pass | no (normal end) |
| `target`: cooled by `SPRAY_STOP_DROP_C` since the alarm (0 = not used) | every row | no (normal end) |
| `water warm`: `T_module − T_water` fell below `SPRAY_MIN_DT_WATER_C` | every row | no (normal end) |
| `sensor`: a sensor of the row, or the tank sensor, gave no reading | every row | no |
| `no flow`: pump on, no flow pulse for `PUMP_NO_FLOW_S` | every loop pass | **yes** |
| `overcurrent`: pump current above `PUMP_I_MAX_A` | every pump reading (250 ms) | **yes** |
| `pump sensor`: the pump INA226 failed several times in a row | every pump reading | no |
| `command`: `SPRAY OFF` typed during the spray | at once | no |
| `hard timer`: the independent timer fired (§5) | — | **yes** |

A latched fault opens no further event until someone has looked at the rig and typed
`SPRAY RESET`. After any stop the event's window still runs to its end: the event is
analysed in the arm it was drawn for ("as assigned"), and also listed as not delivered if
no water flowed.

With `SPRAY_STOP_DROP_C = 0` every spray has the same length, which keeps the trial
simple: one dose. A temperature target makes the dose depend on the event.

## 5. Pump off at boot, on reset and on any fault

| Situation | What keeps the pump off | Kind |
|---|---|---|
| Power-up, before the firmware runs | 10 kΩ pull-down at the MOSFET gate (GPIO32 is high-impedance during reset, `PINMAP.md`) | hardware: **to be fitted and checked on the bench** |
| Boot | `pvOutputsOff()` drives the pin LOW as the first action; the PWM is attached at duty 0 | firmware |
| Any reset (watchdog, brown-out, crash) | pin released → pull-down; then the boot sequence; the logic starts in `idle` with duty 0; an event that was open is not continued | hardware + firmware |
| Any sensor fault | no event opens (`sensor`, `pump sensor`, `tank sensor`); a running spray stops | firmware, tested on the PC |
| Loop late or stuck | an `esp_timer` one-shot, armed when the pump starts, switches the PWM off 3 s after the longest spray should have ended, whatever the main loop is doing; the loop watchdog resets the chip if the loop stalls | firmware, **not run on a board** |
| Start-up of a part failed (PWM, timer, pulse counter, NVS) | the trial is disarmed; the boot line says which part | firmware |
| Dry run | the PWM is never attached; the pin stays LOW for the whole run | firmware |
| Values still `[PLACEHOLDER]` | a wet build stops with a compiler error until `PHASE_B_PLACEHOLDERS_CONFIRMED` is set | build |

"Any sensor fault" was taken literally for the sensors the decision needs: the row's
sensors (RTC time, all five back-sheet sensors, ambient, irradiance, panel V/I), the pump
INA226 and the tank sensor. One failed back-sheet sensor out of five therefore blocks and
stops spraying. That is the cautious reading; whether it is too strict for clone DS18B20s
is a question for the first weeks of dry run (the `refused` lines will show how often it
happens).

No I-V sweep is started while the pump runs, and no event opens unless the load holds
the module.

## 6. Pins and channels

| Signal | Pin | Source |
|---|---|---|
| Pump MOSFET gate (PWM) | GPIO32, `pins::PUMP_PWM` | already in `pv_config.h` / `PINMAP.md` since Phase 0 |
| Load branches A, B | GPIO33, GPIO14 | unchanged; a `static_assert` refuses a pump pin equal to either |
| Flow sensor pulses | GPIO35 `[PLACEHOLDER]`, `FLOW_PIN` in `config.py` | one of the spare input-only pins; a `static_assert` refuses any pin already used. A 5 V Hall sensor needs a divider or level shifter; the pin has no internal pull-up |
| Pump PWM generator | LEDC channel 2 | the load uses channels 0 and 1 |
| Pump INA226 | PaHub channel 1, address 0x41 `[PLACEHOLDER]` | `docs/phase-b/02` §1 |
| Tank DS18B20 | on the 1-Wire bus (GPIO4), found by its ROM, `TANK_ROM` `[PLACEHOLDER]` | all zero = not fitted: no wet event opens |

## 7. What is written

Six columns after `status` in the 10 s row, **only** in a build with `PHASE_B_ENABLED 1`:

| Column | Meaning |
|---|---|
| `spike_id` | event the row belongs to; `0` = none; wet run 1, 2, 3 …; dry run −1, −2, … |
| `spray_assign` | `-1` = no event open, `0` = no-spray arm, `1` = spray arm |
| `pump_duty` | mean PWM duty over the 10 s before the row, 0–1 |
| `flow_Lpm`, `water_L_cum`, `P_pump_W` | metering, `docs/phase-b/02` |

Three more bits of `status` (9, 10, 11): `PUMP_FAIL` (no pump power for this row),
`SPRAY_BLOCKED` (an alarm at this row would not open an event), `SPRAY_ABORT` (a spray was
stopped by a fault since the last row).

`/spray_log.csv` — `date,time,uptime_ms,spike_id,event,detail`; events `boot`, `opened`,
`stopped`, `closed`, `refused`, `command`, `fault`; details as `key=value`.

Serial commands: `SPRAY ON`, `SPRAY OFF` (also stops a running spray; kept across
restarts), `SPRAY RESET` (after a latched fault), `SPRAY STATUS`.

A day file begun by an older build keeps its old header line; before the first longer row
goes in, the new header line is appended, and `analysis/ingest.py` reads such a file piece
by piece.

## 8. What is tested, and what is not

Tested on the PC (`tests/test_spray_logic.py`, 43 tests, the C logic through `ctypes`
with the firmware's own configuration): balance of every block; the firmware and the
Python sequence agree; every refusal reason and that it is decided before the arm; every
stop reason; the pump is off after init, after every stop and for the whole of a dry run;
the window has the same number of rows in both arms; counters survive a restart; a failed
store drops the event; `millis()` wrap-around; the row means add up to litres and joules.
`tests/test_firmware_host.py` type-checks `logger.ino` and the glue against small stub
headers in all 16 combinations of the four flags (a syntax check, **not a build**).

Not done, needs the board or the parts:
- compiling for the ESP32 (`python firmware/build_all.py logger`), in particular the
  ESP-IDF calls of `pv_spray.cpp`: pulse counter, NVS, `esp_timer`, LEDC channel 2;
- the gate pull-down and the pin's level through reset, on the scope;
- the cut-off timer and the watchdog with a deliberately blocked loop;
- every `[PLACEHOLDER]`: pump duty and longest spray from the pump curve, pulses per
  litre from a bucket test, over-current limit from the pump's data, the priming time
  behind `PUMP_NO_FLOW_S`, the two water limits, the tank sensor's ROM, the seed;
- a NASIM model trained on field data: the model in the repository is trained on
  synthetic data, and a wet run driven by it would spray on that model's alarms.
