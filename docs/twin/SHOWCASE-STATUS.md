# Showcase work: status (branch `twin-showcase`, stopped early 2026-11-17)

SIMULATION only. Work was stopped before the page code was written.

## Done
- `twin/showcase.py`: runs the five controllers on one scenario day and writes
  `out/twin/showcase/story.json` (story window, every module temperature, chapters,
  caption facts), `compare.json` and `compare.png` (labelled SIMULATION).
  `python -m twin.showcase` ran correctly BEFORE the last small edit (chapter cuts for an
  early alarm); after that edit it was only compiled, not re-run.
- `twin/__init__.py`: `VIEW_LABEL` = "SIMULATION — placeholder parameters, not measured".
- `twin/rooftop.py`: `simulate_day(on_step=...)`, a read-only hook. No physics change.
- Frame-time benchmark: `python tests/browser/bench3d.py` (+ `rooftop3d_bench.cjs`).
- "Before" frame times on `main` (headless software WebGL, stage 952x500; NOT the laptop):
  `docs/twin/showcase/bench_before_main.json`. ms per drawn frame, heat / realistic:
  low 141.9 / 143.9, medium 160.6 / 168.4, high 166.8 / 167.0.
- Baseline tests on `main`: 415 passed (without `tests/test_scada_rooftop3d.py`, not run).

## Half-done / decided but not coded
- Story detector: threshold rule by default (shows climb, then alarm, then spray: rise
  7.5 degC, burst 62 s, drop 4.7 degC, simulated). NASIM-GBT alarms during the sun's
  return, before a rise is visible (`--story-detector nasim`). Asim to confirm the default.
- The twin uses one irradiance for the whole roof, so all six strings alarm within 30 s;
  there is no single "affected string". The moving shadow will be drawing only.
- No tests yet for `twin/showcase.py`. Tests were not re-run on this branch.

## Not started
- Page: story mode (play/pause/step, captions file, Arabic RTL, IBM Plex Sans Arabic
  vendored), cloud shadows, in-view legend in degC, sun path arc, richer spray, the
  always-visible label, controller comparison charts.
- Server routes `/api/sim/story`, `/api/sim/compare`; `scada.run` wiring.
- MP4/PNG export command (no MP4 exists), `twin.refresh` (refit + regenerate),
  docs/twin/README.md section, "after" frame times.

## Resume
    git checkout twin-showcase
    pip install -r requirements.txt
    python -m analysis.demo_synth        # NASIM model, about 10 min (out/ is not in git)
    python -m twin.showcase              # check it still runs, then continue with the page
