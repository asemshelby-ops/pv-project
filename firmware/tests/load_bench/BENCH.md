# Load bench test — what to do on the day the parts arrive (B0–B9)

Status: written 2026-11-13, **before any load part exists**. The firmware it tests has only
been compiled and run on the PC against a simulated module (`tests/test_load_logic.py`).
Nothing below has been done on hardware yet.

This file is the *how*: wiring, the command to type, what to write down.
The *pass criteria* are in `docs/paper/06-load-bench-test-protocol.md` §3 and are **not
restated or changed here** — the "Pass" column only points to them. Parts list and
stresses: the same document, §1 and §2. Records to keep: its §4.

A failed step is fixed before the next one starts. Stop and type `OFF` whenever something
smells, smokes, hums or reads wrong.

## Before you start

Separate from the load, and needed **before the logger goes outdoors** even with no load
fitted: the INA226 reset check, `firmware/BRINGUP.md` step 4a (seven short checks with a
supply, a resistor and a multimeter).

Safety (from 06 §2): the resistors and the heatsink get hot enough to burn. Electrolytic
capacitors can vent if reversed: check polarity twice. Switch the supply off before
touching the power wiring. Never leave the load running unattended. Safety glasses on.
10 A fuse in the supply lead.

Two firmwares are used:

| Steps | Firmware | Build and flash |
|---|---|---|
| B1–B5, B7 (2–4) | `firmware/tests/load_bench` | `python firmware/build_all.py load_bench`, then `arduino-cli upload -p COM3 --input-dir out/firmware_build/load_bench firmware/tests/load_bench` |
| B6, B7 (1), B8, B9 | the logger **with the load switched on** | `python firmware/build_all.py logger_load`, then `arduino-cli upload -p COM3 --input-dir out/firmware_build/logger_load firmware/logger` |

`logger_load` is the normal logger compiled with `HAS_LOAD=1`. The logger flashed today
(`HAS_LOAD 0` in `pv_config.h`) keeps both load pins LOW and is not affected by any of
this. Change `HAS_LOAD` to 1 in `pv_config.h` only after B0–B7 have passed.

Serial monitor: `arduino-cli monitor -p COM3 -c baudrate=115200`, line ending "Newline".
The bench sketch prints its command list for any unknown command. **`OFF` always switches
both branches off.**

Bench sketch commands:

| Command | Does |
|---|---|
| `OFF` | both branches off |
| `D 0.25` | manual: branch A at 25 %, branch B off |
| `AB 0.25` | manual: both branches at 25 % |
| `STEPS` / `STEPS AB` | manual staircase 0, 0.05 … 1.00, 2 s per step, one CSV line per step |
| `SETTLE 0.2 0.4` / `… AB` | step from the first duty to the second, print V for ≈ 50 ms |
| `AUTO` | the firmware logic as in the logger (OFF → HOLD, a sweep every 60 s) |
| `HOLD 18.0` | the logic holds 18.0 V; a sweep only when `SWEEP` is typed |
| `SWEEP` | one I-V sweep now; prints every point as CSV |
| `GAMMA 5` | a sweep every 15 s for 5 min |
| `R` | one 64-average reading |

On the bench there is no pyranometer: the sketch tells the logic G = 1000 W/m².
The heatsink sensor is the **first DS18B20 on the 1-Wire bus** — connect only that one
to GPIO4 (with the 4.7 kΩ pull-up). The sketch prints its ROM at start: copy it into
`HEATSINK_ROM` in `logger/logger.ino`. Without the sensor, `AUTO` and `HOLD` trip at once
("heatsink sensor missing"); that is the designed behaviour, not a fault.

Wiring for B2 onwards (06 §1, BRINGUP §4): supply + → 10 A fuse → INA226 unit IN+ … IN− →
capacitor bank + → both resistor banks → each bank's MOSFET drain; sources to supply −.
The INA226 unit sits **between the supply and the capacitor**, never after the capacitor.
Gate driver inputs: GPIO33 (branch A), GPIO14 (branch B), each with its 10 kΩ pull-down
at the driver. Driver supply from the DAQ side, not from the PV line.

## Steps

| Step | Set up | Do | Write down | Pass |
|---|---|---|---|---|
| **B0** Parts check | No power. Multimeter, datasheets. | Measure each resistor and each assembled bank (4-wire, or 2-wire minus the lead resistance). Read from the datasheets: MOSFET pulse current, capacitor ripple-current rating at 20 kHz, the resistor derating curve at 50 °C ambient, bank B pulse rating. | Bank A Ω, bank B Ω; the four datasheet numbers with page; the capacitor bank's total µF (the simulation assumed 2000 µF). | 06 §3 row B0 |
| **B1** Gate drive, no power | Power stage **unpowered** (no supply connected). Scope (or multimeter on DC) on gate A and gate B. Flash `load_bench`. | `D 0`, `D 0.25`, `D 0.5`, `D 1` → gate A follows, gate B stays LOW. `AB 0.25`, `AB 0.5`, `AB 1` → both gates the same. `OFF`. Then scope on **driver input and gate of branch B (GPIO14)**, single-shot trigger: power-cycle the ESP32 5 times, press reset 5 times, re-flash once. Repeat on branch A. | Frequency; duty read on the scope at each setting; height and length of any pulse at the GPIO14 driver input; whether anything reaches a gate. | 06 §3 row B1 |
| **B2** Low power, meter check | Supply 12 V, current limit 2 A. Multimeter as ammeter in the supply lead; second multimeter for V at the supply terminals and at the unit. | Type `D x` and raise x until the current is ≈ 0.5, 1, 1.5, 2 A (about `D 0.06`, `0.125`, `0.19`, `0.25` `[EST]`: I = 12·D/1.5). At each point type `R`. `OFF`. | Per point: INA226 V and I (the `R` line), meter V at the supply, meter V at the unit, meter I. Which terminal the unit senses V_BUS on (open TODO in BRINGUP §4). | 06 §3 row B2 |
| **B3** Supply as a crude module | Supply 21 V, limit 5.9 A (short tests). | `STEPS` (branch A alone), then `STEPS AB`. Copy both tables. With a clamp meter or the voltage across each bank during `AB 0.3`: current of each branch. Then `HOLD 12` and watch a minute: the voltage should sit at 12 V (the supply is in current limit there). `OFF`. | The two CSV tables (`D,V,I,R_from_reading`); branch currents; any duty range where the supply hunts between its two modes. | 06 §3 row B3 |
| **B4** Settling time | Supply 21 V, limit 5.9 A. | In the current-limited region: `SETTLE 0.4 0.6 AB` and `SETTLE 0.6 0.4 AB`. Near V_oc: `SETTLE 0.02 0.05 AB`. Near the lowest voltage: `SETTLE 0.8 1.0 AB`. Also `SETTLE 0.5 0.7` (branch A alone, as in hold). Repeat with the limit at 1.2 A (the module in weak sun): the settling is slower there, see the note below. `OFF` after each. | Each printed `t_ms,V` table; for each, the time after which V stays within 0.2 % of the end value. | 06 §3 row B4 → `T_SETTLE_MS` |
| **B5** Heat run | Supply 21 V, limit 4.7 A (≈ 100 W). Heatsink sensor mounted. Thermocouple or IR thermometer. | `HOLD 12` (branch A alone carries the 100 W). Every 5 min for 30 min: resistor body, heatsink, MOSFET A, capacitors, diode. Then `GAMMA 5` and after 5 min read bank B and MOSFET B. Then the trip test: warm the heatsink sensor with hot air until it passes `T_TRIP_C` → the status line must show `trip  D_A 0.0000 … TRIP: heatsink hot`. Then unplug the sensor → `TRIP: heatsink sensor missing`. | The temperature table; bank resistance before and after; the two trip lines; the ROM printed at start. | 06 §3 row B5 → `T_TRIP_C` |
| **B6** Ripple and interference | Scope at the INA226 input. Full DAQ wired (all sensors, SD card). Flash `logger_load` with `HEATSINK_ROM` filled in. Supply 21 V, limit 4.7 A; the pyranometer must read ≥ 50 W/m² (lamp or daylight) or the load stays off. | Scope: ripple p-p while the load holds. Then 10 min with the load off (type `LOAD OFF`) and 10 min with it on (`LOAD ON`). Copy the day file. | Ripple p-p; standard deviation of `voltage` and `current` in each 10 min; count of rows with status bits TEMP_FAIL, IRRAD_FAIL, PANEL_FAIL in each 10 min. | 06 §3 row B6 → averaging |
| **B7** Faults | (1) `logger_load`, load holding. (2)–(4) `load_bench`, `HOLD 12`. | (1) Press reset while the load holds; also leave it running 2 min and check `/load_log.csv`. (2) Unplug the INA226 cable while holding: within ≈ 1 s the status line shows `INA226 FAILING: duty frozen`, D_A unchanged; plug it back: it continues. (3) Pull the supply connector under load, scope on the MOSFET drain. (4) Supply limited to 0.2 A, connect it reversed. | (1) scope or meter on both gates through the reset; the restart lines. (2) D_A before and after. (3) spike height. (4) current drawn. | 06 §3 row B7 |
| **B8** First sun | Clear midday. Module on the stand, PYR20 in plane, `logger_load`. Multimeter for V_oc at the module connectors. | Note V_oc with the meter while the load is `off` at start. Type `GAMMA 3` (a sweep every 15 s ≈ ten sweeps back to back), then leave 10 min of normal operation (hold + a sweep every 60 s). Copy `/SW*.csv`, `/SS*.csv`, the day file and `/load_log.csv`. | Meter V_oc and the `V_oc` column of `/SS…csv`; P_mp of the ten sweeps (`analysis/gamma.py`); lowest voltage of a sweep; `voltage` in the rows against `V_best` of the last sweep; the `flags` column and the sweep duration printed in each `# sweep:` line. | 06 §3 row B8 |
| **B9** Reference check | An electronic load or I-V tracer if one can be borrowed; otherwise the supervisor's data logger on the validation day (04). | Same module, same minutes: one reference I-V curve between two firmware sweeps. | Both P_mp values, G and T at each. | 06 §3 row B9 |

## What the PC tests already showed, and what only the bench can show

Shown on the PC (simulated module, 2000 µF `[EST]`): the mode machine, both branches off
at start and on every fault, branch B driven only in sweeps, the sweep finding the MPP
within 0.5 %, the hand-over at the end of a sweep, the hold loop after an irradiance step.

**Settling (matters for B4).** The time constant of a duty step is not one number:
τ = C / (D/R + |dI/dV|), and below the knee of the curve it is ≈ C·V/I, so it grows as the
module current falls. In the simulation the worst τ is ≈ 3.9 ms in full sun (5τ ≈ 20 ms =
`T_SETTLE_MS`) but ≈ 10 ms at 400 W/m² and ≈ 18 ms at 200 W/m² `[EST, simulation]`. A fixed
20 ms wait is therefore 5τ only near full sun. The firmware does not rely on the fixed
wait alone: a point is stored only when two readings in a row at the same duty agree
within `SWEEP_SETTLE_TOL` (0.2 % of V_oc); if the sweep would overrun its slot
(`SWEEP_BUDGET_MS`), points are stored anyway and the sweep is flagged `NOT_SETTLED`
(flag value 32). B4 at the low current limit and the `flags` column in B8 are the checks.

Only the bench can show: that the gates stay low through a reset (B1), the real τ and
ripple (B4, B6), that 20 kHz switching does not disturb I2C, 1-Wire or Modbus (B6), that
the INA226 triggered mode and the timing of one sweep point behave as assumed (the
`# sweep` line prints the duration: expected ≈ 1.5–1.9 s `[EST]`), heat (B5), and MPP
accuracy on a real curve (B8, B9). A lab supply is a rectangle, not an I-V curve (06 §5).

## Numbers to carry back into the firmware

| Number | From | Goes to |
|---|---|---|
| Heatsink sensor ROM | B5 start line | `HEATSINK_ROM` in `logger/logger.ino` |
| `T_SETTLE_MS`, `SWEEP_SETTLE_TOL` | B4 (and B6 noise) | `pv_config.h` |
| `T_TRIP_C` | B0 derating curve, B5 | `pv_config.h` |
| `HOLD_PERIOD_MS`, `HOLD_GAIN` | B3, B8 | `pv_config.h` |
| `HAS_LOAD 1` | after B0–B7 pass | `pv_config.h` |
| Shunt factor, cable resistance, V_BUS terminal | B2 | uncertainty budget §5 (06 §4) |

After any change in `pv_config.h`: `python -m pytest tests/test_load_logic.py` (the PC
tests read the constants from that file) and `python firmware/build_all.py`.
