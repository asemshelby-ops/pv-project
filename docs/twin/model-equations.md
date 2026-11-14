# Model equations of the twin

> SIMULATION. Every symbol with a value takes it from `twin/params.py`
> ([parameters.md](parameters.md)); all of them are placeholders today. "Model value" below
> means a number that follows from those placeholders, not a property of our module.

Sample period `dt` = 10 s, as the logger. Temperatures in degC, irradiance `G` in W/m2
in the plane of the module.

## 1. Module, electrical (`twin/electrical.py`)

Single-diode model with the scaling of De Soto, Klein and Beckman (2006):

```
I = I_L - I_0 (exp((V + I R_s) / a) - 1) - (V + I R_s) / R_sh

Tk   = T_cell + 273.15,   Tref = 298.15 K,   S = effective irradiance on the cells
a    = a_ref Tk / Tref                                  (a = n Ns k T / q)
I_L  = S / 1000 (I_L,ref f_IL + alpha_sc (Tk - Tref))    alpha_sc = alpha_sc_rel I_L,ref
I_0  = I_0,ref f_I0 (Tk / Tref)^3 exp(Eg_ref / (k Tref) - Eg / (k Tk))
Eg   = Eg_ref (1 + dEg/dT (Tk - Tref))
R_sh = R_sh,ref 1000 / S,     R_s constant
```

`f_IL`, `f_I0` are the mismatch factors of one module (1 for the single module twin). The
code is the same set of equations as `pvlib.pvsystem.calcparams_desoto`; the tests compare
the two, and the maximum power against `pvlib.pvsystem.singlediode` (agreement better than
1e-4).

Cell temperature from the modelled back-sheet temperature (Sandia offset):

```
T_cell = T + cell_minus_back_c G / 1000
```

Voltage at a given current is found by Newton's method on `x = V + I R_s`.

Placeholder values: the five reference parameters are the unique single-diode curve
through the four listing numbers (I_sc 5.88 A, V_oc 21.5 V, I_mp 5.55 A, V_mp 18.0 V) with
`R_sh,ref` fixed at 500 ohm (`twin.fit.electrical_from_listing`). Model values that follow:
P_mp 99.9 W at reference conditions, ideality factor 0.99 for 36 cells, and a temperature
coefficient of P_mp of **-0.46 %/degC** (`electrical.gamma_pmp`). That gamma is a property
of the De Soto equations with the silicon band gap, **not our module's gamma**, which is
unknown until the I-V sweeps are analysed.

## 2. String (`electrical.mpp_strings`)

Modules in series carry one current; their voltages add. A module that cannot carry the
string current is clamped by its bypass diode:

```
V_string(I) = sum over modules of max(V_m(I), -V_bypass)
P_string    = max over I of I V_string(I)          (one ideal MPPT per string)
```

The maximum is searched on a grid of 32 currents between half the weakest and the
strongest photocurrent, refined with 13 points around the best one; against a
40 000-point scan the power differs by less than 1e-4 (tests). The plant power is the sum
of the six strings. No inverter, no cables.

## 3. Module, thermal (`twin/thermal.py`)

One lumped heat capacity with the Faiman loss term (Faiman 2008), the model already used
by `sim/thermal.py` for the synthetic data and the CUSUM detector:

```
C dT/dt = A G - A U(v) (T - Ta) - K_w (T - T_w) - q_evap          U(v) = u0 + u1 v
```

`K_w` and `q_evap` are zero for a module that is not sprayed. As in pvlib's Faiman model,
`G` is the full in-plane irradiance: the reflected and the electrically extracted shares
are folded into `u0`, `u1`. Inputs are held over one step, so the step is exact:

```
T_ss = (A G + A U Ta + K_w T_w - q_evap) / (A U + K_w)
tau  = C / (A U + K_w)
T(t + dt) = T_ss + (T(t) - T_ss) exp(-dt / tau)
T_mean    = T_ss + (T(t) - T_ss) (1 - exp(-dt / tau)) tau / dt      (mean over the step)
```

Model value: without spray `tau = C / (A U)` = 6.3 min at 1.5 m/s (that is how `C` was
set, from Herteleer et al. 2023).

## 4. Spray cooling (`twin/spray.py`)

For one sprayed module receiving `q` L/min (`m = rho_w q / 60` kg/s):

```
f_wet = min(1, q / q_full)                 wetted share of the glass
NTU   = U_mw A f_wet / (m c_w)
eps   = 1 - exp(-NTU)                      share of (T - T_w) by which the water warms
K_w   = eps m c_w                          heat to the water = K_w (T - T_w)
```

The water film enters at the tank temperature and warms towards the module temperature,
like the cold stream of a heat exchanger whose other side is at one temperature. Two
limits are built in: the water cannot take more than it can carry (`K_w <= m c_w`) nor
more than the surface passes (`K_w <= U_mw A`). More flow always cools more, but less per
litre: that is what makes a low pump duty attractive.

`U_mw` = 62 W/(m2 K) is **not a literature value**. It is the value at which the model
reproduces the team's working estimate (CLAUDE.md, [EST]): 2 L/min on one module warms by
5 degC, i.e. takes 0.70 kW, when the module is 20 degC above the water.

Model values at the three placeholder duties, module 50 degC, water 30 degC, air 30 degC
at 40 % humidity, wind 1.5 m/s:

| Duty | Flow, L/min | Pump, W | eps | K_w, W/K | Heat to water, W | Evaporation, W |
|---|---|---|---|---|---|---|
| 0.50 | 0.875 | 3.6 | 0.44 | 26.7 | 534 | 402 |
| 0.75 | 1.31 | 5.4 | 0.36 | 32.5 | 650 | 426 |
| 1.00 | 1.75 | 7.2 | 0.28 | 34.2 | 684 | 398 |

## 5. Evaporation (optional, `evap_enabled`)

From the wetted glass, with the Chilton-Colburn analogy between heat and mass transfer:

```
T_film = T - (T - T_w) eps / NTU                         mean film temperature
h_c    = hc0 + hc1 v                                     McAdams: 5.7 + 3.8 v, W/(m2 K) (*)
h_m    = h_c / (rho_air c_air Le^(2/3))                  m/s
m_evap = f_wet A h_m max(0, rho_sat(T_film) - RH rho_sat(Ta))      kg/s
q_evap = m_evap h_fg
rho_sat(t) = 610.94 exp(17.625 t / (t + 243.04)) / (R_v (t + 273.15))     Magnus formula
```

(*) The McAdams coefficient includes radiation and free convection, not forced convection
alone; Watmuff et al. (1977) revised it to 2.8 + 3.0 v without them (Palyvos 2008). Used
here as a convection coefficient it overstates the evaporation by about 1.5 times at
1.5 m/s: an upper bound. See [refs.md](refs.md); `python -m twin.rank` runs both forms.

All the latent heat is taken from the module. Evaporation acts only while the zone is
sprayed. In the table above it is of the same size as the heat carried by the water: the
simulated benefit of spraying depends on this term as much as on any other, and it rests
on a textbook correlation for a dry plate in wind, not on a measurement of a wet module.

## 6. Pump, valves and water (`twin/spray.py`, `twin/rooftop.py`)

Pump unit = one R385 feeding one module's nozzles: a table `duty -> (flow, power)`,
interpolated linearly; below `pump_min_duty` the pump does not run. The rooftop scales it:

```
flow per module in an open zone = flow(duty)             the same in every open zone
P_pump   = N_sprayed_modules P_unit(duty) plant_pump_power_factor
P_valves = N_open_valves valve_hold_w
```

A valve stays at least `valve_min_on_s` open and `valve_min_off_s` closed. If more zones
are asked for than `max_zones_open`, the zones with the hottest sensors are served.

Tank (fully mixed, mass `M` = `tank_l_per_module` x modules x `rho_w`):

```
M c_w dT_w/dt = (1 - f_loss) sum of K_w (T_mean - T_w)  -  UA_tank (T_w - Ta)
water used        = evaporated + f_loss x sprayed       (replaced by make-up water at Ta)
water circulated  = everything the pump moved
```

In this closed loop the heat the water takes from the modules comes back to the tank. Over
a day the only ways heat leaves the system are the air, evaporation and the tank's own
loss: the tank warms and the spray cools less. That is why the tank temperature is an
input of the controllers.

## 7. Rooftop spread (`twin/rooftop.py`)

Each module draws once, from the distributions in `params.py`:

```
f_IL, f_I0           mismatch factors (section 1)
S = G x soiling      soiling = string factor x module factor (electrical only)
v = wind x exposure  exposure = string factor (1.0 at string 1 ... 0.6 at string 6) x module factor
```

Five modules of each string carry a back-sheet sensor: reading = true temperature + a
fixed offset + noise, rounded to the DS18B20 step. Controllers see only these readings.

## 8. Controllers (`twin/controllers.py`)

**Fixed threshold**, per zone: on when the zone's sensor mean reaches 45 degC, off at
35 degC (Moharram et al. 2013).

**Predictive.** Taking heat `E` out of a module lowers its temperature by `E / C`; the
difference to the uncooled module decays with `tau = C / (A U)`, so its time integral is
`E / (A U)`, whatever the heat capacity and however the heat was taken. With `k` the power
gained per degC:

```
k(G)  = -gamma P_mp,ref G / 1000                                  W/degC per module
gain  = k(G) q_cool(T_zone, T_w, duty, RH, v) / (A U(v))          W per module
        q_cool = K_w (T_zone - T_w) + q_evap
cost  = P_unit(duty) plant_pump_power_factor + valve_hold_w / modules per string
```

A zone is opened when `gain / cost >= predictive_on_ratio` (1.2) and closed when it falls
below `predictive_off_ratio` (1.0); the pump duty is the one of `predictive_duties` with
the largest summed `gain - cost`. It is the pump break-even of CLAUDE.md turned into a
rule. It predicts by persistence: irradiance and wind are assumed to stay as they are for
the next minutes. `gamma`, `P_mp,ref` and the spray model are the controller's own copy of
the parameters.

**Spike-triggered.** One detector of `detect/` per zone (NASIM-GBT as trained by the paper
pipeline; the threshold rule and the CUSUM are available). On an alarm:

```
rise  = max(rise of the zone over the last 10 min, 3 degC)        3 degC = the spike definition
burst = C rise / q_cool        limited to spike_min_burst_s ... spike_max_burst_s
gain  = k(G) rise tau,   cost = (pump + valve share) burst        fire the burst if gain > cost
```

This is the decision rule of the team simulation (CLAUDE.md) with the controller's model
in place of the fixed numbers. After a burst the module re-heats by about what was cooled:
the zone's detector is not fed during the burst and for `spike_blank_s` (15 min), then
restarted with its own 10-min warm-up.

## 9. Fit (`twin/fit.py`)

- Electrical: least squares (soft-L1 loss) of modelled minus measured current over all
  points of the usable I-V sweeps, each at its irradiance and cell temperature; free:
  `I_L,ref`, `I_0,ref`, `R_s`, `R_sh,ref`, `a_ref`, `alpha_sc_rel`, and `Eg_ref` when the
  sweeps span at least 10 degC.
- Gamma: `Eg_ref` such that the model's gamma equals a measured gamma (one-dimensional
  root search).
- Thermal: least squares of modelled minus measured module temperature over logged days;
  each detector segment starts at its measured temperature; free: `C`, `u0`, and `u1`
  only when the files hold wind speed.

## 10. Validation (`twin/validate.py`)

Driven by measured irradiance, ambient and wind only, the twin predicts module temperature
and P_mp. Reported per day and overall: bias, mean absolute error, RMSE and the 95 %
quantile of the absolute error, for temperature and for power (with the twin's own
temperature and with the measured temperature), plus P_mp against the maximum of every
usable I-V sweep.

## References

- De Soto, W., Klein, S. A., Beckman, W. A., Solar Energy 80(1) (2006) 78-88,
  doi:10.1016/j.solener.2005.06.010; erratum (sign of Eq. 6a) Solar Energy 81(1) (2007) 150.
- Faiman, D., Prog. Photovolt. 16 (2008) 307-315.
- King, D. L., Boyson, W. E., Kratochvil, J. A., Sandia report SAND2004-3535 (2004).
- Herteleer, B. et al., Solar Energy 263 (2023) 111889.
- Moharram, K. A. et al., Ain Shams Engineering Journal 4 (2013) 869-877.
- McAdams, W. H., Heat Transmission, 3rd ed., McGraw-Hill (1954), as quoted in Duffie, J. A.,
  Beckman, W. A., Solar Engineering of Thermal Processes, 4th ed., Wiley (2013) (wind
  coefficient, includes radiation and free convection); revision without them: Watmuff
  et al. (1977), as tabulated by Palyvos, Appl. Therm. Eng. 28 (2008) 801-808.
- Bergman, T. L., Lavine, A. S., Incropera, F. P., DeWitt, D. P., Fundamentals of Heat and
  Mass Transfer, 7th ed., Wiley (2011), section 6.7 (heat and mass transfer analogy,
  evaporative cooling) and the effectiveness-NTU relations.
- Alduchov, O. A., Eskridge, R. E., J. Appl. Meteorol. 35(4) (1996) 601-609, Eq. (21).

Faiman, King, Herteleer and Moharram are in `paper/refs.bib`; the others are in
[refs.bib](refs.bib), checked on 2026-11-14 ([refs.md](refs.md) says what each check
covered and what must still be opened before an equation or page number is quoted).
