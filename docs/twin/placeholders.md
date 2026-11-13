# What must be measured to replace each placeholder

> SIMULATION. Today **all** physical parameters of the twin are placeholders
> (`[LIT-PLACEHOLDER]` in [parameters.md](parameters.md)). This page says, measurement by
> measurement, what replaces them and how the measured value enters the twin.

## How a measured value gets in (no code is edited)

`twin/params.py` holds the placeholders and is not edited for measurements. Its `load()`
overlays two files when they exist:

| File | Written by | Holds |
|---|---|---|
| `twin/params_measured.json` | `python -m twin.fit ... --real` | fitted or hand-entered values, each tagged `[MEASURED]` with its source |
| `twin/data/pump_curve_R385_measured.csv` | filled in by hand from the bench | the pump table |

Three ways to write the first file:

```
python -m twin.fit --data <copy of the SD card> --real          fit from rig files
python -m twin.fit --gamma -0.0041 --real                       a gamma from analysis/gamma.py
python -m twin.fit --real --set spray_u_module_water=48 --source "spray test 2027-01-12"
```

Without `--real` the result goes to `out/twin/fit_synthetic.json`, tagged
`[FIT-SYNTHETIC]`, and is never picked up automatically: a fit on synthetic files cannot
end up in the measured slot. After every replacement: run `python -m twin.validate` on
days not used for the fit, and `python -m twin.compare` again.

## 1. Already planned in the paper campaign (no extra hardware)

| Measurement | Replaces | How |
|---|---|---|
| **I-V sweeps** of the module at several irradiances and temperatures (sweep files `SW*`/`SS*`, docs/paper/07) | `i_l_ref`, `i_o_ref`, `r_s`, `r_sh_ref`, `a_ref`, `alpha_sc_rel`; `eg_ref` when the sweeps span >= 10 degC of cell temperature | `twin.fit --data ... --real`. Sweeps taken right after uncovering the module have cells warmer than the back sheet; the fit uses a robust loss, but steady sweeps are better. R_sh needs points near short circuit (branch B of the load). |
| **gamma** from the gamma runs (analysis/gamma.py, docs/paper/02) | `eg_ref` (it sets the model's temperature coefficient of P_mp) | fitted with the sweeps, or `twin.fit --gamma <value> --real`. The report of the fit prints the model's gamma: it must agree with the measured one. |
| **Logged days** of module temperature, irradiance and ambient (the 10 s day files) | `heat_capacity_j_per_k` (the time constant), `u0` | `twin.fit --data ... --real`: thermal fit, up to 14 days spread over the campaign. |
| The same days **with an anemometer** | `u1`, and a true `u0` | same command; without wind in the files `u1` keeps its placeholder and `u0` becomes an effective value at 1.5 m/s. Anemometer: pending the supervisor's approval. |
| Module label, tape measure, junction box | `cells_in_series`, `area_m2`, `bypass_drop_v` and the number of diodes | `twin.fit --real --set cells_in_series=... --source "module label"`. Two bypass diodes need a code change in `electrical.py` (assumption A3). |
| Cleaning log: power before and after each morning cleaning (analysis/soiling.py) | the level of `soiling_module` for our site | enter the distribution by hand in `params_measured.json` (a dict; `--set` takes scalars only) |

`cell_minus_back_c` cannot be measured on our rig (no cell sensor); it stays a literature
value.

## 2. Pump bench test (Abdullah, R385)

| Measurement | Replaces | How |
|---|---|---|
| **Bucket test with the real nozzles**: at each PWM duty from the lowest that still sprays up to 100 %, the volume collected and the time, the voltage and the current at the pump | `pump_curve`, `pump_min_duty` | copy `twin/data/pump_curve_R385_TEMPLATE.csv` to `pump_curve_R385_measured.csv` and enter one line per run: `duty_pct,volume_l,time_s,voltage_v,current_a`. Runs at the same duty are averaged; a duty that gave no water is entered with volume 0. |

Needed for a useful curve: at least four duties, two or three runs each, the same nozzles,
pipe lengths and lift as on the stand (the flow depends on them), water at a known level
in the tank. Record the supply voltage: the twin takes power as voltage x current at the
pump.

## 3. Spray tests on the 100 W module (Phase 3P, after the pump works)

| Measurement | Replaces | How |
|---|---|---|
| **Cooling per second of spraying**: module temperature before, during and after bursts of known flow; water temperature into the nozzles and in the gutter | `spray_u_module_water` | from the water: heat = flow x c_w x (T_out - T_in); from the module: C x dT/dt at the start of a burst. Enter with `--set`. |
| The same at several duties, with photos or water-sensitive paper | `spray_full_wetting_lpm` | the flow at which the whole glass is wet |
| **Temperature decay after a burst** and the tank weight before and after a day | `evap_enabled`, `evap_hc0`, `evap_hc1` | calibrate the evaporation coefficient so that the model reproduces the decay and the water loss; humidity from the BME280. If the simple term cannot reproduce it, the model needs changing, not only the numbers. |
| Tank weight before and after a known sprayed volume, evaporation separated | `spray_loss_fraction` | `--set` |
| Cooling curve of the warm tank over a night | `tank_ua_w_per_k_per_module` | `--set` |
| Coil current of the valve that is bought | `valve_hold_w` | `--set` |

Each spray test must log `T_water` (spare DS18B20 in the tank) and separate washing from
cooling: clean the module before the test (CLAUDE.md).

## 4. Cannot be measured on a single-module rig

These stay assumptions until there is a real multi-module installation. They are the
reason the rooftop results are a development tool and not a forecast.

| Placeholder | Why |
|---|---|
| `plant_pump_power_factor` | one large pump with long pipes is not 120 small pumps; needs a hydraulic design |
| `mismatch_i_l`, `mismatch_i_o` | need several modules of the same type under the same sun |
| `soiling_string`, `wind_exposure_first`, `wind_exposure_last`, `wind_exposure_module` | need measurements across a real roof |
| `wind_fallback_ms` | only matters while there is no anemometer |

## Order that gives the most for the least

1. Sweeps, gamma and logged days come with the paper campaign: run the fit and the
   validation as soon as two weeks of files exist.
2. The pump curve is a short bench test, and pump energy is subtracted one for one from
   the net energy (compare the two duties in sensitivity 3 of
   [simulation-results.md](simulation-results.md)).
3. The evaporation calibration decides whether spraying pays at all in the model
   (sensitivity 1).
