# Digital twin of the module and a simulated rooftop (post-paper work)

On `main` since 2026-11-14 (built on branch `twin-sim`, now retired). This is project work for **after** the paper. Nothing here is used by
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
| `twin/showcase.py` | one simulated day for the presentation views: story mode and controller comparison |
| `twin/refresh.py` | **one command when real data arrives**: refit + regenerate those views |
| `scada/export_story.py` | the story mode as an MP4 and PNG stills for slides |
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
python -m scada.run --showcase            only the story mode and the controller comparison
python -m twin.showcase                   (re)create their data: out/twin/showcase/ (about 20 s)
python -m scada.export_story              the story as MP4 + stills (about 10 min at 1280x720)
python -m twin.refresh --data <SD copy> --real     real data: refit and regenerate (see below)
python -m twin.params                     regenerate docs/twin/parameters.md
python -m pytest tests/test_twin_*.py tests/test_scada_sim.py
```

`twin.compare` and `scada.run --sim nasim` use the NASIM-GBT model of the paper pipeline's
synthetic run. It is not in git; create it once with `python -m analysis.demo_synth`
(about 10 minutes). Without it, `twin.compare` stops with that instruction.

Results go to `out/twin/` (not in git): `validation_demo/`, `compare/report.md`,
`rank/report.md`. `twin.rank` does not need the NASIM model.

## Presentation views (story mode, comparison, export)

Everything in this section is SIMULATION. The 3D view and every exported picture and video
carry the label **"SIMULATION — placeholder parameters, not measured"** (`twin.VIEW_LABEL`;
the export stops if the page does not show it).

**Story mode** — `http://127.0.0.1:8050/rooftop?story=1`, or the button "▶ Story" on the 3D
view. About 75 s, eight steps: cloud shadow crosses the roof → modules cool → sun returns →
temperatures climb (thermal spike) → detector alarm → valves open, spray → temperatures
drop → what it cost and gave. Play/pause (space), step (⏮ ⏭ or the arrow keys, or click a
segment of the progress bar), EN / عربي, Esc leaves. Dragging the view frees the camera
until the next step. Counters: PV power, pump power, net power, water used.

- It replays `out/twin/showcase/story.json`: a window of ONE simulated day, every 10 s step
  of the twin with every module temperature. Chapter limits and the numbers in the captions
  are computed from that run (`twin/showcase.py`, `chapters()`); nothing is scripted.
- **Caption text is in one file: `scada/web/story_captions.json`** (English and Arabic).
  `{name}` is replaced by a number of the run. In Arabic, every English term, number and
  unit is wrapped in `<bdi dir="ltr">…</bdi>`; `tests/test_twin_showcase.py` fails if one
  is not. Font: IBM Plex Sans Arabic, vendored (offline).
- The day is a **scenario day** `[SCENARIO]`: clear sky over Makkah with three clouds
  placed by hand. With `--data <SD copy>` the weather of a logged day is used instead.
- The twin applies **one irradiance to all modules** (one pyranometer; a cloud edge crosses
  this roof in less than one 10 s sample). So after a cloud every string re-heats and all
  six alarms come within about 30 s: there is no single "affected string". The shadow that
  slides over the roof is drawing, tied to the irradiance ramp of the run.
- The story's detector is the **threshold rule** by default (rise ≥ 2.5 °C in 10 min): it
  waits for the rise, so the picture shows climb → alarm → spray. NASIM-GBT alarms while
  the sun is still returning, before a rise is visible:
  `python -m twin.showcase --story-detector nasim`. The captions name the detector that ran.
- The two cloud steps use the realistic module look (the shadow shows on lit glass); on the
  heat map colour means temperature and is never darkened. In story mode the colour scale
  is narrowed to the story's range (35–60 °C on the scenario day); the legend shows it.

**Controller comparison** — on the same page, below the 3D view: the five strategies on
that same day, rooftop and sensor noise. Four charts with one time axis (module
temperature, net energy minus no cooling, pump energy, water use), a card per strategy
marked SIM, and the table. The grey band is the story's window. The comparison uses
NASIM-GBT for the spike-triggered strategy when its model is on the computer
(`python -m analysis.demo_synth`), else the threshold rule, and says which.

**Export for slides** — `python -m scada.export_story [--lang en|ar|both] [--size 1920x1080]
[--fps 25] [--gpu]` writes `out/twin/showcase/story_<lang>.mp4`, stills in
`out/twin/showcase/stills/` and `compare_page.png`. Headless Chromium draws the story frame
by frame at a fixed time step (the video is the same on any computer), ffmpeg encodes it.
`out/twin/showcase/compare.png` (written by `twin.showcase`) is the comparison as a figure.

**3D view** — new drawing aids, none of them data: the cloud shadow (see above; on the
live page it follows irradiance / clear-sky irradiance of the newest step), the sun's path
over Makkah for the simulated day with hour marks (button "Sun path"), a colour legend in
°C inside the view, spray jets on the medium and high quality tiers. Frame times:
`python tests/browser/bench3d.py [--gpu] [--story]` (raw results of 2026-11-17, software
rendering: `showcase/bench_before_main.json`, `showcase/bench_after.json`).

## When real data exists

**One command** does steps 1, 2 and the views:

```
python -m twin.refresh --data <copy of the SD card> --real
python -m twin.refresh --data <copy of the SD card> --real --gamma -0.0041 --export
```

It validates the twin as it is (`out/twin/refresh/1_before/report.md`), fits
(`twin/params_measured.json`, tag `[MEASURED]`), validates again (`2_after/report.md`),
and regenerates the story mode and the controller comparison with the new parameters and
the weather of a logged day (`--date YYYY-MM-DD`; default: the day with the deepest midday
cloud; `--scenario-weather` keeps the scenario day). `--export` also renders the MP4.
Without `--real` it is a dry run: the fit goes to `out/twin/refresh/fit_synthetic.json`
and never into the measured slot. It does **not** fit evaporation, spray conductance or
pump power: those need the bucket test and the spray test (step 3 and `placeholders.md`).
The steps one by one:

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
