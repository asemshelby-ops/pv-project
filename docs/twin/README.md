# Digital twin of the module and a simulated rooftop (post-paper work)

Branch `twin-sim`. This is project work for **after** the paper. Nothing here is used by
the paper; `paper/`, `analysis/`, `firmware/`, `detect/` and `export/` are not changed (the
twin imports from `analysis/`, `detect/`, `sim/`, `data/` and `config.py`, read only).

> **Everything the twin computes is a SIMULATION with placeholder parameters.** No number
> in this folder or in the twin's reports is a measurement of our module, of our pump or of
> any real plant, and none of it says what cooling would gain on a real roof. The twin is a
> tool for developing and comparing control strategies before the hardware exists.

Purpose: a model of our 100 W module (electrical + thermal), multiplied into a rooftop of
120 modules with spray zones, used to try control strategies on the same weather and the
same rooftop, and shown in the SCADA as a simulation source.

| Document | Content |
|---|---|
| [model-equations.md](model-equations.md) | every equation: module, string, thermal, spray, evaporation, pump, tank, controllers |
| [assumptions.md](assumptions.md) | every assumption, numbered, with what it leaves out |
| [parameters.md](parameters.md) | generated from `twin/params.py`: every value, unit, tag, source |
| [placeholders.md](placeholders.md) | what must be measured to replace each placeholder, who measures it, and how the value gets in without a code change |
| [simulation-results.md](simulation-results.md) | the first runs: validation hook on synthetic files, comparison of the five strategies. Simulation only |
| [ranking.md](ranking.md) | which unmeasured quantity changes the result most, one at a time (`twin.rank`) |
| [refs.md](refs.md), [refs.bib](refs.bib) | the twin's references not in `paper/refs.bib`, checked 2026-11-14, and what each check covered |
| [SUMMARY-AR.md](SUMMARY-AR.md) | ملخص بالعربية للفريق |
| [WHAT-DECIDES-AR.md](WHAT-DECIDES-AR.md) | ما الذي يحسم مشروع التبريد؟ ترتيب الكميات غير المقيسة (للفريق) |

## The parts

| File | Content |
|---|---|
| `twin/params.py` | **the one parameter file**: value, unit, tag, source, "to replace it, measure ..." |
| `twin/electrical.py` | single-diode module model (De Soto), series strings with bypass diodes |
| `twin/thermal.py` | lumped Faiman thermal model with the spray term |
| `twin/spray.py` | spray cooling, evaporation, pump table and the CSV slot for the measured curve |
| `twin/rooftop.py` | 120 module twins, 6 strings, spread, valves, shared pump and tank |
| `twin/controllers.py` | no cooling, continuous, fixed threshold, spike-triggered (NASIM), predictive |
| `twin/compare.py` | all strategies on the same weather and rooftop, with sensitivity blocks |
| `twin/rank.py` | one-at-a-time ranking of the unmeasured quantities by their effect on net energy |
| `twin/fit.py` | fits parameters to rig files and writes the overlay that replaces placeholders |
| `twin/validate.py` | validation hook: replay of logger files against measured temperature and power |
| `twin/data/pump_curve_R385_TEMPLATE.csv` | the table Abdullah fills in on the bench |
| `scada/sim_source.py`, `scada/web/rooftop.*` | the twin as a SIMULATION source of the SCADA |

## How to run it (from the project folder)

On Windows use `.venv\Scripts\python` in place of `python`.

```
python -m twin.validate --demo            validation hook on SYNTHETIC rig files (about 30 s)
python -m twin.compare                    five strategies, 5 synthetic days (about 10 min)
python -m twin.compare --no-sensitivity   the main table only (about 2 min)
python -m twin.rank                       one quantity at a time, ranked (about 8 min, 4 processes)
python -m scada.run --sim                 rooftop page: http://127.0.0.1:8050/rooftop
python -m twin.params                     regenerate docs/twin/parameters.md
python -m pytest tests/test_twin_*.py tests/test_scada_sim.py
```

`twin.compare` and `scada.run --sim nasim` use the NASIM-GBT model of the paper pipeline's
synthetic run. It is not in git; create it once with `python -m analysis.demo_synth`
(about 10 minutes). Without it, `twin.compare` stops with that instruction.

Results go to `out/twin/` (not in git): `validation_demo/`, `compare/report.md`,
`rank/report.md`. `twin.rank` does not need the NASIM model.

## When real data exists

1. **Validate first.** `python -m twin.validate --data <copy of the SD card> --real`
   replays the measured irradiance, ambient temperature and wind through the twin and
   writes its error against the measured module temperature and power
   (`out/twin/validation/report.md`). Expect a large error with the placeholders: that is
   the point of measuring.
2. **Fit.** `python -m twin.fit --data <copy of the SD card> --real` fits the single-diode
   parameters to the I-V sweep files, the heat capacity (time constant) and the Faiman
   coefficients to the day files, and writes `twin/params_measured.json`, tag `[MEASURED]`.
   A gamma from `analysis/gamma.py` can be given with `--gamma`.
3. **Pump curve.** Copy `twin/data/pump_curve_R385_TEMPLATE.csv` to
   `pump_curve_R385_measured.csv` in the same folder and fill in the bucket-test lines.
4. From then on every twin program uses the measured values: `twin/params.py` overlays
   both files on the placeholders when they exist. **No code is edited.** Validate again
   on days that were not used for the fit and commit the two files with the report.

`tests/test_twin_params.py` checks the two files whenever they exist: every entry must be
tagged `[MEASURED]`, so a fit on synthetic files can never sit in the measured slot.

## Rules for using the twin's numbers

- Every figure, table, page and file from the twin says SIMULATION. Keep the label when a
  number is copied into a report or a slide.
- Do not write "the system saves x %" from these numbers. Correct wording: "in the
  simulation with placeholder parameters, strategy A gave x % more net energy than no
  cooling on five synthetic days".
- The ranking of strategies depends on placeholders, above all on the evaporation term
  and the pump power (see [simulation-results.md](simulation-results.md)). Re-run after
  each placeholder is replaced.
- The rooftop is not a plant design. 120 x 100 W modules, strings of 20 and one R385-like
  pump per module are a scaled copy of our rig (see [assumptions.md](assumptions.md)).
