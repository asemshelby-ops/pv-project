# Assumptions of the twin

> SIMULATION. This is the list of everything the model assumes or leaves out. Each item
> says what it would change if it were wrong. Values and their sources:
> [parameters.md](parameters.md). What replaces them: [placeholders.md](placeholders.md).

## A. Module, electrical

| # | Assumption | What it leaves out / effect if wrong |
|---|---|---|
| A1 | The module follows the single-diode model with De Soto's scaling. | Low-light behaviour, spectral and angle-of-incidence effects are not modelled; power at low sun may be off. |
| A2 | The four numbers of the seller listing are right, and R_sh = 500 ohm. | The listing is not a datasheet. All electrical values move with it. The label of the module must be read. |
| A3 | 36 cells in series; one bypass diode per module with a 0.5 V drop. | The cell count is unknown. A 36-cell module often has two diodes: partial shading of half a module is not represented. |
| A4 | The temperature dependence is De Soto's with the silicon band gap: gamma of the model is -0.46 %/degC. | Our module's gamma is UNKNOWN. Every simulated gain from cooling is proportional to gamma. |
| A5 | Cell temperature = back-sheet temperature + 3 degC x G/1000 (Sandia). | Not measurable on our rig. A wetted front changes this offset; not modelled. |
| A6 | Modules of the rooftop differ only by a factor on the photocurrent and on the saturation current. | No spread of R_s, no degraded or faulty modules, no hot spots. |
| A7 | Soiling is one transmission factor per module, uniform over the module, constant during a run. | No partial soiling, no build-up over days, **no washing by the spray** (CLAUDE.md: washing raises power and must be separated from cooling). Soiling reduces the light on the cells but not the heating. |
| A8 | Every module sees the same in-plane irradiance at the same instant. | No row shading, no cloud edge crossing the roof, no tilt or azimuth differences. |
| A9 | One ideal maximum-power-point tracker per string; it always finds the global maximum. | No inverter efficiency, no MPPT error, no cable loss, no clipping. Energies are DC at the string terminals. |
| A10 | 120 x 100 W modules in 6 strings of 20. | Not a plant design: a real string length follows from the inverter's voltage window and the cold-day V_oc, and a 12 kW roof would use larger modules. |

## B. Module, thermal

| # | Assumption | What it leaves out / effect if wrong |
|---|---|---|
| B1 | One lumped heat capacity per module, uniform temperature. | No gradient glass-cell-back sheet; the sub-minute response to a spray on the glass is smoothed. |
| B2 | Heat loss = (u0 + u1 v)(T - Ta), with pvlib's Faiman defaults. | No separate sky radiation, no dependence on mounting. u0, u1 are for open-rack modules in general, not for our stand or a roof. |
| B3 | The full in-plane irradiance heats the module (Faiman convention). | The temperature does not depend on whether the module is loaded; an open-circuit module is in reality a little warmer. |
| B4 | Wind at a module = site wind x a fixed exposure factor; direction is ignored. | Real shelter depends on wind direction and roof geometry. The factors are invented. |
| B5 | Without an anemometer the wind is 1.5 m/s all the time. | The twin then cannot follow wind lulls, the main driver of spikes in the synthetic data (see the validation demo). |
| B6 | Each simulated day starts at 06:00 with modules and tank at the ambient temperature. | No night-time cooling below ambient, no carry-over of a warm tank. |

## C. Spray, evaporation, tank

| # | Assumption | What it leaves out / effect if wrong |
|---|---|---|
| C1 | Heat to the water follows an effectiveness model with one conductance U_mw, set from the team's estimate (5 degC rise at 2 L/min, module 20 degC above the water). | **Not measured and not from literature.** Real sprays wet unevenly, splash and run in rivulets. This number scales all cooling by the water. |
| C2 | The wetted share of the glass grows linearly with flow up to 1 L/min per module. | Invented. It decides how well a low pump duty still cools. |
| C3 | Evaporation from the wetted glass follows the Chilton-Colburn analogy with the McAdams wind coefficient, at the mean film temperature; all latent heat comes from the module. | A dry-plate correlation applied to a wet module. In the model it is about as large as the heat carried by the water, so **the sign of the net result depends on it** (sensitivity 1 in the results). An upper-bound choice: part of the latent heat really comes from the water. |
| C4 | Evaporation stops the moment the valve closes. | The film left on the glass keeps cooling for a while (this decay is how the term would be calibrated). |
| C5 | Water on the glass changes neither reflection nor transmission. | Small optical gain or loss ignored. |
| C6 | All modules of an open zone receive the same flow; all open zones the same duty. | No pressure drop along the pipe, no blocked nozzle. |
| C7 | Closed loop: 95 % of the sprayed water returns to one fully mixed, shaded tank of 20 L per module; the rest is lost; make-up water is at ambient temperature. | The 5 % loss is invented and dominates "water used" at high flow. Sun on tank, gutter and pipes (CLAUDE.md: the bigger risk) is not modelled. The returning water does not cool by its own evaporation. |
| C8 | No limit on the temperature difference between water and hot glass. | Thermal shock is not modelled; a real controller needs a guard (PROJECT_STATE.md). |
| C9 | Water quality, scaling and nozzle wear are ignored. | - |

## D. Pump and valves

| # | Assumption | What it leaves out / effect if wrong |
|---|---|---|
| D1 | One pump unit = the R385 listing: 1.75 L/min and 7.2 W at full duty, flow and power proportional to the duty, running from duty 0.5. | The listing gives free flow; through nozzles the flow is lower and the current different. Proportionality to duty is assumed. |
| D2 | The rooftop pump draws (number of sprayed modules) x (unit power) x 1.0. | A linear copy of the rig. A real installation has one larger pump with another efficiency, pipe losses and a pressure requirement of the nozzles. Net energy is very sensitive to this. |
| D3 | The pump is sized for all six zones (`max_zones_open` = 6). | An undersized pump can be simulated; then the hottest zones are served first. |
| D4 | Valves open and close instantly, hold 5 W each while open, and never fail. Dwell times 30 s open, 60 s closed. | No stuck valve, no leak. The simulated valve counts (hundreds of operations per day for some strategies) are not checked against the life of a real valve. |
| D5 | Pump and valves are fed without conversion loss; their energy is subtracted from the DC energy. | Supply efficiency ignored. |

## E. Sensors, detector, controllers

| # | Assumption | What it leaves out / effect if wrong |
|---|---|---|
| E1 | Five back-sheet sensors per string, on five different modules, with the noise, offset and step of `config.py`. | The noise values are assumptions of the synthetic pipeline, not DS18B20 measurements. Per-module sensors do not scale to plants (PROJECT_STATE.md); five per string is a middle choice. |
| E2 | Controllers know irradiance, ambient temperature, humidity and tank temperature exactly; wind only if the weather table has it. | No weather-sensor error; the synthetic weather has wind, our rig has none in this phase. |
| E3 | The model-based controllers hold the same parameter values as the plant. | Optimistic: a real controller's model is wrong. Sensitivity 2 runs the predictive controller with a model that is off by 30 %. |
| E4 | NASIM-GBT is the model trained by the paper pipeline on SYNTHETIC single-module data, used unchanged per zone on the mean of the string's five sensors. | It was never trained on a cooled module or on a string. Its behaviour here says nothing about its field performance. |
| E5 | After a burst a zone's detector is blind for 15 min and then warms up for 10 min. | A second spike within 25 min is missed. |
| E6 | The predictive controller assumes that irradiance and wind persist (no forecast) and decides greedily, second by second. | It is not an optimal controller: it ignores that spraying warms the shared tank and that waiting for a hotter module takes more heat per pump-second (sensitivity 2). |

## F. Weather

| # | Assumption | What it leaves out / effect if wrong |
|---|---|---|
| F1 | Weather = synthetic days of `data/synth.py`, hard variant, starting 15 November: clear-sky irradiance for Makkah with invented clouds, haze, wind lulls and ambient steps (all `[ASSUMPTION]` in `config.py`). | **No measured Makkah weather.** Ambient 24-28 degC mean: a mild season; summer is hotter. How often spikes occur is the open question of the paper itself. |
| F2 | The logged window 06:00-18:00 only; five days in the reported run. | Five days of one seed: the differences between strategies are not statistically assessed. |
| F3 | The weather is the same for the whole roof and is not changed by the spray. | - |

## G. What the results are

- Net energy = DC energy of the strings - pump energy - valve energy (A9, D5).
- A comparison of strategies **inside one model**, on the same weather, rooftop and
  sensor noise. Not an estimate of the yield of any real installation.
