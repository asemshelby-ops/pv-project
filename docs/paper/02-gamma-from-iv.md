# Measuring γ (temperature coefficient of P_mp) from I-V sweeps

Status: method design, 2026-11-11. Needs the PWM load and sweep firmware
(`07-load-firmware-spec.md`). No value of γ exists yet for our module; the seller listing
gives none, and none may be assumed.

**Standard.** IEC 60891:2021 (Edition 3.0) defines the procedures for temperature and
irradiance corrections of measured I-V curves and the procedures to determine the factors
used in those corrections (IEC webstore abstract, checked 2026-11-11). The full text is
paywalled and **has not been read**. Everything below is the team's own procedure. The
paper may say "determined outdoors from I-V sweeps at near-constant irradiance" and must
not say "according to IEC 60891" unless someone reads the standard and confirms each
requirement (irradiance stability, temperature span, sensor accuracy). `[OPEN]`

## 1. Definition

`γ = (1 / P_mp,ref) · dP_mp/dT_cell` at constant irradiance, in %/°C, with the reference
at `T_ref = 25 °C`. Reported with its irradiance level, because γ is measured near one
irradiance (≈ 800–1000 W/m²) and is used at similar levels.

## 2. Method A (primary): shade, uncover, sweep while the module heats

Idea: change the temperature a lot while the irradiance stays the same. A module that
was shaded is close to air temperature. Uncovered at noon, it heats by ≈ 20 °C `[EST]`
with a time constant of ≈ 6 min `[LIT, CLAUDE.md]`, under an irradiance that barely moves.

Conditions:
- Clear sky, within ± 60 min of solar noon (computed, not 12:00), G ≥ 800 W/m².
- Module cleaned that morning (daily protocol).
- Logger in `GAMMA_RUN` mode: one sweep every 15 s.

Steps:
1. Shade the **module only** with an opaque board held ≥ 10 cm above the glass (air must
   pass under it), for ≥ 25 min (≈ 4 time constants). The PYR20 stays in full sun the
   whole time, so its own temperature and reading stay steady.
2. Press the marker, remove the board in one movement, step away from the module and the
   PYR20 (no shadow, no reflection).
3. Leave the module alone for 20 min (≈ 3 time constants). ≈ 80 sweeps are recorded.
4. Press the marker, write the run on the day's log sheet.
5. Repeat on the same day only after a new 25-min shading. Target: ≥ 6 valid runs on
   ≥ 3 different days.

A run is valid when: G stays within ± 2 % of its mean over the run; no cloud marker; all
five temperature sensors present; the temperature span of the used sweeps is ≥ 12 °C.

The opaque board is used here on purpose. For spike tests an opaque cover is avoided
(CLAUDE.md) because it creates an unrealistically large step; for γ a large temperature
span is exactly what is needed.

## 3. Data reduction

For each sweep `i`:
1. `P_mp,i`, `V_oc,i`, `I_sc,i` from the sweep points (§4).
2. `G_i` = mean of the PYR20 readings taken just before and just after the sweep. Reject
   the sweep if the two differ by more than 2 %.
3. `T_cell,i = T_module,i + ΔT_cb · G_i / 1000`, `ΔT_cb = 3 °C` (King et al. 2004, as in
   PROJECT_STATE §5.1). **Drop the first 2 min after uncovering** `[ASSUMPTION]`: the
   cells heat before the back sheet does, and the steady offset is not yet established.
4. Irradiance-corrected power `P*_i = P_mp,i · G_ref / G_i`, with `G_ref` = mean G of the
   run. The correction is linear and small (≤ 2 %) by the validity rule.
5. Ordinary least squares of `P*` on `T_cell`: `P* = a + b · (T_cell − 25)`.
6. `γ = b / a`.

The same regression on `V_oc` gives β (with `V_oc` first corrected by a term in
`ln(G_i/G_ref)`), and on `I_sc · G_ref / G_i` gives α. Report α, β and γ per run and their
mean and standard deviation over runs.

**Cross-check without the pyranometer.** Use the sweep's own `I_sc` as the irradiance
reference: regress `P_mp / I_sc` on `T_cell`. Its relative slope is `γ − α`. With α from
step 6 this gives a second γ that does not depend on PYR20 stability inside the run. The
two values should agree within their uncertainties; if not, the PYR20 drifted.

## 4. P_mp, V_oc and I_sc from one sweep

- `P_mp`: fit a parabola to P(V) over the points with `P ≥ 0.97 · max(P)` (at least 5
  points; the firmware's fine pass provides them). Take its vertex. Fit error
  `[MEASURE]`: standard deviation of `P_mp` over 10 back-to-back sweeps at steady G
  (bench step B8); target ≤ 0.3 % `[ASSUMPTION]`.
- `V_oc`: the reading at D = 0 (load off), taken at the start of the sweep.
- `I_sc`: the load cannot short the module. At D = 1 the voltage is about
  `I_sc · 0.75 Ω ≈ 4.4 V` `[EST]`. Fit a line to I(V) for `V < 0.5 · V_oc` and extrapolate
  to V = 0. The slope there is small, so the extrapolation error is small `[MEASURE]`.
- Cable correction before any of this: `V = V_meas + I · R_cable` (uncertainty budget §5).

## 5. Error propagation

`γ = b / a`, so `u_r(γ)² = u_r(b)² + u_r(a)² − 2·r·u_r(a)·u_r(b)`. With T centred near the
middle of the span, a and b are nearly uncorrelated and `u_r(a)` is tiny, so
`u_r(γ) ≈ u_r(b)`.

**Type A (scatter), one run.** `u(b) = s / √Σ(T_i − T̄)²`, s = residual standard deviation.
Example `[EST]`: s = 0.3 % of P, N = 70 sweeps spread over 15 °C (σ_T ≈ 4.3 °C):
`u(b) = 0.3 / (4.3 · √70) ≈ 0.008 %/°C`, i.e. ≈ 2 % of a γ of −0.4 %/°C (illustrative
value, not ours). Scatter inside a run is not the problem; the systematic terms are.

**Type B (systematic).** Each is a bias in the slope equal to (error that changes from
the start to the end of the run) ÷ (temperature span ΔT_span).

| Term | Mechanism | Size, relative to γ | Basis |
|---|---|---|---|
| Cell-to-back offset | `ΔT_cb` builds from 0 (shaded, isothermal) to ≈ 2.7 °C (900 W/m²). Ignoring it over a 20 °C back-sheet span overstates \|γ\| by 13 %. With the correction of §3, the residual is `u(ΔT_cb)·0.9 / ΔT_span = 0.52/20` | ≈ 2.6 % | `[ASSUMPTION]` u(ΔT_cb) = 0.58 °C |
| Irradiance correction | PYR20 error that drifts by ε during the run biases the slope by `ε / ΔT_span`. ε = 0.5 % over 20 °C gives 0.025 %/°C | ≈ 6 % for γ = −0.4 %/°C | `[ASSUMPTION]` ε; `[MEASURE]` PYR20 repeatability (budget §4) |
| Temperature scale | DS18B20 error that varies by 0.2 °C across the span | ≈ 1 % | `[ASSUMPTION]` |
| Temperature non-uniformity | cells hotter or cooler than the five sensed points | `[MEASURE]`: spread of T1..T5 during the run | — |
| Electrical gain (V, I) | constant factor on P: cancels in `b / a` | 0 | — |
| Cable drop | corrected; residual acts on the I-V shape, nearly constant at steady G | negligible | `[EST]` |

Combined, one run `[EST]`: `√(2² + 2.6² + 6² + 1²) ≈ 7 %` (k = 1).

**Between runs.** The reported value is the mean over n ≥ 6 runs. Its uncertainty is
`√(s_runs²/n + u_B²)`, where `s_runs` is the standard deviation between runs and `u_B` is
the part of the Type B terms that is common to all runs (the cell-to-back offset and the
temperature scale: ≈ 2.8 %). Target: `u_r(γ) ≤ 10 %` (k = 1). If the run-to-run spread is
larger than this, the method is not under control and the paper reports that.

## 6. Method B (cross-check): many days, narrow irradiance bins

From the normal 60-s sweeps of the whole campaign, on clear periods only:
1. Keep sweeps with G within 850–950 W/m² and a stable G (± 2 % over the last 10 min),
   and no spike event in progress.
2. Regress `P_mp · 900 / G` on `T_cell`.

This is near steady state (no lag problem) and has thousands of points, but temperature,
sun angle and spectrum all change together with time of day and season, so the slope can
be confounded. Use it only to check that Method A is plausible. A useful subset: pairs of
sweeps at equal times before and after solar noon on the same clear day. The sun angle
and air mass are nearly the same, while the afternoon module is warmer.

## 7. What the paper reports

- γ, α, β: mean ± expanded uncertainty (k = 2), number of runs and days, irradiance and
  temperature ranges.
- Both γ values (pyranometer-normalised and I_sc-normalised) and the Method B slope.
- One figure: `P*` against `T_cell` for all runs, with the fit.
- A sentence stating that the module has no datasheet coefficients to compare with, and
  the typical crystalline-silicon range from a cited source (chosen in Stage 3).

## 8. Needed from the firmware and the bench

- `GAMMA_RUN` mode: sweep every 15 s for a set duration (firmware spec §3).
- PYR20 read immediately before and after each sweep (firmware spec §4).
- Sweep repeatability and cable resistance (bench steps B2, B8).

## Sources (accessed 2026-11-11)

- IEC 60891:2021, Edition 3.0, *Photovoltaic devices — Procedures for temperature and
  irradiance corrections to measured I-V characteristics*. Abstract only:
  https://webstore.iec.ch/en/publication/61766
- King, Boyson, Kratochvil (2004), Sandia array performance model — as recorded in
  PROJECT_STATE §5.1; re-checked in Stage 3.
