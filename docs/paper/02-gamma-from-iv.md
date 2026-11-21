# Measuring γ (temperature coefficient of P_mp) from I-V sweeps

Status: method design, 2026-11-11. Needs the PWM load and sweep firmware
(`07-load-firmware-spec.md`). No value of γ exists yet for our module; the seller listing
gives none, and none may be assumed.

**2026-11-21: the PWM load was dropped** (charge controller + battery + lamp instead), so
no I-V sweeps will exist and Methods A and B below cannot be run as written. They are kept
as the reference design. The proposed replacement is **Method C, §9 — PENDING the
supervisor's approval**; until he approves it, the paper has no agreed way to obtain γ.

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

## 9. Method C (proposal, PENDING supervisor approval): γ from controller-held power over many days

Drafted 2026-11-21 for the supervisor. Nothing here has been measured or tried.

**Idea.** With no sweep, the only power we have is the power at the point where the charge
controller holds the module. If that point is the maximum-power point, the 10 s rows give
`P_mp` directly, and γ is the slope of irradiance-normalised power against cell
temperature, collected over many days instead of inside one 20-min run. It is Method B
(§6) with the controller's operating point in place of the sweep's `P_mp`.

**It only works if two things are true, and both must be shown from our own data first:**

1. *The controller really tracks the MPP.* Many low-cost units sold as "MPPT" connect the
   module to the battery through a switch (PWM type). The module is then held near the
   battery voltage (≈ 12–14.5 V for a 12 V battery), on the flat part of the I-V curve
   where the current barely depends on temperature, and **γ cannot be seen at all**.
   **Go/no-go, before any row is used for γ** (procedure: `firmware/BRINGUP.md` §12): at
   clear midday with the lamp on, the logged module voltage must sit near the listed
   `V_mp` (18 V at 25 °C; ≈ 15–18 V on a hot module `[EST]`) and at least 2 V above the
   battery voltage. If it follows the battery voltage, Method C is dead and the supervisor
   is told that day. The controller is **not bought yet**: it must be a real MPPT, chosen
   for that (a datasheet that states the tracking method and the PV input voltage range),
   not for the word on the label.
2. *The controller is not curtailing.* When the battery is full (absorption / float) or
   the lamp draws less than the module can give, the controller moves the module off the
   MPP toward `V_oc`: power falls for a reason that has nothing to do with temperature.
   Those rows must be removed (rule below). Hardware plan (decision of 2026-11-21,
   replacing the 60–80 W plan of the same day; nothing bought): lamp **80–100 W**, at or
   above the module's midday power, switched ON by the ESP32 (GPIO33) only in a window of
   about ± 2 h around solar noon and only in strong sun, OFF otherwise so the module
   recharges the battery (`firmware/BRINGUP.md` §13). AGM battery ≥ 26 Ah; third INA226
   in the battery lead, reading net current. In the window the lamp takes everything the
   module gives, the battery supplies the small difference and is never full: the
   controller has no reason to curtail, which is exactly when the rows are used.
   Energy balance `[EST, BRINGUP §13]`: on a clear day a 100 W lamp takes ≈ 110 Wh (35 %)
   from a 26 Ah battery in the window and the rest of the day gives back about as much,
   with no margin; an 80 W lamp takes ≈ 30 Wh but is below the module's peak. The battery
   **can** fall below 50 % in a single window with weak sun, so the lamp is tied to
   irradiance and battery voltage, not to the clock alone. `[OPEN]` battery datasheet
   (charge-current limit, capacity at a 4 h discharge, cycle life).
   Rows within a few minutes of a lamp switch are not used.

**Row selection.** A row is used when all hold:
- controller state = bulk (not absorption, float or off). Source, in this order: the
  controller's own state if the unit that is bought exposes one; otherwise battery
  voltage and current from a **third INA226-10A** on PaHub channel 2 (to buy; the second
  unit stays on the pump, the reference cell will be a fourth in Stage 2): bulk = battery
  voltage below the controller's absorption setpoint; otherwise the module-side test
  alone: `V_module` below the curtailed voltage found in the go/no-go check
  `[ASSUMPTION until that check]`;
- lamp ON for at least 5 min `[ASSUMPTION]` (logged lamp state);
- G within 850–950 W/m², stable to ± 2 % over the last 10 min; clear sky, within ± 2 h of
  solar noon (limits the angle-of-incidence and spectrum changes the PYR20 does not see
  the way the module does);
- module cleaned that morning; no spike event, cleaning, shading or marker window open;
  all five temperature sensors present.

**Data reduction.** `T_cell` as in §3 step 3. `P* = P · 900 / G`. Ordinary least squares
`P* = a + b · (T_cell − 25)` over all used rows; `γ = b / a`. Also fit each day alone and
report the spread of the daily slopes. The before/after-noon pairs of §6 are the most
convincing subset: same sun angle, warmer module in the afternoon.

**What is weaker than Method A, to say plainly to the supervisor and in the paper:**
- *No proof of MPP in each row.* Method A fits the maximum of a measured curve. Here a
  tracking error of the controller that changes with temperature or irradiance goes
  straight into γ. The controller's tracking efficiency is not known and cannot be
  measured without a sweep `[OPEN]`; one borrowed I-V tracer or electronic load for a day
  would bound it.
- *Confounding.* Temperature, time of day, season, spectrum and soiling change together
  over days; a narrow G bin and the noon window reduce this but do not remove it. Method A
  changed temperature alone in 20 min.
- *Small temperature span.* Inside 850–950 W/m² the cell temperature moves mainly with
  air temperature and wind, perhaps 10–15 °C over the campaign `[EST]`, against 20 °C in
  one Method A run. With the PYR20's 5 % accuracy class, a drift of the irradiance scale
  of 0.5 % over the span is ≈ 10 % of a γ of −0.4 %/°C (illustrative value, not ours).
- *No α, β, no `V_oc`, no `I_sc`:* the cross-check of §3 (normalising by `I_sc`) is gone.
- Expected uncertainty: not estimated yet; it will be larger than the 10 % target of §5.
  The paper would report γ as "estimated from operating data under MPPT", with its
  confidence interval from the day-to-day spread, and would not call it a measurement
  from I-V curves.

**Questions for the supervisor.**
1. Is γ from operating data acceptable for the paper's power-cost figure, given the limits
   above? The alternative is one borrowed I-V tracer for a few Method A runs.
2. His 2026-11-03 direction was no battery and no charge controller. Does the new load
   (controller + battery + lamp) have his agreement?
3. May a third INA226 be bought for the battery side, to detect bulk / float?

**Status of the hardware (2026-11-21).** Controller: not bought; must be a real MPPT (the
Anern 30 A unit in the old BOM has not been checked for tracking or for a data port, and
neither may be assumed). Battery: AGM 12 V, 26 Ah or more, not bought (the 18 Ah QSSUN
is no longer the plan). Lamp: 80–100 W, not chosen, solar-noon window only. Third INA226-10A for the battery: to buy, PaHub channel 2, address
0x41 (`firmware/PINMAP.md`). The logger gains battery columns and a charge-state column
only once the unit is on the bench and the supervisor has approved the method.

## Sources (accessed 2026-11-11)

- IEC 60891:2021, Edition 3.0, *Photovoltaic devices — Procedures for temperature and
  irradiance corrections to measured I-V characteristics*. Abstract only:
  https://webstore.iec.ch/en/publication/61766
- King, Boyson, Kratochvil (2004), Sandia array performance model — as recorded in
  PROJECT_STATE §5.1; re-checked in Stage 3.
