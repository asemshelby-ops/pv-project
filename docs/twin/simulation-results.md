# First runs of the twin (2026-11-13)

> **SIMULATION - digital twin with placeholder parameters. Not a measurement; no claim
> about a real plant.** Every number on this page comes from `twin/` with the placeholder
> values of [parameters.md](parameters.md) and synthetic weather. The numbers show that
> the tools run and how the strategies behave **inside this model**. They are not results
> of the project and must not be quoted without the word "simulation".

Reproduce: `python -m analysis.demo_synth` (once, for the NASIM model), then
`python -m twin.validate --demo` and `python -m twin.compare`. Outputs in `out/twin/`.

## 1. Validation hook, tested on synthetic rig files

`python -m twin.validate --demo` writes ten synthetic days in the logger's own file
format (UTC, wind = `NaN` as on the rig, plus the sweep files of four gamma runs), in which
one hidden module produces both the 10 s power and the sweeps. That hidden module is
deliberately **not** the twin's placeholder (single-diode curve of `analysis/fake_logger.py`:
94.7 W at reference conditions against the placeholder's 99.9 W). The hook then runs three
times. Twin minus "measured", 36 119 daylight rows:

| Run | Module temperature | Power (twin's own temperature) | P_mp against sweep maxima (324 sweeps) |
|---|---|---|---|
| 1. Placeholders, logger files (no wind) | bias +1.99, RMSE 2.49 degC | bias +2.79 W, RMSE 7.2 % of the mean | RMSE 8.6 % |
| 2. Placeholders, the generator's table with its wind column | bias +0.07, RMSE 0.10 degC | bias +3.38 W, RMSE 8.7 % | - |
| 3. After `twin.fit` on the files of run 1 | bias -0.02, RMSE 1.15 degC | bias +0.09 W, RMSE 1.1 % | RMSE 0.3 % |

What this shows, and what it does not:

- The hook reads the logger's files, replays irradiance, ambient and wind, and reports the
  error per day and overall. It works on files without wind (run 1) and with wind (run 2).
- The fit replaces nine placeholders through the overlay file only, and the error falls:
  the route "measure, fit, validate" works end to end without a code change.
- Run 2's temperature error of 0.1 degC is **not** a statement about model accuracy: the
  synthetic generator contains the same thermal equation as the twin. It only proves that
  the replay is implemented correctly.
- Run 3 keeps 1.15 degC RMSE because the files hold no wind: the twin runs at a constant
  1.5 m/s and cannot follow the wind lulls of the synthetic weather. The same will be true
  on the rig as long as there is no anemometer.
- The synthetic sweeps were all taken near 900 W/m2, so the fit's behaviour over a range
  of irradiance is not tested yet.

## 2. Five strategies on the same weather and the same rooftop

Set-up: 120 module twins (6 strings x 20, 12.0 kW at reference conditions, model value),
rooftop seed 1 (photocurrent factor 0.973-1.021, soiling 0.919-0.989, wind exposure
0.52-1.24), five synthetic days 15-19 November (hard variant, seed 21, peak 938 W/m2),
06:00-18:00, 10 s steps. NASIM-GBT is the model the paper pipeline trained on synthetic
data; weather seed 21 was not in its training. Net energy = DC energy of the strings -
pump - valves. All 34 physical parameters are placeholders, including the pump curve.

| Strategy | DC energy, kWh | Pump + valves, kWh | **Net energy, kWh** | Net vs no cooling | **Water used, L** | Water circulated, m3 | **Pump hours** | Valve openings | Mean module T, degC |
|---|---|---|---|---|---|---|---|---|---|
| No cooling | 273.52 | 0.00 | 273.52 | - | 0 | 0.0 | 0.0 | 0 | 44.8 |
| Continuous (full duty) | 296.79 | 44.89 | 251.90 | -7.90 % | 34 691 | 632.6 | 50.2 | 120 | 29.5 |
| Fixed threshold 45/35 degC (full duty) | 283.12 | 7.39 | 275.73 | +0.81 % | 5 910 | 104.1 | 19.4 | 760 | 39.4 |
| Spike-triggered (NASIM-GBT) | 273.76 | 0.14 | 273.62 | +0.04 % | 109 | 2.0 | 0.4 | 84 | 44.6 |
| Predictive (gain > pump power) | 292.64 | 13.51 | 279.13 | +2.05 % | 11 158 | 184.2 | 32.6 | 981 | 33.2 |

Five days in total; pump hours are hours in which the pump ran (any number of zones).
"Water used" is evaporation plus the assumed 5 % that does not return to the tank; for
the strategies that spray a lot, most of it is that assumed loss (predictive: about
9.2 m3 of the 11.2 m3).

Reading the table (inside the model):

- **Continuous spraying at full pump duty loses energy.** It gains 23.3 kWh of DC energy
  and spends 44.9 kWh on the pump and valves.
- **The predictive rule gives the largest net gain of the five, +2.05 %.** It sprays 6.5 h
  a day on average and pays for it with about 2 000 L of water per net kWh gained.
- **Spike-triggered control gains almost nothing in energy (+0.04 %)**, with 0.4 pump
  hours and 109 L in five days: 84 short bursts on 119 detector alarms. This agrees with
  what CLAUDE.md already notes from the team simulation: a strategy that cools most of the
  day nets more energy than one that answers spikes. The value of the spike detector is
  detection and a small, event-triggered use of water, not net energy.
- Differences of a few tenths of a percent between strategies on five days of one weather
  seed are not assessed statistically.

### Sensitivity 1: evaporation switched off in the plant

| Strategy | Net energy, kWh | Net vs no cooling | Water used, L | Pump hours | Valve openings |
|---|---|---|---|---|---|
| No cooling | 273.52 | - | 0 | 0.0 | 0 |
| Continuous | 235.37 | -13.95 % | 31 631 | 50.2 | 120 |
| Fixed threshold 45/35 degC | 250.63 | -8.37 % | 20 675 | 34.6 | 76 |
| Spike-triggered (NASIM-GBT) | 273.59 | +0.03 % | 109 | 0.4 | 64 |
| Predictive | 273.96 | +0.16 % | 2 299 | 12.4 | 2 399 |

Without the evaporation term no strategy gains anything worth the water: the closed loop
warms its own tank (to 45 degC under continuous spraying) and the water then cools little.
The fixed threshold never reaches its 35 degC switch-off and keeps pumping at a loss; the
predictive rule stops when the pump costs more than the expected gain and ends near zero,
but with about 80 valve operations per valve and day, which a real valve would not take.
**Whether spraying pays in this model is decided by the evaporation placeholder**, a
textbook correlation that has not been checked on a wet module.

### Sensitivity 2: the predictive controller with a wrong model, or a higher switching ratio

| Controller | Net vs no cooling | Water used, L | Pump hours | Valve openings |
|---|---|---|---|---|
| Predictive, right model, on/off ratio 1.2/1.0 (the default above) | +2.05 % | 11 158 | 32.6 | 981 |
| Model expects 30 % less cooling | +2.40 % | 7 540 | 25.5 | 1 735 |
| Model expects 30 % more cooling | +1.59 % | 13 191 | 37.2 | 736 |
| Right model, on/off ratio 1.6/1.3 | +2.28 % | 9 398 | 29.1 | 1 108 |
| Right model, on/off ratio 2.0/1.6 | +2.38 % | 7 436 | 24.9 | 1 385 |

- A model that is off by 30 % moves the result by a few tenths of a percent; the
  controller still gains.
- The rule "spray as soon as the expected gain exceeds the pump power" is **not the best
  setting of its own rule**: demanding a larger margin gives a little more net energy with
  a third less water. A likely reason, not yet verified: a hotter module gives up more
  heat per second of pumping, and less spraying keeps the shared tank cooler. The rule
  decides second by second and sees neither effect (assumption E6).

### Sensitivity 3: other settings of the two classical strategies

Scored on the same five days they would be chosen on, so the best row is optimistic.

| Strategy | Net vs no cooling | Water used, L | Pump hours | Valve openings |
|---|---|---|---|---|
| Continuous, duty 0.5 | -0.45 % | 18 649 | 50.2 | 120 |
| Continuous, duty 1 | -7.90 % | 34 691 | 50.2 | 120 |
| Fixed 40/30 degC, duty 0.5 | +2.00 % | 10 501 | 31.8 | 222 |
| Fixed 40/30 degC, duty 1 | -1.44 % | 17 612 | 31.2 | 349 |
| Fixed 45/35 degC, duty 0.5 | +1.94 % | 3 911 | 20.6 | 651 |
| Fixed 45/35 degC, duty 1 | +0.81 % | 5 910 | 19.4 | 760 |
| Fixed 50/40 degC, duty 0.5 | +0.84 % | 906 | 8.8 | 327 |
| Fixed 50/40 degC, duty 1 | +0.56 % | 1 411 | 8.0 | 337 |
| Fixed 55/45 degC, duty 0.5 | +0.12 % | 81 | 1.1 | 41 |
| Fixed 55/45 degC, duty 1 | +0.09 % | 129 | 0.9 | 42 |

- **The pump duty matters more than the choice of strategy.** Halving the duty turns the
  fixed threshold from +0.81 % into +1.94 % and continuous spraying from -7.9 % into
  -0.45 %: in the model half the flow still takes most of the heat (section 4 of
  [model-equations.md](model-equations.md)).
- **A fixed threshold at the lowest duty (+1.9 to +2.0 %) does as well as the predictive
  rule (+2.05 %)**, and 45/35 at half duty does it with a third of the water. In this
  simulation the predictive rule is not better than a well-set thermostat. Its advantage
  is elsewhere: it needs no set point chosen on the days themselves, and it does not run
  at a loss when cooling cannot pay (sensitivity 1: +0.16 % against -8.4 %).

## 3. What these runs are good for

- They are a working test bench: a new controller can be added to `twin/controllers.py`
  and compared in ten minutes on the same weather, rooftop and sensor noise.
- They point at what to measure first: the pump curve at low duty and the evaporation
  (see [placeholders.md](placeholders.md)). Until then even the sign of the net result is
  open. The one-at-a-time ranking of 2026-11-14 ([ranking.md](ranking.md)) confirms it:
  evaporation and pump power are by far the largest effects (9.2 and 5.2 points) and
  the only ones that turn both fixed-threshold settings into a loss.
- They give development leads, each to be re-checked after the placeholders are replaced:
  a margin in the predictive rule; the tank temperature as a state the controller should
  plan with; a limit on valve operations.

## 4. What these runs are not

- Not a measurement and not a forecast of any real installation.
- Not evidence that spray cooling pays, nor that it does not.
- Not a result on NASIM's detection quality: the detector was trained on synthetic
  single-module data and is used here on simulated strings.
- Not a plant design: the rooftop is our rig multiplied by 120
  ([assumptions.md](assumptions.md), A10, D2).
