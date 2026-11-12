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
| `analysis/detection.py` | chronological split 60/20/20 by whole days; four detectors; tunes on validation; scores the test days once; every setting scored for the delay-versus-false-alarms curves; settings at equal false-alarm budgets |
| `analysis/metrics.py` | event matching, valid observation time, per-cause recall, F1 and delay, lead, day-bootstrap intervals, paired differences |
| `analysis/gamma.py` | P_mp, I_sc per sweep; γ, α, β per shade-and-uncover run; mean and uncertainty |
| `analysis/validate.py` | clock lag, bias, limits of agreement against a reference-logger export |
| `analysis/report.py` | figures (PDF + PNG) and tables (CSV + LaTeX) |
| `analysis/fake_logger.py` | writes synthetic data as rig files, for tests and the demo only |

Tests: `tests/test_analysis.py` (13 tests); whole suite 88 passed (2026-11-12, after the
reviewer checks of §3a–§3c and the segment rule of §9).

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

Re-run on 2026-11-12 with a fourth detector and with false alarms per valid time. The
spike labels, the tuned settings and the TP/FN/FP of the first three detectors are
identical to the earlier run; only the false-alarm rates moved (340 valid hours instead
of 360: the daily cleaning window is taken out).

Split: 90 / 30 / 30 days. NASIM-GBT retrained on the 90 training days (hybrid, no wind,
50 trees). Settings chosen on the validation days: threshold 2.7 °C; CUSUM κ = 0.02 °C
per sample, h = 2.0 °C; irradiance step ≥ 50 % in 2 min; NASIM p ≥ 0.98 for 2 samples.

| Detector | TP/FN/FP | Precision | Recall (95 %) | F1 (95 %) | False alarms/day | Delay min, median (IQR) | Lead over the 3 °C crossing, min | Rise left at alarm |
|---|---|---|---|---|---|---|---|---|
| Threshold, fixed 3.0 °C | 84/1/1 | 0.99 | 0.99 (0.96–1.00) | 0.99 (0.97–1.00) | 0.04 | 8.0 (5.5–9.8) | −0.7 | 38 % |
| Threshold, tuned | 85/0/2 | 0.98 | 1.00 (1.00–1.00) | 0.99 (0.97–1.00) | 0.07 | 7.3 (5.0–8.7) | −0.5 | 43 % |
| CUSUM | 83/2/4 | 0.95 | 0.98 (0.94–1.00) | 0.97 (0.94–0.99) | 0.14 | 5.7 (2.7–9.8) | +0.5 | 54 % |
| Irradiance step | 40/45/11 | 0.78 | 0.47 (0.38–0.54) | 0.59 (0.50–0.66) | 0.39 | 0.5 (0.3–0.8) | +2.8 | 99 % |
| NASIM-GBT | 81/4/0 | 1.00 | 0.95 (0.91–0.99) | 0.98 (0.95–0.99) | 0.00 | 1.8 (0.5–5.7) | +3.0 | 74 % |

False alarms per day are per 12 valid daytime hours (`03` §4, §6); before this change the
three rates read 0.03, 0.07 and 0.13.

Per cause (recall / F1 / median delay in min; precision uses all false alarms):

| Detector | irradiance (n = 46) | non_irradiance (n = 25) | mixed (n = 14) |
|---|---|---|---|
| Threshold, tuned | 1.00 / 0.98 / 5.4 | 1.00 / 0.96 / 8.5 | 1.00 / 0.93 / 8.6 |
| CUSUM | 1.00 / 0.96 / 2.9 | 0.92 / 0.88 / 9.8 | 1.00 / 0.88 / 10.2 |
| Irradiance step | 0.87 / 0.82 / 0.5 | 0.00 / 0.00 / – | 0.00 / 0.00 / – |
| NASIM-GBT | 1.00 / 1.00 / 0.7 | 0.92 / 0.96 / 5.7 | 0.86 / 0.92 / 6.0 |

NASIM-GBT minus baseline on the same resampled days (mean, 95 % interval), at these
F1-tuned settings:
- against the tuned threshold: F1 −0.01 (−0.04 to +0.02); median delay −5.2 min (−6.3 to −3.5);
- against CUSUM: F1 +0.01 (−0.03 to +0.05); median delay −3.0 min (−4.8 to −1.8);
- against the irradiance step: F1 +0.39 (+0.32 to +0.47); recall +0.48 (+0.41 to +0.57);
  false alarms −0.39 per day (−0.64 to −0.18); median delay on irradiance-driven spikes
  **+0.1 min (0.0 to +0.3)**, i.e. NASIM is not earlier there.

### 3a. The same comparison at equal false-alarm budgets (reviewer point R2)

Each detector gets, on the validation days, its fastest setting within the budget that
still finds 90 % of the validation spikes; the numbers are from the test days
(`table8_matched_false_alarms`, `fig6_delay_vs_false_alarms`).

| Budget, per day | Detector | Setting | Test false alarms/day | Test recall | Delay, min | NASIM minus this, delay min (95 %) |
|---|---|---|---|---|---|---|
| 0.05 and 0.1 | Threshold | 2.7 °C | 0.07 | 1.00 | 7.3 | −5.2 (−6.3 to −3.5) |
| | CUSUM | κ 0.05, h 0.5 | 0.07 | 0.87 | 2.0 | +0.3 (−0.8 to +1.8) |
| | NASIM-GBT | p 0.98 | 0.00 | 0.95 | 1.8 | |
| 0.2 | Threshold | 2.1 °C | 0.18 | 1.00 | 6.0 | −4.1 (−5.2 to −2.9) |
| | CUSUM | κ 0.05, h 0.5 | 0.07 | 0.87 | 2.0 | −0.1 (−1.0 to +0.8) |
| | NASIM-GBT | p 0.95 | 0.07 | 1.00 | 1.8 | |
| 0.5 | Threshold | 2.0 °C | 0.64 | 1.00 | 5.8 | −4.2 (−5.2 to −3.2) |
| | CUSUM | κ 0.05, h 0.5 | 0.07 | 0.87 | 2.0 | −0.4 (−1.2 to +0.3) |
| | NASIM-GBT | p 0.90 | 0.32 | 1.00 | 1.7 | |

The irradiance step has no row: no setting finds 90 % of the spikes (its recall never
exceeds 0.47 on the test days, at any false-alarm rate).

What this changes:
- **The lead over the threshold rule holds**: 4 to 5 min at every budget.
- **The lead over CUSUM does not hold.** The 3.0 min of §3 came from tuning CUSUM for F1,
  which picked a slow setting (h = 2.0 °C). Tuned for speed within the same false-alarm
  budget, CUSUM alarms after 2.0 min and its difference from NASIM is not detectable at
  any budget. It pays in recall: 0.87 on the test days against 0.95–1.00 for NASIM
  (whether that recall difference is itself detectable was not tested). On the test-day
  curve restricted to recall ≥ 0.9, CUSUM's best delay is 3.0 min at 0.1 false alarms per
  day and 1.8 min at 0.9 per day; NASIM reaches 1.8 min at zero.
- The validation budget is not met exactly on the test days (threshold at budget 0.5:
  0.64 per day), so the realised rate is reported, not the budget.

### 3b. The irradiance-step baseline (reviewer point R3)

- On irradiance-driven spikes the one-line rule is as early as NASIM: 0.5 min against
  0.7 min. **NASIM's speed on these spikes is its irradiance feature, nothing more.**
- The rule finds 87 % of the irradiance-driven spikes and none of the others, so 47 %
  overall, and it gives 11 false alarms (0.39 per day) where NASIM gives none: a cloud
  edge that does not heat the module by 3 °C still trips it.
- So NASIM does **not** beat the rule on lead time. It beats it on coverage (the 39
  non-irradiance and mixed spikes, of which it finds 35) and on false alarms.
- On non-irradiance spikes NASIM (5.7 min) is ahead of the F1-tuned threshold (8.5 min;
  difference −2.8 min, −3.5 to −1.8), but the speed-tuned CUSUM of §3a reaches 4.2 min
  there. NASIM has no speed advantage that a simple rule does not also reach.

### 3c. Sensitivity of the cause labels (reviewer point R6)

All 359 valid spikes, relabelled with other limits on the explained fraction f_G
(`table9_cause_sensitivity`). "Known" = the 172 spikes where the generator injected only
one kind of driver.

| Limits (non / irradiance) | irradiance | non_irradiance | mixed | Relabelled | Known: right / mixed / opposite |
|---|---|---|---|---|---|
| 0.25 / 0.75 | 200 | 79 | 80 | 37 | 128 / 44 / 0 |
| 1/3 / 2/3 (used) | 207 | 109 | 43 | 0 | 147 / 25 / 0 |
| 0.40 / 0.60 | 210 | 130 | 19 | 24 | 163 / 9 / 0 |
| 0.50 / 0.50 | 213 | 146 | 0 | 43 | 172 / 0 / 0 |

The irradiance count barely moves (200–213). The limits only decide how many spikes are
called `mixed` instead of `non_irradiance`; no known spike ever gets the opposite label.
On this synthetic set a single limit at 0.5 would label every known case correctly, but
the generator's spikes are cleaner than real ones will be, so the limits stay at 1/3 and
2/3 until controlled tests on the module say otherwise.

**How to read this** (revised 2026-11-12 after §3a–§3c). The three temperature-based
detectors find almost every spike and their F1 differences are not detectable. NASIM
alarms 4 to 5 minutes before the threshold rule at any false-alarm budget. It is **not**
detectably earlier than a CUSUM tuned for speed, and **not** earlier than a one-line
irradiance rule on the spikes that rule can see. What NASIM offers on this data is the
combination in one detector: early, recall 0.95–1.00, and 0 to 0.07 false alarms per
day. Whether that combination is worth a learned model over "fast CUSUM + irradiance
step" is the question for the field data. Cautions:
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

- The matched-budget comparison picks one setting per detector and budget; CUSUM's
  validation recall (0.94) did not carry over to the test days (0.87). A recall
  difference between detectors at a matched budget is not tested for detectability.
- The irradiance step uses one fixed window (2 min); other windows were not tried.

- Controlled tests are only simulated by renaming clouds; windbreak tests are untested.
- `analysis/pipeline.py` has no option yet for the calibration file or the mapping file
  on the command line.
- The earlier evaluation (`eval/run.py`, pre-10-Nov labels) is kept and still tested; it
  is not used for the paper.

## 9. Detector segments now follow the board's rule (2026-11-12)

`analysis/ingest.py` used to start a new detector segment at every row with a missing
input and at every gap above 15 s. It now does what the logger does: a row that is
unusable or absent is skipped, and only `config.DET_RESET_AFTER_FAILS` = 5 such rows in a
row start a new segment (the same constant reaches the firmware through
`nasim_config.h`). A restart of the board (`uptime_ms` falls) and a new day also start
one.

Effect on the results:
- **The 150-day run above: none.** Its files have no missing rows; the segments are
  identical under both rules (150, one per day), so every number of §2–§6 is unchanged by
  this rule.
- **A fault-injected set** (30 synthetic days, seed 5; irradiance made empty in 20 single
  rows, 3 bursts of 2–4 rows and 1 burst of 6 rows per day `[ASSUMPTION: an invented fault
  pattern, to exercise the rule]`), full pipeline under both rules, three detectors,
  6 test days with 12 spikes:

| | Old rule | Board's rule |
|---|---|---|
| Detector segments (30 days) | 743 (24.8 per day) | 61 (2.0 per day) |
| Valid spikes labelled (irradiance / non / mixed) | 68 (35 / 28 / 5) | 70 (45 / 19 / 6) |
| Threshold, tuned: TP/FN/FP | 11/1/1 | 12/0/0 |
| CUSUM: TP/FN/FP, median delay | 11/1/1, 5.3 min | 12/0/1, 2.9 min |
| NASIM-GBT: TP/FN/FP, median delay | 11/1/4, 3.8 min | 12/0/0, 1.1 min |

  Under the old rule every single failed read cost each detector its 10-minute history,
  and cut rises in two, which also changed their size and cause labels. With 12 test
  spikes these numbers show the direction, not a size. Output: `out/segment_rule_check/`.
- Known side effect `[OPEN]`: a skipped row is not counted, so a 10-minute window spans up
  to 40 s more than 10 minutes (`07` §7). Accepted for now.
