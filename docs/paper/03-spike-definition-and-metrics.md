# Thermal spike: definition, cause labels and detection metrics

Status: definition for the paper, 2026-11-11. It replaces the labelling used by the
synthetic pipeline until now (labels taken from the simulator's hidden wind; irradiance
rises scored as false alarms). Basis: supervisor, 2026-11-10 — a temperature rise after
the sun returns **is** a spike; the cause is recorded as a separate field.

Numbers marked `[ASSUMPTION]` are fixed here before any real data is seen. They are
re-examined once, on the first two weeks of data, and then frozen for the paper. A
sensitivity table shows how the results move when each one changes.

## 1. Two roles that must stay separate

| | Reference labelling (the "truth") | Detectors (what is evaluated) |
|---|---|---|
| When | offline, after the day ends | online, on the ESP32, sample by sample |
| Sees | the whole day, past and future | only the current and earlier samples |
| Smoothing | centred (uses future samples) | causal (3-sample median of past samples) |
| Output | events: onset, peak, size, cause | alarm times |

This answers the obvious objection: "if a spike is defined as a 3 °C rise, a 3 °C
threshold detects it perfectly". The reference knows the event only in hindsight. A
causal threshold rule fires at the moment the rise **reaches** 3 °C, never earlier. The
research question is therefore about **how early and how reliably** a detector fires
while the rise is still developing, and how many false alarms that costs.

## 2. Reference definition of a spike

Signal: `T_module(t)` = mean of the calibrated back-sheet sensors (at least 4 of 5 valid),
on the 10 s grid, in local time (Asia/Riyadh).

1. Smooth offline: centred 3-sample median, then centred 7-sample (1 min) mean → `T_s`.
2. Rise over a trailing window: `R(t) = T_s(t) − min T_s(u)` for `u` in `[t − W, t]`,
   with **W = 10 min**.
3. A spike exists when `R(t) ≥ θ`, with **θ = 3.0 °C**.
   - `t_θ`: first time `R ≥ θ` ("threshold-crossing time").
   - `t_on` (onset): the latest time in `[t_θ − W, t_θ]` at which `T_s` is at its minimum.
   - `t_pk` (peak): the first local maximum of `T_s` after `t_θ` that is not exceeded
     during the following 5 min `[ASSUMPTION]`.
   - Size `A = T_s(t_pk) − T_s(t_on)`; duration `t_pk − t_on`; maximum rate = largest
     2-min slope inside the event, in °C/min.
4. The search for the next event starts at `t_pk`.
5. A **minor rise** is the same construction with `2.0 °C ≤ max R < 3.0 °C`
   `[ASSUMPTION]`. It is counted and reported, and it is neutral in detector scoring.

Why these values:
- θ = 3 °C: the supervisor's "about 3–4 °C" `[SUPERVISOR]`, lower end.
- W = 10 min: about 1.6 thermal time constants (τ ≈ 6.3 min, Herteleer et al. 2023, as
  recorded in CLAUDE.md); a step change shows ≈ 80 % of its final effect within W.
- 3 °C in 10 min is three times the normal heating rate of ≈ 0.1 °C/min (Moharram et al.
  2013, as recorded in CLAUDE.md).

Check on real data before freezing θ `[REC]`: on clear periods with no event and no
intervention, the 99.9th percentile of `R(t)` must be below θ. If the morning warm-up
alone reaches θ, the paper says so and restricts the analysis window instead of quietly
raising θ.

**Valid periods.** An event is kept when all of these hold: onset inside the logged day
window (06:00–18:00 local); sun elevation above ≈ 11.5° (`cos(zenith) ≥ 0.2`)
`[ASSUMPTION]`; no data gap longer than 30 s between `t_on − W` and `t_pk`; not inside an
excluded window (§4).

## 3. Cause label

Without an anemometer, wind cannot be observed. The honest label set is therefore
**irradiance-driven or not**, plus what the log sheet tells us.

For each event:
- `G_pre` = lowest 1-min mean of G in `[t_on − 10 min, t_on + 1 min]`.
- `G_ev` = mean G over `[t_on, t_pk]`; `ΔG = G_ev − G_pre`.
- Rise that this irradiance step explains, first-order thermal model:
  `ΔT_G = (ΔG / U_eff) · (1 − exp(−(t_pk − t_on) / τ))`.
  `U_eff` and `τ` are fitted once on this campaign's event-free clear-sky data (constant
  U, because wind is not measured). Starting values until then: `U_eff = 35 W/(m²·K)`
  `[EST]` (Faiman pvlib defaults at 1.5 m/s) and `τ = 6.3 min` `[LIT]`.
- Explained fraction `f_G = ΔT_G / A`.

| `cause` | Rule | Meaning |
|---|---|---|
| `irradiance` | `f_G ≥ 2/3` | sun returning after cloud, haze or dust; shade removed |
| `non_irradiance` | `f_G ≤ 1/3` | wind lull, warm-air step or anything else not seen by the pyranometer |
| `mixed` | between | both contribute |

The thresholds 1/3 and 2/3 are `[ASSUMPTION]`. Also stored per event, as numbers and not
as labels: `ΔG`, `f_G`, and the change of air temperature over the event `ΔT_air`.

Second field, `origin`, from the day's log sheet and `/markers.csv`:

| `origin` | Source | Use |
|---|---|---|
| `natural` | no log-sheet entry within the event | frequency statistics and detection |
| `ctl_mesh` | rise after the shade mesh is removed | detection; known irradiance cause |
| `ctl_windbreak` | rise while the windbreak is up | detection; known non-irradiance cause |
| `negative_control` | shade < 1 min, hand wave | must produce no event and no alarm |

**Check of the rule itself.** Controlled events have a known cause. The automatic `cause`
rule is run on them blind, and the paper reports the agreement (a 2 × 3 table). This is
the only validation of the cause labels that is possible without an anemometer.

## 4. Excluded windows

Alarms and rises inside these windows are ignored (neutral), and the windows are removed
from the observation time used for rates:
- 30 min after a logged cleaning `[ASSUMPTION]` (water cools the glass; the re-heating is
  not a spike of interest);
- γ runs (`02-gamma-from-iv.md`): from the shading until 30 min after uncovering;
- logged maintenance, and any period with a missing sensor.

## 5. Spike statistics reported (characterisation)

All for `natural` events, split by `cause`; controlled events in a separate table.
- Observation time: valid daytime hours and number of days.
- Events per day and per 100 valid daytime hours, with a 95 % interval (Poisson, or
  bootstrap by day).
- Distributions of size A, duration, maximum rate, time of day.
- Power cost per event: `γ · ΔT_cell` (uncertainty budget §7) and the summed cost per day.
- Minor rises: the same counts, to show what the 3 °C threshold leaves out.

Wording rule: these numbers describe **this module, this site, these dates**. They are
not "the frequency in Makkah's climate"; the paper says which months were covered.

## 6. Detection metrics

A detector produces alarm times. After an alarm it is silent for 10 min (unchanged).

**Matching.** An alarm is matched to a spike when it falls in `[t_on, t_pk + 2 min]`
`[ASSUMPTION: grace of 2 min]`.
- First matched alarm of a spike → **TP**. Further alarms in the same spike are ignored.
- Spike with no matched alarm → **FN**.
- Alarm outside every spike, minor rise and excluded window → **FP**.
- Alarm inside a minor rise → neutral, counted separately ("alarms on minor rises").

**Metrics.**

| Metric | Definition |
|---|---|
| Recall | `TP / (TP + FN)`; overall and per cause |
| Precision | `TP / (TP + FP)` |
| F1 | harmonic mean of the two |
| F1 per cause c | recall from the spikes of cause c; precision `TP_c / (TP_c + FP)` with **all** false alarms, because a false alarm has no cause. Stated in the table caption |
| False alarms per day | `FP / valid days`; also per 100 valid daytime hours |
| Detection delay | `t_alarm − t_on`: median, interquartile range, 90th percentile |
| Lead over hindsight threshold | `t_θ − t_alarm`: positive = the alarm came before the rise reached 3 °C |
| Rise remaining at the alarm | `(T_s(t_pk) − T_s(t_alarm)) / A`: the share of the spike still to come |
| Chance recall | recall of alarms placed at random at the detector's own alarm rate; shows the result is above chance |

**Uncertainty of the metrics.** Bootstrap over whole days (2000 resamples), 95 %
percentile intervals. Differences between detectors (F1, median delay) are computed on
the same resampled days (paired) and reported with their intervals. A difference whose
interval contains zero is reported as "no detectable difference".

**Data split.** Chronological, by whole local days: 60 % train, 20 % validation, 20 %
test. Controlled-test days are spread over the three parts by the test schedule. Every
tunable value is chosen on validation days only; the test days are scored once.

**Minimum data** (PROJECT_STATE §3): ≥ 50 spikes for training and ≥ 20 held out. With
fewer, the threshold and CUSUM results are the primary result and NASIM is exploratory.

## 7. Detectors under this definition `[REC]` — implemented in the analysis stage

| | Old | New |
|---|---|---|
| (a) Threshold | rise ≥ 2.5 °C in 10 min **and** irradiance steady within 5 % | rise ≥ θ_a in 10 min; **no irradiance gate**; θ_a tuned on validation |
| (b) CUSUM | on the residual of a thermal model driven by G and T_air | on the temperature increment: `S = max(0, S + (T_k − T_{k−1}) − κ)`, alarm at `S ≥ h`. The residual version explains irradiance rises away by design, so it would miss half of the new definition. It stays as a NASIM feature |
| (c) NASIM-GBT | irradiance rises = hard negatives | positives = first 10 min of every spike, any cause; `mixed` events are scored, not excluded |

## 8. On-device agreement (ESP32 parity)

The logger writes `p` and `alarm` for every row. The same rows are replayed offline
through the Python detector. Reported: number of rows compared, alarm mismatches, largest
`|Δp|`, and the number and total length of detector blackouts (rows where the logger gave
no `p` during the day window).

## 9. Fields of the event table (`events.csv`)

`event_id, date_local, t_on, t_theta, t_pk, size_C, duration_s, max_rate_C_per_min,
G_pre, G_ev, dG, f_G, dT_air, cause, origin, marker_ids, valid, note` — times in UTC,
ISO 8601; `date_local` is the Asia/Riyadh date used for the day split.
