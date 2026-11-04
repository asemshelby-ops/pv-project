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

## Layout

| Path | Content |
|---|---|
| `config.py` | every parameter, tagged [CLAUDE.md] / [LIT] / [DS] / [EST] / [ASSUMPTION] / [SUPERVISOR] |
| `sim/thermal.py` | first-order Faiman thermal model of the module |
| `data/synth.py`, `data/schema.py` | synthetic days in the real CSV schema + labelled events |
| `detect/` | streaming features and the detectors: Threshold, CUSUM, NASIM-GBT |
| `eval/` | chronological split, event-level metrics, the one-command run |
| `export/` | (Step 5) C export of the model and features, parity test |
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
