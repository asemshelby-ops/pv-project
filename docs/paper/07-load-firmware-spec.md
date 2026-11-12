# I-V sweep and MPP-hold firmware — design spec

Status: design, 2026-11-11. **No code is written yet**; it is written when the load parts
arrive, and tested with `06-load-bench-test-protocol.md`. Today the logger holds the load
off, so it records the module at open circuit (audit item 1).

Values tagged `[EST]` or `[ASSUMPTION]` are starting points. The bench step that fixes
each one is named.

## 1. What the firmware must do

1. **Hold** the module at its maximum-power voltage between sweeps, so the module runs
   (and heats) as a working module does, and the 10 s rows show a meaningful power.
2. **Sweep** the I-V curve every 60 s and store the raw points, so `P_mp`, `V_oc`, `I_sc`
   and the fill factor are measured, not inferred.
3. Run a **γ mode**: the same sweep every 15 s for a set time (`02-gamma-from-iv.md`).
4. Never disturb the 10 s logging grid or the NASIM detector.
5. Fail safe: any fault ends with the load off or passive.

Not in scope now: perturb-and-observe tracking, pump control, any control action taken
from the NASIM alarm.

## 2. Hardware it assumes

| Item | Value | Source |
|---|---|---|
| Load | **two branches** in parallel across the module, each a 1.5 Ω resistor bank switched by its own N-MOSFET through a gate-driver channel; one capacitor bank on the module side | decision 2026-11-11 |
| `R_BRANCH`, `LOAD_BRANCHES` | 1.5 Ω, 2 → `R_HOLD` = 1.5 Ω (branch A alone), `R_SWEEP` = 0.75 Ω (A and B) | `pv_config.h` |
| PWM, branch A | GPIO33, LEDC, 20 kHz | `pv_config.h` |
| PWM, branch B | GPIO14, second LEDC channel on the same timer (same frequency and phase); 10 kΩ pull-down on the driver input because GPIO14 toggles at boot | `pv_config.h` (decision 2026-11-12) |
| PWM resolution | 11 bit `[REC]` (now 10 bit in `pv_config.h`; 80 MHz / 20 kHz allows 11, PINMAP) | to change with the load code |
| Current and voltage | panel INA226 unit, between module and capacitor | CLAUDE.md |
| Heatsink temperature | one spare DS18B20 on the resistor heatsink, by ROM | new; needed for the trip |

Branch rule: **branch A is the only one switched in `HOLD`; branch B is switched only in
`SWEEP`, with the same duty as A.** The module then sees `R_HOLD / D` in hold and
`R_SWEEP / D` in a sweep. Reasons and part stresses: bench protocol §1.

With 11 bits, one duty step in hold at the MPP (D ≈ 0.46) changes `R_eff` by 0.11 % and
the module voltage by ≈ 0.05 % (≈ 10 mV) `[EST]`, because near the MPP
`dV/V ≈ −½ · dD/D`.

## 3. Modes

```
            boot
              │
            OFF ──(G ≥ 50 W/m² and V_oc ≥ 10 V)──► HOLD ◄──────────────┐
              ▲                                      │                 │
              │                          every SWEEP_PERIOD            │
      (G < 30 W/m² for 5 min)                        ▼                 │
              └───────────────────────────────── SWEEP ──(done)────────┘
   any mode ──(fault)──► TRIP ──(cause cleared for 5 min)──► OFF
```

| Mode | Duty | Notes |
|---|---|---|
| `OFF` (0) | A 0, B 0 | night, low light, before the first valid reading |
| `HOLD` (1) | A regulated, B 0 | constant-voltage hold at `V_set` |
| `SWEEP` (2) | A and B, same sequence | ≈ 1.6 s; rows are never sampled during it |
| `TRIP` (3) | A 0, B 0 | latched; reason logged |

`GAMMA_RUN` is not a mode of its own: it is `HOLD` + `SWEEP` with `SWEEP_PERIOD = 15 s`,
started by a serial command (`GAMMA <minutes>`) and ended by its timer. A laptop is at
the rig during γ runs anyway. Low-light limits (50, 30 W/m², 10 V) are `[ASSUMPTION]`.

## 4. Sweep

Sequence, started right after a 10 s row has been written (so the next row is ≥ 7 s away):

| # | Action | Time `[EST]` |
|---|---|---|
| 1 | Read G over Modbus (`G_before`); take the latest T1..T5 | 30 ms |
| 2 | Duty 0 on both branches, wait 50 ms, read → `V_oc` | 60 ms |
| 3 | **Coarse pass**, both branches: 24 duties, logarithmic from 0.02 to 1.00. At each: set the same duty on A and B, wait `t_settle`, trigger one conversion, wait for conversion-ready, store (k, D, V, I) | 24 × 30 ms |
| 4 | Find the coarse point with the largest V·I, at index m | — |
| 5 | **Fine pass:** 24 duties, linear between coarse points m − 1 and m + 1 | 24 × 30 ms |
| 6 | Largest V·I of all points, at duty `D_best` → `V_set`. Branch B off; branch A continues in `HOLD` from `D = min(1, 2 · D_best)`, which is the same `R_eff` with one branch | — |
| 7 | Read G again (`G_after`) | 30 ms |
| 8 | Append the points and one summary line to the SD card | 50 ms |

Total ≈ 1.6 s. The module spends this time away from its MPP once a minute; the effect
on its temperature is negligible `[EST]`.

Why two passes: a single linear sweep puts most points where the curve is flat. The
coarse pass covers V_oc down to the lowest voltage (≈ 4.4 V `[EST]`), which gives I_sc by
extrapolation and shows any bypass-diode step; the fine pass puts 24 points on the top of
the power curve, which is what `P_mp` needs.

Why the capacitor does not spoil the points: the INA226 sits between the module and the
capacitor. It reads the module's own current and voltage, so each (V, I) pair lies on
the module's curve even while the capacitor is still charging. `t_settle` only has to
make the voltage quiet during the few ms in which shunt and bus are converted one after
the other.

INA226 settings (TI datasheet, Rev. C):

| Use | Conversion time | Averages | Time per reading |
|---|---|---|---|
| Sweep points | 1.1 ms each for shunt and bus | 4 | 8.8 ms |
| Hold loop and 10 s rows | 1.1 ms each | 64 | 141 ms |
| Settling measurement (bench B4 only) | 140 µs | 1 | 0.28 ms |

Triggered (single-shot) conversions, started after the settle wait. The averaging in hold
spans ≈ 2800 PWM periods, which removes the 20 kHz ripple `[MEASURE: bench B6]`. Check
that the pinned INA226 library (0.6.6) exposes triggered mode and the conversion-ready
flag; if not, write the two registers directly.

Validity flags stored with each sweep (bit field): `G_UNSTABLE` (`|G_after − G_before| /
G_before > 2 %`), `MAX_AT_EDGE` (best point is the first or last of the fine pass),
`NON_MONOTONIC` (voltage did not fall as duty rose in the coarse pass), `LOW_LIGHT`,
`INA_ERROR`. Flagged sweeps are stored, never deleted; the analysis decides.

The firmware does **not** fit `P_mp`. It stores raw points; the parabola fit and the
I_sc extrapolation are done offline (`02-gamma-from-iv.md` §4), where they can be
re-done.

## 5. Hold

Constant-voltage hold, not constant duty. Reason: with a fixed duty the module sees a
fixed resistance; when a cloud halves the irradiance the operating point slides far down
the curve (≈ 9 V), and the logged power is meaningless. `V_mp` moves little with
irradiance, so holding the voltage keeps the module near its MPP through the very events
the paper studies.

Branch A only. Every 250 ms `[ASSUMPTION]`:
1. Read V and I (64-average reading).
2. `e = (V − V_set) / V_set`.
3. `D ← D · (1 + K · e)`, K = 1.0 `[ASSUMPTION]`, change limited to ±10 % of D per step,
   D kept inside [0.01, 1.0].

Near the MPP a relative duty change moves the voltage by about half as much in the
opposite direction, so K = 1 removes about half the error each step (stable, no
overshoot) `[EST; tune on bench B3/B8]`.

`V_set`: the best point of the last valid sweep. Before the first sweep of the day:
`0.78 · V_oc` `[ASSUMPTION: fractional open-circuit-voltage rule; re-check the factor in
Esram & Chapman 2007, cited in PROJECT_STATE §4]`.

## 6. Scheduling: no RTOS, no long blocking calls

The hold loop needs service a few times a second. Today's logger blocks for 750 ms in the
DS18B20 conversion and up to 1.1 s when it looks for the RTC second edge. Changes:

| Now | Change |
|---|---|
| `requestTemperatures()` blocks 750 ms | start the conversion without waiting, 800 ms before each row; read the result at the row |
| `syncRtcEdge()` blocks up to 1.1 s | poll the RTC once per loop pass until the second changes |
| Modbus read waits up to 300 ms on a timeout | unchanged (typical reply ≈ 20 ms `[EST]`); the hold loop tolerates a 300 ms gap |
| V, I and G read 0.8–1.1 s apart | at the row: RTC, then INA226 (141 ms), then G, then BME280. V, I and G within ≈ 0.2 s |

`loop()` becomes: service the load (if 250 ms have passed) → handle a marker → start the
temperature conversion when due → write the row when due → start a sweep when due.
Worst gap in hold service: ≈ 0.35 s (a Modbus timeout) outside sweeps.

One timeline of a minute: rows at 0, 10, 20 … s; a sweep from ≈ 0.5 s to ≈ 2.1 s after
the row at 0 s; hold everywhere else. In γ mode the sweeps start at 0.5, 15, 30.5 and
45 s: alternately just after a row and midway between two rows, never across a row.

While only the panel INA226 is used (pump unit deferred), the PaHub channel can stay
selected instead of being selected and released around every reading. The RTC and BME280
are on the upstream bus at other addresses, so nothing collides.

## 7. Files and columns

10 s row (`/YYYYMMDD.csv`) — five columns appended at the end, so older files and the
existing parsers still work. This uses the additions already proposed in CLAUDE.md
("confirm before coding") plus two from the audit:

Full header, in this order (written by the logger since 2026-11-12):

```
date,time,T1,T2,T3,T4,T5,T_ambient,humidity,irradiance,voltage,current,power,wind,p,alarm,D,load_mode,sweep_id,uptime_ms,status
```

| Column | Meaning | Written today (no load hardware) |
|---|---|---|
| `D` | duty of branch A at the moment of the reading, 0–1 | `NaN` (not measured, like `wind`) |
| `load_mode` | text: `none` = no load fitted. The load firmware will write `off`, `hold`, `sweep` (not expected in rows), `trip` | `none` |
| `sweep_id` | id of the last finished sweep; `0` = no sweep yet | `0` |
| `uptime_ms` | `millis()` at the start of the row's readings: orders the rows and gives their spacing when the RTC time is missing; a fall = the board restarted. Wraps after 49.7 days | number |
| `status` | bit field, decimal; `0` = nothing to report | number |

Changed from the first version of this table: `load_mode = none` and `sweep_id = 0` while
no load exists (Asim, 2026-11-12). `none` makes the column text, so the later modes are
proposed as names instead of the numbers 0–3 `[REC]`: confirm when the load is coded.

`status` bits. The only definition in the firmware is
`firmware/libraries/PvDaq/src/pv_status.h`; the analysis keeps the same table in
`data/schema.py` (`STATUS_BITS`, `decode_status`, `has_status`) and a test compares the
two. A bit is never renumbered once data exists; new bits are added at the end.

| Bit | Value | Name | Set when |
|---|---|---|---|
| 0 | 1 | `RTC_INVALID` | no valid RTC time at this row: `date` and `time` are empty |
| 1 | 2 | `TEMP_FAIL` | at least one of T1..T5 gave no valid reading (its field is empty) |
| 2 | 4 | `AMBIENT_FAIL` | BME280: no ambient temperature |
| 3 | 8 | `IRRAD_FAIL` | PYR20: no irradiance |
| 4 | 16 | `PANEL_FAIL` | panel INA226 or its PaHub channel: no V, I, P |
| 5 | 32 | `TROM_UNMAPPED` | `T_ROM` in `logger.ino` is not filled in: T1..T5 follow the bus-scan order, not positions on the module |
| 6 | 64 | `DET_SKIPPED` | the row was not given to NASIM (an input or the time is missing); the detector keeps its state |
| 7 | 128 | `DET_RESET` | NASIM's state was cleared at this row: `DET_RESET_AFTER_FAILS` (5) skipped rows in a row. `p` is empty for the next 10 min |
| 8 | 256 | `SD_RETRY` | this row needed a second SD write attempt, or the row(s) just before it did not reach the card |

Example: `status = 33` = 32 + 1 = `TROM_UNMAPPED` + `RTC_INVALID`. Until `T_ROM` is
filled in, a clean row reads `32`, not `0`. Rows outside the 06:00–18:00 window are not
"skipped" rows: the detector rests there by design and no bit is set.

Rows without RTC time are never dropped. They are written with `date` and `time` empty,
their `uptime_ms` and bit 0, into the day file of the last row that had a time (or
`/nodate.csv` if no row had one since boot). `analysis/ingest.py` rebuilds their time
from `uptime_ms` and the nearest timed row of the same boot (good to ≈ 1 s, the RTC's
step) and marks them `time_source = "uptime"`; rows it cannot place are counted, not
hidden. Files written before 2026-11-12 (16 columns) still load; the five columns are
then empty.

`/rom_scan.txt`: appended at every boot — one title line (UTC time if known, number of
sensors, whether `T_ROM` is used), then one line per DS18B20:
`index,ROM,crc ok|CRC BAD,genuine pattern|clone?,column` (`T1`..`T5` or `-`).

`voltage`, `current`, `power` keep their meaning: the 64-average reading at the row.
In `HOLD` they are the held operating point (close to the MPP, not exactly it). `P_mp`
comes only from sweeps.

Sweep points (`/SWYYYYMMDD.csv`): `sweep_id,k,D,V,I` — raw values, one line per point.

Sweep summary (`/SSYYYYMMDD.csv`): `sweep_id,date,time,uptime_ms,T1,T2,T3,T4,T5,
G_before,G_after,V_oc,V_best,I_best,D_best,flags`.

Size `[EST]`: 60 sweeps/h × 12 h × 48 points × ≈ 30 bytes ≈ 1 MB per day, about the same
as the row file. A 1 GB card holds more than a year.

## 8. Safety

| Event | Action |
|---|---|
| Boot, reset, flashing | PWM pins not driven → the pull-down on each gate-driver input holds both MOSFETs off. `pvOutputsOff()` drives both load pins LOW (done 2026-11-12) |
| Heatsink ≥ `T_TRIP` | `TRIP`, duty 0. `T_TRIP` = 80 °C `[ASSUMPTION]` until the resistor derating curve is read (bench B0) |
| Heatsink sensor missing or invalid | `TRIP` |
| I > 9.5 A (unit range ±10 A) | `TRIP` |
| INA226 read fails 3 times in a row | freeze the duty (a fixed resistor is passive and safe), log the error, sweeps suspended; back to normal when it reads again |
| Loop stalled | ESP32 task watchdog resets the chip → pins released → load off |

The load can never dissipate more than the module produces, so a stuck duty is not a
hazard by itself; the heatsink trip covers the rest.

## 9. Code layout and tests

- `firmware/libraries/PvDaq/src/pv_load_logic.{h,c}` — plain C, no Arduino calls: the
  duty sequences, the best-point search, the hold step, the mode machine. It compiles on
  the PC the same way `export/c/nasim.c` does.
- `firmware/libraries/PvDaq/src/pv_load.{h,cpp}` — LEDC, INA226 settings, timing.
- `firmware/tests/load_bench/` — serial commands for the bench protocol (`D 0.25`,
  `SWEEP`, `HOLD 18.0`, `SETTLE`, `STEPS`).
- `tests/test_load_logic.py` — runs the C logic against a simulated module (single-diode
  curve from pvlib with the listing values, plus the capacitor and `R / D`):

| Test | Pass |
|---|---|
| Sweep finds the MPP | `V·I` of the best point within 0.5 % of the simulated `P_mp`, for G from 200 to 1000 W/m² and T from 25 to 70 °C |
| Coarse pass reaches below 0.5 · V_oc | true at G = 1000 W/m² |
| Hand-over at the end of a sweep | with branch B dropped and `D_A = 2 · D_best`, the module voltage changes by less than 1 % |
| Branch rule | the duty of branch B is 0 in every mode except `SWEEP` |
| Hold after a 50 % irradiance step | voltage back within 1 % of `V_set` in ≤ 2 s; power ≥ 97 % of the new `P_mp` |
| Mode machine | `OFF → HOLD → SWEEP → HOLD`; every fault → `TRIP`; trip clears only after 5 min |
| Row grid | with sweeps and markers injected, row times stay on the 10 s grid |

Then the bench protocol B1–B9 on real hardware. "Works" is claimed only after both.

## 10. Parameters in one place (`pv_config.h`)

| Name | Start value | Fixed by |
|---|---|---|
| `SWEEP_PERIOD_S` | 60 | — |
| `GAMMA_SWEEP_PERIOD_S` | 15 | γ method |
| `SWEEP_N_COARSE`, `SWEEP_N_FINE` | 24, 24 | bench B8 (repeatability) |
| `SWEEP_D_MIN` | 0.02 | bench B3 |
| `T_SETTLE_MS` | 20 `[EST]` | bench B4 |
| `HOLD_PERIOD_MS`, `HOLD_GAIN` | 250, 1.0 | bench B3, B8 |
| `VSET_FRACTION_OF_VOC` | 0.78 | literature check |
| `T_TRIP_C` | 80 | bench B0, B5 |
| `LOAD_BRANCHES`, `R_BRANCH` | 2, 1.5 Ω (set in `pv_config.h` on 2026-11-11) | decided |
| `PWM_RES_BITS` | 11 | — |

## 11. Decisions

Decided (Asim, 2026-11-11):
- Two 1.5 Ω branches; A holds, B joins only in sweeps.
- The five new row columns of §7 (`D`, `load_mode`, `sweep_id`, `uptime_ms`, `status`),
  appended at the end of each row. Written by the logger since 2026-11-12, before the
  load exists: `D = NaN`, `load_mode = none`, `sweep_id = 0`.

- GPIO14 for the gate of branch B, with a 10 kΩ pull-down on the driver input so the
  gate stays low during boot (Asim, 2026-11-12); verified in bench step B1.

Still needed before coding:
1. Which spare DS18B20 goes on the heatsink, and its ROM.
