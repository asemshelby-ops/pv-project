# Phase B preparation — what was added, how to switch it on, what is still missing

Prepared 2026-11-17 on the branch `phase-b-prep`. **Software only, and not adopted.**
"Phase B" is the step after the paper: a randomized spray trial on the same rig, plus
some measurements that make its result readable. No pump, nozzle, flow sensor or tank
exists yet, the supervisor has not approved any of it, and `paper/main.tex` was not
touched.

Three rules were followed, and each has a test:

1. **Everything new is behind a switch that is OFF.** With every switch off, the logger's
   CSV rows and all analysis results are byte-identical to the project before this work
   (`tests/test_phase_b_flags.py` against `tests/golden/phase_b_off.json`, made from
   commit `51d18aa`). The detector parity between the PC build and Python is unchanged
   (zero mismatches on the 150 synthetic days).
2. **Only synthetic data was used.** Every number in these documents that comes from a
   run is marked SYNTHETIC: it shows that the code runs and finds again what the
   generator put in. None of them says anything about the real module, a real pump or a
   real spray.
3. **What is not known yet is a named constant in one file**, `config.py`, block
   "PHASE B PREPARATION", tagged `[PLACEHOLDER]`. The firmware gets these values through
   a generated header (`python -m export.phase_b_header` →
   `export/c/generated/phase_b_config.h`); no pin and no sensor constant is typed into
   the firmware.

## The switches

| Switch | Where | Default | Turns on |
|---|---|---|---|
| `PHASE_B_ENABLED` | `pv_config.h` (firmware) | `0` | spray trial: events, arms, pump, metering; six more columns in the 10 s row; `/spray_log.csv` |
| `MARKER_CODES_ENABLED` | `pv_config.h` | `0` | a `code` column in `/markers.csv`; the commands `MARK MESH ON/OFF`, `MARK WINDBREAK ON/OFF` |
| `NASIM_TIMING_LOG` | `pv_config.h` | `0` | time of the NASIM update, one line per hour in `/status_log.csv` |
| `PHASE_B_ENABLED` | `config.py` (analysis) | `False` | the pipeline's spray-trial step (needs files with the six columns) |
| `LOADOFF_CLASS_ENABLED` | `config.py` | `False` | cause `load_off`; the verdict with and without those spikes |
| `ECT_ENABLED` | `config.py` | `False` | equivalent cell temperature from the sweeps' V_oc |
| `PHASE_B_DRY_RUN` | `config.py` → header | `True` | (safety) events and arms are logged, the pump output is never attached |
| `PHASE_B_PLACEHOLDERS_CONFIRMED` | `config.py` → header | `False` | (safety) while `False`, a wet build stops with a compiler error |
| `PHASE_B_HELDOUT_WEEK` | `config.py` | `None` | `[PROPOSAL]` one week locked out of every table until unlocked |

A firmware flag is set for one build with `-D`, e.g. the build `logger_all` of
`firmware/build_all.py`, or by editing its `#define` in `pv_config.h`.

## What each part is

| Task | Document | Code | Tests |
|---|---|---|---|
| 1 Randomized spray | [01-randomized-spray.md](01-randomized-spray.md) | `PvDaq/src/pv_spray_logic.c`, `pv_spray.cpp`, `logger.ino` | `test_spray_logic.py`, `test_firmware_host.py` |
| 2 Per-event metering and the two arms | [02-metering-and-arms.md](02-metering-and-arms.md) | the same firmware; `analysis/phase_b.py`, `analysis/fake_phase_b.py` | `test_phase_b_analysis.py` |
| 3 Load-off spikes as their own class | `docs/paper/03` §11 (status note) and below | `data/schema.py` (`MARKER_CODES`), `analysis/ingest.py`, `events.py`, `metrics.py`, `verdict.py` | `test_loadoff.py` |
| 4 Cleaning or cooling | [02-metering-and-arms.md](02-metering-and-arms.md) §5 | `analysis/phase_b.py` (`cleaning_check`) | `test_phase_b_analysis.py` |
| 5 Equivalent cell temperature from V_oc | [03-ect-from-voc.md](03-ect-from-voc.md) | `analysis/ect.py` | `test_ect.py` |
| 6 Model footprint and update time | [04-model-footprint.md](04-model-footprint.md) | `firmware/tools/footprint.py`, `PvDaq/src/pv_timing.c` | `test_footprint.py`, `test_timing.py` |
| 7 Minimum detectable effect, held-out week | `docs/paper/03` §12 `[PROPOSAL]` | `analysis/mde.py`; the lock in `analysis/phase_b.py` | `test_mde.py`, `test_phase_b_analysis.py` |
| Flags off = nothing changed | this page | `tests/golden/make_phase_b_off.py` | `test_phase_b_flags.py` |

### Task 3 in short: marker codes and the `load_off` class

- `/markers.csv` gets a fourth column `code` (firmware flag `MARKER_CODES_ENABLED`):
  `0` button press, `10` / `11` mesh (shade cloth) on / off, `20` / `21` windbreak on /
  off, `30` / `31` load off / on. One table, `MARKER_CODES` in `data/schema.py`; the
  firmware gets it through the generated header. Load-off and load-on are written by the
  firmware itself when the load is switched; mesh and windbreak by a typed command; the
  button stays `0` and is named on the log sheet as before. Files without the column, and
  files where it starts half-way, are read as before.
- With `LOADOFF_CLASS_ENABLED`, a rise that starts between `load off` and two minutes
  after `load on` gets the origin `ctl_load` and the cause **`load_off`**, a class of its
  own next to `irradiance`, `mixed` and `non_irradiance`. What the cause rule of
  `docs/paper/03` §3 would have said is kept in the column `cause_rule`.
- `verdict.py` then prints the verdict of `docs/paper/03` §10 twice, **with and without**
  the load-off spikes, each with its own precondition (≥ 20 test spikes, ≥ 8 of them not
  irradiance-driven), and `verdict.json` holds both. Whether load-off spikes may count
  towards the 20 and the 8 is the supervisor's decision; the code shows both answers and
  takes neither.
- SYNTHETIC illustration (`python -m analysis.demo_phase_b --days 20`; two open-circuit
  tests a day with an **invented** rise large enough to pass 3 °C in 10 min): 31 of the 40
  tests gave a spike labelled `load_off`. On the test days, spikes that are not
  irradiance-driven: 9 with them, 5 without. The "≥ 8" of the precondition is met only if
  they count — which is exactly why the question has to be settled before field data
  exists. (Whether the real module heats that much at open circuit is not known;
  `docs/paper/03` §11 estimates 1.9 to 4.7 °C.)

## How to run it

```bash
python -m pytest                                   # everything: 564 tests, about a quarter of an hour
python -m pytest tests/test_phase_b_flags.py       # flags off = byte-identical
python -m analysis.demo_phase_b --days 20          # SYNTHETIC end-to-end: trial, ECT, load-off
python -m analysis.mde --per-arm 10 20 40          # smallest detectable difference, SYNTHETIC spread
python -m export.phase_b_header                    # after changing a Phase B value in config.py
python firmware/build_all.py logger                # needs arduino-cli; builds all four logger variants
python firmware/tools/footprint.py --detail        # flash and RAM of the NASIM model from those builds
```

On real files, once a Phase B logger has run: set the switches in `config.py`, then
`python -m analysis.pipeline --data <card copy> --out <folder>` as for the paper; or
`python -m analysis.phase_b --data <card copy> --out <folder>` for the trial tables alone.

## Values that are placeholders today

All in `config.py`. A wet build is refused until each has a measured or chosen value and
`PHASE_B_PLACEHOLDERS_CONFIRMED` is set.

| Constant | Today | Comes from |
|---|---|---|
| `PHASE_B_SEED` | 20261117 | chosen once before the first event, written in the protocol |
| `PUMP_DUTY`, `SPRAY_MAX_S` | 0.60, 60 s | pump curve with the real nozzles (bucket test); cooling per second on the module |
| `SPRAY_STOP_DROP_C` | 0 (not used) | a decision: fixed dose or temperature target |
| `SPRAY_MIN_DT_WATER_C`, `SPRAY_MAX_DT_WATER_C` | 2 °C, 40 °C | a decision with a stated basis; no manufacturer guidance exists for this module |
| `PUMP_I_MAX_A`, `PUMP_NO_FLOW_S` | 5 A, 5 s | the pump's data; the priming time of the real loop |
| `FLOW_PULSES_PER_L`, `FLOW_GLITCH_NS`, `FLOW_PIN` | 450, 10 µs, GPIO35 | the sensor that is bought: calibrated by volume, its shortest pulse; the wiring |
| `PUMP_INA226_ADDR`, `_PAHUB_CH`, `_SHUNT_OHM`, `_MAX_A` | 0x41, channel 1, 5 mΩ, 10 A | the board used on the pump rail (see `02` §1) |
| `TANK_ROM` | all zero = not fitted | the ROM of the DS18B20 in the tank (`/rom_scan.txt`) |
| `ECT_VOC_REF_V`, `ECT_ISC_REF_A`, `ECT_BETA_VOC_REL`, `ECT_A_IRR` | 21.5 V, 5.88 A, −0.30 %/°C, 0.05 | a calibration of this module, or the fit on its own sweeps (`03`) |
| `PHASE_B_HELDOUT_WEEK` | `None` | `[PROPOSAL]` the trial's last week, named at the freeze |

Assumptions that are not placeholders but still need a decision or a measurement: the
30-minute window, the 10-minute lock-out and baseline, the 200 W/m² lower limit, the 3 s
margin of the independent cut-off timer (above the longest stall of the main loop, to be
measured on the board), α = 0.05 and 80 % power.

## What still needs hardware, and what needs a decision

Hardware or the board:
- compile the four logger variants for the ESP32 and fix what the real compiler finds;
  the ESP-IDF parts (pulse counter, NVS, `esp_timer`, LEDC channel 2) were checked for
  syntax against stubs only;
- read the footprint table from that build; run the timing build for a day;
- pump, nozzles, tubing, tank, flow sensor, a current sensor on the pump rail, the tank
  temperature sensor; the gate pull-down and the pin's level through reset;
- the pump curve and the flow calibration; the cut-off timer and the watchdog with a
  blocked loop; a full dry run on the rig;
- a NASIM model trained on field data before any wet run;
- a calibration of the module (or enough steady sweeps for the fit) for the ECT level.

Supervisor decisions:
- whether the spray trial is run as a randomized two-arm comparison at all, its primary
  outcome and its length (`docs/paper/03` §12);
- the held-out week (`docs/paper/03` §12);
- whether load-off tests are run, and whether their spikes count towards the 20 / 8
  precondition (`docs/paper/03` §11);
- the pump-rail current sensor: the two M5Stack INA226 units have the same fixed address,
  so "its own I2C address" means its own PaHub channel or a different board;
- whether one failed sensor out of the five on the back sheet should block spraying
  (`01` §5), the two water-temperature limits, fixed dose or temperature target;
- the marker code numbers, before the first file with codes exists (afterwards they must
  not change).
