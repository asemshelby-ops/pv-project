# Analysis pipeline and its end-to-end run on synthetic data

Status: 2026-11-12. **Every number on this page comes from synthetic data.** It shows
that the pipeline runs from files in the rig's formats to the paper's figures and tables.
It says nothing about the real module, and none of it may appear in the paper as a result.

## 1. How to run it

```bash
.venv/Scripts/python -m analysis.demo_synth          # synthetic rig files + full analysis (~9 min)
.venv/Scripts/python -m analysis.export_model        # export NASIM to C, check C against Python
.venv/Scripts/python -m analysis.pipeline --data <SD card copy> --out <folder>   # real data
```

| Module | Does |
|---|---|
| `analysis/ingest.py` | reads `/YYYYMMDD.csv` (UTC → Makkah time), builds detector segments, day quality; reads `/markers.csv` and joins the typed log sheets; excluded windows; controlled tests |
| `analysis/events.py` | reference spike labels (`03` §2): onset, 3 °C crossing, peak, size, cause, origin, validity |
| `analysis/detection.py` | chronological split 60/20/20 by whole days; tunes on validation; scores the test days once |
| `analysis/metrics.py` | event matching, per-cause recall and F1, delay, lead, day-bootstrap intervals, paired differences |
| `analysis/gamma.py` | P_mp, I_sc per sweep; γ, α, β per shade-and-uncover run; mean and uncertainty |
| `analysis/validate.py` | clock lag, bias, limits of agreement against a reference-logger export |
| `analysis/report.py` | figures (PDF + PNG) and tables (CSV + LaTeX) |
| `analysis/fake_logger.py` | writes synthetic data as rig files, for tests and the demo only |

Tests: `tests/test_analysis.py` (10 tests); whole suite 68 passed.

## 2. What was regenerated

150 synthetic days (hard variant, seed 5, tilt 20°), written as logger files in UTC,
with markers, log sheets, sweep files of six γ runs and a reference export. The spikes
are labelled by the paper's definition. Rises after a cloud are now spikes with the cause
`irradiance`; before, they were scored as false alarms.

| | Spikes | Per day | Size °C, median (IQR) | Max rate °C/min, median |
|---|---|---|---|---|
| irradiance | 207 | 1.38 | 9.3 (7.1–11.7) | 1.11 |
| non_irradiance | 109 | 0.73 | 4.4 (4.1–5.2) | 0.51 |
| mixed | 43 | 0.29 | 5.2 (4.4–6.3) | 0.46 |
| all | 359 | 2.39 | 6.6 (4.7–9.8) | 0.73 |

Plus 373 minor rises (2–3 °C), neutral in scoring. These rates are set by the
generator's assumptions (1.5 clouds a day, 4–6 wind lulls a day); they are not a
prediction for Makkah.

## 3. Detectors on the 30 test days (85 spikes)

Split: 90 / 30 / 30 days. NASIM-GBT retrained on the 90 training days (hybrid, no wind,
50 trees). Settings chosen on the validation days: threshold 2.7 °C; CUSUM κ = 0.02 °C
per sample, h = 2.0 °C; NASIM p ≥ 0.98 for 2 samples.

| Detector | TP/FN/FP | Precision | Recall (95 %) | F1 (95 %) | False alarms/day | Delay min, median (IQR) | Lead over the 3 °C crossing, min | Rise left at alarm |
|---|---|---|---|---|---|---|---|---|
| Threshold, fixed 3.0 °C | 84/1/1 | 0.99 | 0.99 (0.96–1.00) | 0.99 (0.97–1.00) | 0.03 | 8.0 (5.5–9.8) | −0.7 | 38 % |
| Threshold, tuned | 85/0/2 | 0.98 | 1.00 (1.00–1.00) | 0.99 (0.97–1.00) | 0.07 | 7.3 (5.0–8.7) | −0.5 | 43 % |
| CUSUM | 83/2/4 | 0.95 | 0.98 (0.94–1.00) | 0.97 (0.94–0.99) | 0.13 | 5.7 (2.7–9.8) | +0.5 | 54 % |
| NASIM-GBT | 81/4/0 | 1.00 | 0.95 (0.91–0.99) | 0.98 (0.95–0.99) | 0.00 | 1.8 (0.5–5.7) | +3.0 | 74 % |

Per cause (recall / F1; precision uses all false alarms):

| Detector | irradiance (n = 46) | non_irradiance (n = 25) | mixed (n = 14) |
|---|---|---|---|
| Threshold, tuned | 1.00 / 0.98 | 1.00 / 0.96 | 1.00 / 0.93 |
| CUSUM | 1.00 / 0.96 | 0.92 / 0.88 | 1.00 / 0.88 |
| NASIM-GBT | 1.00 / 1.00 | 0.92 / 0.96 | 0.86 / 0.92 |

NASIM-GBT minus baseline on the same resampled days (mean, 95 % interval):
- against the tuned threshold: F1 −0.01 (−0.04 to +0.02); median delay −5.2 min (−6.3 to −3.5);
- against CUSUM: F1 +0.01 (−0.03 to +0.05); median delay −3.0 min (−4.8 to −1.8).

**How to read this.** On this data all three detectors find almost every spike, and the
F1 differences are not detectable. The only clear difference is *when* the alarm comes:
NASIM alarms about 5 minutes before the threshold rule, with about three quarters of the
rise still to come. This is the pattern the hindsight-versus-causal design of `03` §1 was
built to show. Two cautions:
1. The synthetic spikes are large (median 6.6 °C) and most follow a deep cloud, which an
   irradiance feature sees at once. Real spikes may be smaller and less clear-cut.
2. NASIM misses 4 spikes, all in `non_irradiance` and `mixed`: with no irradiance step to
   see, it is less sure. That is where real data will test it.

## 4. Cause rule against the generator

Rows: what the generator injected. Columns: the automatic label.

| Injected | irradiance | non_irradiance | mixed |
|---|---|---|---|
| irradiance only | 70 | 0 | 2 |
| wind lull or warm air only | 0 | 77 | 23 |
| both overlapping | 137 | 32 | 18 |
| declared "mesh test" (a cloud) | 7 | 0 | 2 |

Pure cases are labelled correctly in 147 of 172 (85 %); the errors all fall into `mixed`,
never into the opposite class. When a cloud and a lull overlap, the rule mostly says
`irradiance`, because the cloud's rise is the larger one.

## 5. γ and the comparison with a reference logger

- γ: six simulated runs on three days gave −0.583 %/°C (U = 0.033 %/°C, k = 2); the
  generator's own value is −0.581 %/°C. The I_sc-normalised cross-check gave −0.569 %/°C.
  This proves the arithmetic of `analysis/gamma.py`, not the method outdoors.
- Reference comparison: the simulated logger's clock was 3 s ahead and its thermocouples
  read 0.4 °C high; the analysis recovered a lag of 3.0 s and a bias of −0.40 °C. The
  3 s lag and the limits of agreement of the 10-min rise (±0.30 °C) fall just outside the
  proposed criteria of `04` §7, as they should with these simulated errors.

## 6. C export and parity

`analysis/export_model.py` retrained the model, confirmed it equals the saved one, wrote
`export/c/generated/nasim_model_nowind.c`, and ran the 30 test days (129 600 samples)
through Python and through the compiled C detector:

| Build | Largest \|Δp\| | Alarms Python / C | Alarm mismatches | Worst feature difference |
|---|---|---|---|---|
| double | 1.1 × 10⁻¹⁶ | 128 / 128 | **0** | 6.7 × 10⁻¹⁶ |
| float | 0.55 | 128 / 129 | 7 | 0.0135 |

The logger uses the double build; its alarm threshold constant is now 0.98 and it builds
(457 670 B flash).

**Not done: the check on the ESP32 itself.** `export/replay.py` flashes the board and
replays a test set, but it still builds that set from the earlier evaluation. It must be
pointed at this run's test days and run with the board connected. Until then the "ESP32
parity" result is the PC build of the same C code, not the board. (The earlier on-board
run, with the earlier model, showed the board and the PC build agreeing exactly.)

## 7. What changes when real data arrives

1. `--data` points at the SD-card copy; nothing else in the commands changes.
2. The reference mapping in `analysis/pipeline.py` (`DEFAULT_MAPPING`) is rewritten for
   the real data logger's column names.
3. `CAUSE_U_EFF` is refitted on the campaign's event-free clear-sky data.
4. The five new CSV columns and the sweep files are produced by the load firmware; until
   then the γ step is skipped and says so in `summary.md`.
5. Sensor calibration offsets from the water bath are passed to `ingest.read_logger`.

## 8. Known gaps in the pipeline

- Controlled tests are only simulated by renaming clouds; windbreak tests are untested.
- `analysis/pipeline.py` has no option yet for the calibration file or the mapping file
  on the command line.
- The earlier evaluation (`eval/run.py`, pre-10-Nov labels) is kept and still tested; it
  is not used for the paper.
