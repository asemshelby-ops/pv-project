# Phase B, part 3 — equivalent cell temperature from V_oc

Status: prepared 2026-11-17, **not adopted**, tested on synthetic sweeps only. Switch
`ECT_ENABLED` in `config.py`, off. Code: `analysis/ect.py`; tests: `tests/test_ect.py`.
**No conformity with IEC 60904-5 is claimed** (see §5).

## 1. Why

The five DS18B20 sit on the back sheet. The cells are a few degrees warmer in the sun and
react sooner to a change. The open-circuit voltage is set by the cells themselves and
falls as they warm, so it can serve as a thermometer inside the module: the *equivalent
cell temperature* (ECT). Every I-V sweep of the measurement load already measures V_oc,
so the ECT costs no extra hardware and no extra measurement.

## 2. Method

Equation (4) of IEC 60904-5:2011 [1]:

```
ECT = T_ref + (1 / β) · [ V_oc / V_oc,ref − 1 − a · ln(G / G_ref) ]
```

| Symbol | Meaning | Value today |
|---|---|---|
| V_oc | open-circuit voltage of the sweep | measured |
| G | in-plane irradiance at the sweep (PYR20) | measured |
| V_oc,ref | V_oc at the reference condition (G_ref, T_ref) | 21.5 V `[PLACEHOLDER]`: seller listing, assumed to be at 1000 W/m² and 25 °C |
| β | relative temperature coefficient of V_oc, 1/°C | −0.0030 `[PLACEHOLDER]`: **not in the listing and not this module's value**; an illustrative figure for crystalline silicon |
| a | the standard's irradiance constant ("thermal diode voltage", relative to V_oc) | 0.05 `[PLACEHOLDER]` `[EST]` from n·N_s·kT/q ÷ V_oc with 36 cells and n = 1.2, both assumed |

The irradiance term: at the same temperature V_oc rises with the logarithm of the light;
the term takes that part out so that what remains is temperature. The standard also
allows the ratio of short-circuit currents, `ln(I_sc / I_sc,ref)`, in place of
`ln(G / G_ref)` ("self-reference"), which removes the pyranometer from the result
(`ECT_USE_ISC`, off; `ECT_ISC_REF_A` = 5.88 A `[PLACEHOLDER]`, the listing).

Which sweeps are used: sweeps that are usable for any sweep result (no firmware flag,
steady irradiance during the sweep), with a measured V_oc and G ≥ 400 W/m²
(`ECT_MIN_G_WM2`, the limit of Amendment 1 [2]).

**Replacing the placeholders by fitted values** (`fit_parameters`). On sweeps taken while
the back-sheet temperature is steady (changing by less than 0.2 °C/min before and after),
a least-squares fit of

```
V_oc = V_oc,ref · [ 1 + β · (T − T_ref) + a · ln(G / G_ref) ]
```

gives V_oc,ref, β and a for this module. T is the back-sheet mean plus the Sandia
cell-to-back offset, 3 °C × G/1000 [4]. The fit needs at least 30 steady sweeps spanning
5 °C and a factor 1.2 in irradiance; otherwise the placeholders stay and the report says
so.

## 3. Comparison with the DS18B20 mean

- **Offset.** ECT minus back-sheet mean over all usable sweeps: median, spread, and its
  slope against irradiance. The Sandia model expects about +3 °C per 1000 W/m² for an
  open-rack glass / cell / polymer-sheet module [4].
- **Lag during spikes.** For every valid spike, over the spike and 10 min on each side:
  the ECT series (one value per sweep) is paired with the back-sheet series (one value
  per 10 s) shifted by a trial lag L, for L from −300 s to +300 s; the L with the highest
  correlation is taken and refined by a parabola through its neighbours. A positive lag
  means the back sheet follows the cells. A spike needs at least 8 usable sweeps.
  Reported: the median lag and its quartiles over the spikes.

## 4. What the numbers can and cannot mean

- With the placeholder values, the **level** of the ECT means nothing: an error of 0.3 %
  in V_oc,ref alone is about 1 °C (the standard's own example [2]), and β is not this
  module's. Its **changes over minutes** depend on β only as a scale factor, and the
  **lag** does not depend on the three values at all. So: lag yes, shape roughly, level
  no.
- With fitted values the ECT agrees with the sensors on average **by construction**,
  because the sensors were the fit's temperature. The fitted level is therefore not an
  independent measurement either. What stays informative is the lag, and how far the two
  move apart during fast changes.
- The standard is written for **stable conditions**. Using it through a spike, which is
  a fast change, goes beyond it: the module is not in thermal equilibrium then. The lag
  result is exploratory.
- One sweep every 1–2 minutes is coarse against a lag of tens of seconds; the estimate
  rests on the parabola between sample points and on many spikes, not on one.
- An independent level needs a calibration of this module (the standard requires one):
  V_oc at known temperatures and irradiances. The γ runs planned for the paper
  (`docs/paper/02`: module shaded, then uncovered, sweeps while it heats) record V_oc
  against temperature at nearly constant irradiance as a by-product and could supply β.
  Whether that procedure satisfies IEC 60891 has not been checked (§5, source 3).

## 5. Sources, and how far each was read

1. **IEC 60904-5:2011, Edition 2.0**, *Photovoltaic devices – Part 5: Determination of
   the equivalent cell temperature (ECT) of photovoltaic (PV) devices by the open-circuit
   voltage method*. Read in the publisher's free preview (standards.iteh.ai, sample
   pages): scope, general requirements, the definition of *a* (its Eq. (2), from V_oc at
   two irradiances and one temperature) and Eq. (4) as written above. The full text was
   **not** available; the clauses on apparatus and reporting were not read.
2. **IEC 60904-5:2011 + AMD1:2022 (Edition 2.1, consolidated)**. Read in the same
   preview: the lower irradiance limit is raised from 200 to 400 W/m²; the method
   "requires calibration of the device to be measured" and the calibration of another
   device of the same type is not sufficient (0.3 % in V_oc ≈ 1 °C); the dependence of
   V_oc on irradiance becomes quadratic in ln G, with two factors B1 and B2 determined
   from I-V curves at five or more irradiance levels. **The amended equations were not
   legible in the preview and are not implemented.** Only the 400 W/m² limit is taken
   over. This is the reason no conformity is claimed.
3. **IEC 60891** (procedures for temperature and irradiance corrections), which the
   standard names for measuring β: **not read**.
4. D. L. King, W. E. Boyson, J. A. Kratochvil, *Photovoltaic Array Performance Model*,
   Sandia report SAND2004-3535, 2004 — the 3 °C cell-to-back offset; already reference
   [1] of `docs/paper/08-literature-review.md`, checked there in full text.

Before the ECT is used in anything that is published, references 1–3 have to be read in
full (the university library, or the supervisor's copy), and the wording above checked
against them.

## 6. Example on SYNTHETIC sweeps

`python -m analysis.demo_phase_b --days 20` (seed 5): 8 237 usable sweeps, one per minute,
from a single-diode module model with assumed coefficients; the generator makes the back
sheet follow the cells with a first-order lag of 45 s and keeps the cells 3 °C × G/1000
above it. **All invented**; nothing here describes the real module.

| Parameters | V_oc,ref, V | β, %/°C | a | ECT − back sheet: median (sd), °C | per 1000 W/m², °C | Lag of the back sheet in spikes: median (quartiles), s |
|---|---|---|---|---|---|---|
| placeholder | 21.50 | −0.300 | 0.050 | +15.0 (3.0) | +19.2 | +35 (+32 to +53), 27 spikes |
| fitted on 4 309 steady sweeps | 21.505 | −0.463 | 0.0500 | +2.2 (0.5) | +3.1 | +35 (+32 to +53), 27 spikes |

What the example shows:
- with a β that is not the module's (here −0.30 against the synthetic module's −0.46),
  the level is wrong by 15 °C and drifts with irradiance. This is the reason the
  placeholder level must not be quoted;
- the lag is the same with both sets of values, as it should be: it does not depend on
  them;
- the fitted offset, +3.1 °C per 1000 W/m², is what the generator put in, and what the
  fit was given as its temperature: agreement by construction, not a finding;
- the lag that was found, 35 s, is of the size of the 45 s put in but not equal to it.
  With one sweep a minute and rises that last a few minutes, the cross-correlation is a
  rough estimator. On the real module it would tell "tens of seconds" from "minutes",
  not more.
