# Final review of the paper package

Date: 2026-11-12. Reviewed: `docs/paper/01`–`11`, `analysis/`, `paper/main.tex`, and the
firmware changes on branch `paper-package`. Two readings: a strict IEEE reviewer, and the
hardware lead. Nothing was merged to `main` and nothing was pushed.

## 1. Verdict

The package is a complete plan and a working analysis chain. It contains **no measured
result**. A reviewer today would reject the paper for that reason alone, and that is the
correct state for this date: the draft is honest about it. What decides the paper now is
hardware and days in the sun, not writing.

## 2. Fixed in this review

| # | Finding | Fix |
|---|---|---|
| 1 | The draft said three detectors "run on the microcontroller". Only NASIM-GBT runs in `firmware/logger`; the threshold and CUSUM are computed offline. | Abstract, contribution 3 and Section III-D reworded; a TODO states the two honest options (port both, or say only one ran on the device) |
| 2 | Section cross-references were typed by hand ("Section IV-D"). | Replaced by `\label` / `\ref` |
| 3 | The detection TODO did not ask for the comparison a reviewer will ask for first. | Added: lead time at a matched false-alarm rate |
| 4 | Author placeholder. | Six authors in the order given; e-mail TODO kept |

Checks after the fixes: `python paper/check_draft.py` passes; the test suite passes (68).

## 3. As a strict reviewer: what would be attacked

| # | Weakness | Severity | What answers it |
|---|---|---|---|
| R1 | No data. | blocking | the campaign |
| R2 | **Lead time at unequal operating points.** Settings are tuned for F1, then delays are compared. A detector tuned to be trigger-happy leads by construction. | high | `analysis/detection.py`: add a delay-versus-false-alarms curve per detector and compare at a matched rate. Not implemented yet |
| R3 | **An obvious fourth baseline is missing**: "alarm when irradiance steps up by X %". On synthetic data NASIM's lead comes mostly from seeing the irradiance step. If a one-line rule matches it, the learned model adds nothing for irradiance-driven spikes. | high | add the rule as baseline (d); report NASIM's lead separately for spikes that are not irradiance-driven |
| R4 | Reference labels and the threshold detector use the same signal. | medium | already designed for (`03` §1) and stated in the limitations; keep both |
| R5 | One module, one site, a few weeks; controlled events will outnumber natural ones. | medium | stated; report natural and controlled separately; no climate claim |
| R6 | Cause labels have no wind measurement behind them; the two thresholds (1/3, 2/3) are assumptions. | medium | the check against controlled tests (`table6`), plus a sensitivity table. The sensitivity table is not implemented |
| R7 | γ by a transient method with back-sheet sensors; the largest error terms are estimated, not measured. | medium | ≥ 6 runs on ≥ 3 days; report the spread; the I_sc-normalised cross-check |
| R8 | False alarms per day use all test days; excluded periods are not subtracted from the observation time, although `03` §4 says they are. | low | small code change in `analysis/metrics.py`; not done |
| R9 | Section IV-F quotes synthetic numbers. Some reviewers dislike any synthetic number in a field paper. | low | supervisor's call; the fallback (two sentences) is marked |
| R10 | Most references were read at abstract level. | medium | read [9], [16], [43] in full; repeat the search for on-microcontroller detection |
| R11 | The affiliation line and the paper title are the team's wording. | low | supervisor confirms department name and title |

## 4. As the hardware lead: what would fail in the field

| # | Risk | State | Action |
|---|---|---|---|
| H1 | **The load does not exist.** No parts, no firmware. Without it there is no γ and no power cost, and the module logs at open circuit. | open, critical path | buy the two-branch parts (`06` §1), bench B0–B9, then write the firmware of `07` |
| H2 | Logger robustness (audit items 4 and 5): one failed read resets NASIM for ≥ 10 min; RTC, BME280 and INA226 are not re-read for up to 60 s after an error; rows without RTC time are lost to `/nodate.csv`; `T_ROM` is empty. | **fixed in the firmware 2026-11-12, builds; not yet run on the board**: a failed read skips one row and NASIM resets only after 5 in a row; failed devices are re-read at the next row; rows without RTC time are kept (status bit, `uptime_ms`); ROMs are printed at boot and saved to `/rom_scan.txt`; scan-order columns no longer shift. `T_ROM` is still empty | fill `T_ROM` after the water bath; bench-check the new paths (unplug a sensor, pull the RTC, pull the card) |
| H3 | The five approved CSV columns (`D`, `load_mode`, `sweep_id`, `uptime_ms`, `status`). | **done 2026-11-12**: written by the logger (`NaN`, `none`, `0` until the load exists), documented in `07` §7, read by `analysis/ingest.py`; old files still load | the load firmware fills `D`, `load_mode`, `sweep_id` |
| H4 | GPIO14 toggles at boot. | mitigated on paper: 10 kΩ pull-down, bench step B1 | verify on the scope before connecting the module |
| H5 | On-board parity not re-run with the retrained model; `export/replay.py` uses the old test set. | open (PROJECT_STATE, pending) | adapt and run with the board |
| H6 | NASIM in the logger is trained on synthetic data with a 21°→20° tilt change and literature thermal parameters. Its alarms in pilot data mean little. | stated in the firmware banner | retrain on real data; do not interpret pilot alarms |
| H7 | Uncertainty budget rests on assumed values: shunt tolerance, cable resistance, PYR20 "5 %" basis, sensor noise, shield error. | open | bench B2; the seller's answer; water bath; validation day |
| H8 | Data-logger model unknown: the validation protocol and `DEFAULT_MAPPING` are generic. | open | update `04` and the mapping when the model is known |
| H9 | `CLAUDE.md` still lists the old baselines (irradiance gate, residual CUSUM) and "tilt ~21°" outside its CURRENT PHASE section. | stale lines, not contradicting CURRENT PHASE directly | sync from the Project copy when convenient |

## 5. Needs real data

1. Everything in Sections VI–VIII of the draft (30 TODOs).
2. `U_eff` for the cause rule; the check that θ = 3 °C sits above normal heating.
3. Every `[MEASURE]` row of the uncertainty budget.
4. γ, α, β and their spread.
5. Whether natural spikes are frequent enough: ≥ 50 + 20 events, or the fallback of `03` §6.
6. Retraining NASIM, then the on-board parity and timing.

## 6. Needs a supervisor decision

1. Research question, contribution sentence and non-claims (`09` §8).
2. Venue: journal or conference, budget, indexing requirement, submission date (`10` §5).
3. Author order, title, department name on the affiliation line.
4. Validation-day acceptance criteria (`04` §7).
5. Whether Section IV-F (synthetic verification) stays in the paper.
6. Whether pilot logging at open circuit may start before the load exists.
7. Title wording of the project ("Sudden Degradation"), after the paper is agreed.

## 7. Post-paper backlog (unchanged; nothing added to the paper's scope)

Anemometer and a wind-resolved cause label · float build of NASIM on the ESP32 ·
cooling, pump and the net-power rule · evaporation term in the thermal model ·
diagnose-before-spray and the reference panel · SCADA simulation.

## 8. Package inventory

| Where | What | State |
|---|---|---|
| `docs/paper/01`–`07` | uncertainty budget, γ method, spike definition, validation day, daily protocol, load bench test, load firmware spec | written; assumptions tagged |
| `docs/paper/08`–`10` | literature review (44 checked references), gap and contribution, venues | written; abstract-level reading for most sources |
| `docs/paper/11` | pipeline and synthetic end-to-end results | written; synthetic only |
| `analysis/` | ingestion, labelling, detectors, metrics, γ, validation, report, demo, export | tested (68 tests), run end to end on synthetic files |
| `paper/` | IEEE draft and bibliography | not compiled; static check passes |
| `firmware/` | tilt 20°, two-branch load constants, GPIO14, NASIM threshold 0.98 | builds; no load code; not flashed in this work |
