# NASIM — real-time thermal-spike detection for PV cooling

Graduation project, Mechatronics, Umm Al-Qura University:
*AI-Driven Smart Cooling for PV Panels Based on Real-Time Thermal Spike and
Sudden Degradation Detection.*

**NASIM** is the full system: a spike detector running on the ESP32 plus the
pump decision rule (net power). This repository holds supervisor task 1: the
detection models, built now on **synthetic** data so that the same code
retrains on the real module's data later. "Degradation" is used in the team's
working sense (reversible, temperature-induced power loss), pending the
supervisor's confirmation.

> Results on synthetic data prove the pipeline, not the method.

## Run

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pytest
.venv/Scripts/python -m eval.run --seed 5
.venv/Scripts/python -m eval.run --seeds 5 6 7 8 9          # + mean/spread summary
.venv/Scripts/python -m eval.run --seeds 5 6 7 8 9 --hard   # hard synthetic variant
```

Power BI tables (daily summary, spikes, sweeps, alarms) from the SD-card files:
`python -m analysis.powerbi --data <folder>` -> `out/powerbi/`; see
[docs/powerbi.md](docs/powerbi.md).

Seeds 0-4 are **burned**: their test results were seen while designing the
current NASIM-GBT, so the current version is judged on seeds 5-9 only.

`eval.run` generates the data, splits it chronologically by whole days,
trains, tunes on validation only, scores the test days once and writes
`out/results/<base|hard>/seed<N>/results.md` (tables, example-day plots,
NASIM-GBT feature importance) and, for several seeds,
`out/results/<base|hard>/summary_seeds<a>-<b>.md`. Base: 60 days split
36/12/12; hard: 150 days split 90/30/30 (>= 50 training spikes per seed).
Every tuned value is chosen on validation days only; a best value on the
edge of its grid extends the grid and the search repeats.

The **hard** variant (`config.HARD_VARIANT`, all values [ASSUMPTION]) removes
the easy giveaways of the base data: lull wind stays inside the background
range, sensor noise is 3x larger with slow drift, and slow haze dips
(irradiance_rise) and warm-air steps of Ta (ambient_rise, neutral) add
non-wind temperature rises.

## Paper pipeline (since 2026-11-12)

The paper uses `analysis/`, not `eval/run.py`: it reads the logger's own files (UTC),
labels spikes by the 10 Nov definition (a rise after the sun returns counts) and
compares threshold, CUSUM and NASIM-GBT. `eval/run.py` below is the earlier evaluation
with the earlier labels; it is kept and tested, but not used for the paper.

```bash
.venv/Scripts/python -m analysis.demo_synth        # synthetic rig files -> figures, tables
.venv/Scripts/python -m analysis.export_model      # NASIM to C + parity check
.venv/Scripts/python -m analysis.pipeline --data <SD copy> --out <folder>
```

Details and the synthetic results: `docs/paper/11-pipeline-and-synthetic-results.md`.

## Post-paper work: digital twin and simulated rooftop (on `main`; built on the retired branch `twin-sim`)

`twin/` is a digital twin of the module (single-diode + lumped thermal model) scaled to a
simulated rooftop of 120 modules with spray zones, for developing and comparing cooling
controllers before hardware exists; `scada/` shows it on the page `/rooftop`. **Everything
it computes is a simulation with placeholder parameters**, labelled as such; nothing in the
paper, the firmware or `analysis/` depends on it. Start at `docs/twin/README.md`.

```bash
.venv/Scripts/python -m twin.validate --demo     # validation hook on synthetic rig files
.venv/Scripts/python -m twin.compare             # five control strategies, simulated
.venv/Scripts/python -m scada.run --sim          # rooftop page, marked SIMULATION
```

## Layout

| Path | Content |
|---|---|
| `config.py` | every parameter, tagged [CLAUDE.md] / [LIT] / [DS] / [EST] / [ASSUMPTION] / [SUPERVISOR] |
| `sim/thermal.py` | first-order Faiman thermal model of the module |
| `data/synth.py`, `data/schema.py` | synthetic days in the real CSV schema + labelled events |
| `detect/` | streaming features and the detectors: Threshold, CUSUM, NASIM-GBT |
| `eval/` | chronological split, event-level metrics, the one-command run |
| `export/c/nasim.[ch]` | C port of the features, thermal step, CUSUM core and NASIM-GBT alarm logic |
| `export/c/generated/` | generated: `nasim_config.h` (from config.py) and the two m2cgen models |
| `export/run.py` | export + Python-vs-C parity test, report in `out/export/parity.md` |
| `twin/` | post-paper: digital twin, simulated rooftop, controllers (`docs/twin/`) |
| `scada/` | post-paper: SCADA layer reading the logger's files; rooftop simulation page (`docs/scada/`) |
| `tests/` | pytest for each module |

## Detectors compared

| Name | What it is |
|---|---|
| Threshold | rise >= 2.5 degC in 10 min, 3-sample median, irradiance gate 5 %, 10-min silence (as specified, and with the rise tuned) |
| CUSUM | CUSUM on measured minus modelled temperature; model driven by G, Ta and a slow wind baseline; also run with C, u0, u1 off by +/-20 % |
| NASIM-GBT | small LightGBM (<= 50 trees, depth <= 4) on ESP32-computable features, with and without wind. Current version: hybrid (CUSUM residual and statistic as extra features, same code as the CUSUM baseline), sqrt class weighting, alarm after 2 consecutive samples |
| NASIM-GBT v1 | the first version (no hybrid features, full class weighting, one sample), kept for comparison |

Event scoring: first alarm inside a spike = true positive; alarms outside
events or inside irradiance-driven rises = false alarms; minor lulls, mixed
windows and natural minor episodes are neutral; natural spikes are reported
separately.

## Export to C (Step 5)

```bash
winget install BrechtSanders.WinLibs.POSIX.UCRT     # gcc for the PC parity test
.venv/Scripts/python -m export.run
```

Exports both hybrid NASIM-GBT models (main: **no wind**) of
`config.EXPORT_VARIANT` / `EXPORT_SEED` with m2cgen, and runs that run's
held-out test days through Python and the compiled C detector. The double
build must match Python exactly (0 alarm mismatches); the float build is
reported for comparison, because the ESP32 FPU is single precision. The
models are trained on synthetic data: retrain on real data before use. No
ESP32 firmware here (task 2).
