# Which unmeasured quantity decides the result? One at a time (2026-11-14)

> **SIMULATION - digital twin with placeholder parameters. Not a measurement; no claim about a real plant.**

One quantity at a time, every other placeholder at its value in `twin/params.py`. Cells: net energy against no cooling, in %, at the low and the high value. Ranges are assumptions (see `twin/rank.py`); a wider range ranks higher.

| Rank | Quantity | Range (assumed) | Fixed 45/35, full duty: low / high, % (base +0.81) | Fixed 45/35, lowest duty: low / high, % (base +1.94) | Predictive: low / high, % (base +2.05) | Largest change, points | Sign of the result changes | Largest change of water, L |
|---|---|---|---|---|---|---|---|---|
| 1 | Evaporation from the wet glass | low: no evaporation (the bound CLAUDE.md used); high: the McAdams wind coefficient of the placeholder, which includes radiation and free convection and so overstates the mass transfer (refs.md). The Watmuff form, 2.8 + 3.0 v, is a separate row | -8.37 / +0.81 | -3.64 / +1.94 | +0.16 / +2.05 | 9.18 | yes | 14765 |
| 2 | Pump electrical power at a given flow | low: x0.5; high: x3, a typical 12 V spray pump (17-33 W at 2 L/min, CLAUDE.md) against the R385 listing (7.2 W at 1.75 L/min) | +2.12 / -4.41 | +2.73 / -1.22 | +4.07 / +0.04 | 5.22 | yes | 9583 |
| 3 | Pump flow through our nozzles at a given duty | x0.5 / x1.5: the listings give free flow and disagree on the head; the nozzles cut it | +0.36 / +0.81 | +0.29 / +2.11 | +0.13 / +2.41 | 1.92 | no | 5706 |
| 4 | Flow needed to wet the whole glass | x0.5 / x2, no source for the placeholder | +0.81 / +0.37 | +2.11 / +0.30 | +2.41 / +0.13 | 1.92 | no | 3914 |
| 5 | Faiman heat loss u0 and u1 | x0.8 / x1.2 together | +2.15 / +0.20 | +3.76 / +0.93 | +3.90 / +1.01 | 1.85 | no | 2324 |
| 6 | Temperature coefficient gamma of P_mp | -0.30 / -0.50 %/degC (model placeholder: about -0.46) | -0.39 / +1.11 | +0.65 / +2.27 | +0.32 / +2.58 | 1.73 | yes | 3150 |
| 7 | Evaporation, Watmuff coefficient instead of McAdams | low: h = 2.8 + 3.0 v (Watmuff et al. 1977, radiation and free convection removed); high: the placeholder 5.7 + 3.8 v | -0.57 / +0.81 | +1.29 / +1.94 | +1.17 / +2.05 | 1.38 | yes | 3585 |
| 8 | Wind speed at the module | x0.5 / x1.5 of the synthetic wind (no anemometer on the rig) | +0.58 / +0.57 | +2.79 / +1.22 | +2.60 / +1.67 | 0.85 | no | 5269 |
| 9 | Heat transfer module -> sprayed water (spray_u_module_water) | x0.5 / x2, no source for the placeholder | +0.17 / +1.19 | +1.63 / +2.14 | +1.82 / +2.17 | 0.64 | no | 1520 |
| 10 | Relative humidity | x0.5 / x1.5 of the synthetic humidity, capped at 100 % | +1.16 / +0.22 | +2.11 / +1.67 | +2.61 / +1.56 | 0.59 | no | 1577 |
| 11 | Heat capacity of the module | x0.7 / x1.3 (time constant 6.3 +/- 1 min, Herteleer et al. 2023, plus margin) | +0.83 / +0.78 | +1.97 / +1.92 | +2.09 / +2.02 | 0.04 | no | 29 |
| 12 | Heat loss of the tank to the air | x0.2 / x5, no source for the placeholder (sun on the tank is not modelled) | +0.81 / +0.80 | +1.94 / +1.94 | +2.05 / +2.05 | 0.01 | no | 41 |
| 13 | Share of sprayed water not returning to the tank | 0.02 / 0.15 (placeholder 0.05); changes water, not energy | +0.81 / +0.82 | +1.94 / +1.95 | +2.05 / +2.06 | 0.01 | no | 18405 |

Base case water used, L: Fixed 45/35, full duty 5910, Fixed 45/35, lowest duty 3911, Predictive 11158

Reproduce: `python -m twin.rank` (5 synthetic days, weather seed 21, rooftop seed 1, as
[simulation-results.md](simulation-results.md); about 8 minutes on 4 processes; no NASIM
model needed). Output in `out/twin/rank/`. The base row reproduces the main table and
sensitivity 3 of simulation-results.md (+0.81, +1.94, +2.05 %), and the no-evaporation
row reproduces sensitivity 1.

## Reading the table (inside the model only)

- **Two quantities dominate and turn both fixed-threshold settings into a loss: the
  evaporation and the pump's electrical power.** (gamma at -0.30 %/degC and Watmuff's
  coefficient flip only the full-duty threshold.) Evaporation moves net energy by up to 9.2 points (the fixed
  threshold goes from +0.81 % to -8.37 % without it); a pump that draws 3 times the
  R385 listing at the same flow takes the fixed threshold to -4.4 % and the predictive
  rule to zero. Nothing else comes close. Both are measured on the bench or the module in
  sun, not by the paper campaign.
- **Next, at about 2 points: how much water reaches the glass** (pump flow through our
  nozzles, and the flow that wets the whole glass; the two rows are mirror images because
  only their ratio matters in the model), **the Faiman losses u0, u1 and gamma.** gamma
  and u0 come free with the paper's I-V sweeps and logged days; u1 needs wind.
- **The McAdams coefficient alone, against Watmuff's form without radiation and free
  convection ([refs.md](refs.md)), is worth 1.4 points** and makes the full-duty fixed
  threshold lose energy (-0.57 %). The placeholder is the optimistic end.
- **Wind and humidity: 0.6-0.9 points each here.** Wind matters more than this row shows:
  it is the likely driver of spikes (detection, not this energy table) and, without an
  anemometer, it is why the twin's temperature error stays at 1.2-2.5 degC
  (simulation-results.md section 1). Humidity is logged (BME280).
- **Heat capacity and tank heat loss hardly matter for energy** (< 0.05 point): they set
  how fast, not how much. The share of water not returned changes only the water (up to
  18 m3 in five days for 120 modules), not the energy.
- The ranking depends on the assumed ranges, set in `twin/rank.py`: a wider range ranks a
  quantity higher. It says what to measure first, not how uncertain each quantity is.
