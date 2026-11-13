"""Every parameter of the digital twin, in ONE file. ALL VALUES ARE PLACEHOLDERS.

Nothing below is a measured property of our BANINUAN 100 W module, of our R385 pump or
of any rooftop. Each entry carries its value, unit, tag, source and what has to be
measured to replace it.

Tags
  [LIT-PLACEHOLDER]  a physical model parameter taken from literature, a seller listing
                     or (where the source says so) a plain assumption. To be replaced by
                     a measurement on our hardware.
  [SCENARIO]         a choice that defines the simulated rooftop or a controller setting
                     (number of strings, set points). Not a property to be measured.
  [CONST]            a physical constant of water or air (textbook value).
  [MEASURED]         written by twin/fit.py from REAL rig files (never present in this file)
  [FIT-SYNTHETIC]    written by twin/fit.py from synthetic files (pipeline test only)

How a placeholder is replaced WITHOUT a code change
  twin/fit.py writes twin/params_measured.json; load() below overlays that file on the
  values here whenever it exists. The pump curve has its own slot: the bench table
  twin/data/pump_curve_R385_measured.csv (template next to it), overlaid the same way.

    python -m twin.params          writes docs/twin/parameters.md from this file
"""
import copy
import json
import pathlib
from dataclasses import dataclass

LIT, SCN, CONST = "[LIT-PLACEHOLDER]", "[SCENARIO]", "[CONST]"
MEASURED, FIT_SYNTH = "[MEASURED]", "[FIT-SYNTHETIC]"

HERE = pathlib.Path(__file__).resolve().parent
MEASURED_PATH = HERE / "params_measured.json"
PUMP_CURVE_PATH = HERE / "data" / "pump_curve_R385_measured.csv"
DOC_PATH = HERE.parent / "docs" / "twin" / "parameters.md"


@dataclass(frozen=True)
class Param:
    value: object
    unit: str
    tag: str
    source: str
    measure: str = ""       # what must be measured to replace the placeholder
    group: str = ""


# Sources quoted more than once
DESOTO = ("De Soto, Klein & Beckman, Solar Energy 80 (2006) 78-88, five-parameter "
          "single-diode model")
LISTING = ("seller listing of the BANINUAN 100 W module (Pmax 100 W, Vmp 18 V, Imp 5.55 A, "
           "Voc 21.5 V, Isc 5.88 A, 1200 x 540 mm); not a datasheet")
LISTING_FIT = ("fitted to the " + LISTING + " by twin.fit.electrical_from_listing(), "
               "with r_sh_ref fixed; model: " + DESOTO)
FAIMAN = ("Faiman, Prog. Photovolt. 16 (2008) 307-315; value = pvlib's Faiman default, "
          "same as config.py")
R385 = ("seller listings of the R385 diaphragm pump: 6-12 V DC, 0.5-0.7 A under load, "
        "1.5-2 L/min free flow, head 3 m (listings disagree: 3-5 m)")
NO_SOURCE = "assumption, no literature source"

_G = {}


def _add(group, **entries):
    for name, (value, unit, tag, source, measure) in entries.items():
        assert name not in _G, name
        _G[name] = Param(value, unit, tag, source, measure, group)


# --- module, electrical: single-diode model at reference conditions (1000 W/m2, 25 degC) ---
_add("module, electrical",
     cells_in_series=(36, "-", LIT,
                      "assumption for a 12 V-class module (Voc 21.5 V ~ 36 x 0.6 V); the "
                      "listing does not give the cell count",
                      "count the cells in one series path / read the module label"),
     i_l_ref=(5.881593, "A", LIT, LISTING_FIT,
              "I-V sweeps of our module (twin.fit, sweep files SW*/SS*)"),
     i_o_ref=(4.108027e-10, "A", LIT, LISTING_FIT, "I-V sweeps of our module"),
     r_s=(0.1354286, "ohm", LIT, LISTING_FIT,
          "I-V sweeps of our module (include the cable resistance to the INA226)"),
     r_sh_ref=(500.0, "ohm", LIT,
               "assumption: the listing gives 4 numbers and the model has 5 parameters; "
               "500 ohm is the value that gives a diode ideality factor of ~1.0 with 36 "
               "cells. " + DESOTO,
               "I-V sweeps reaching near short circuit (slope of the flat part)"),
     a_ref=(0.9196918, "V", LIT, LISTING_FIT + ". a = n * Ns * k * T / q",
            "I-V sweeps of our module"),
     alpha_sc_rel=(0.0005, "1/degC", LIT,
                   "typical order of magnitude for crystalline silicon (+0.05 %/degC); the "
                   "listing gives no temperature coefficient. Same assumed value as "
                   "analysis/fake_logger.py",
                   "I_sc against cell temperature in the gamma runs (analysis/gamma.py: alpha)"),
     eg_ref=(1.121, "eV", LIT, "band gap of silicon in " + DESOTO,
             "set by twin.fit so that the model's gamma equals the gamma measured from "
             "I-V sweeps (docs/paper/02)"),
     d_eg_dt=(-0.0002677, "1/K", LIT, "silicon, " + DESOTO, "not measured; kept"),
     bypass_drop_v=(0.5, "V", LIT,
                    "typical forward voltage of a Schottky bypass diode; ONE diode per "
                    "module is assumed (" + NO_SOURCE + " for our module)",
                    "open the junction box: number and type of bypass diodes"),
     )

# --- module, thermal: C dT/dt = A (G - (u0 + u1 v)(T - Ta)) - q_spray -------------------------
_add("module, thermal",
     area_m2=(0.648, "m2", LIT, LISTING + ": 1.200 x 0.540 m. Same as config.py",
              "measure the glass with a tape"),
     heat_capacity_j_per_k=(8700.0, "J/K", LIT,
                            "C = tau * U * A with tau = 6.3 min (Herteleer et al., Solar "
                            "Energy 2023, fixed-mount systems), U = 25 + 6.84 * 1.5 "
                            "W/(m2 K). Same as config.py",
                            "time constant from logged days (twin.fit thermal fit) or from "
                            "the step after uncovering the module in a gamma run"),
     u0=(25.0, "W/(m2 K)", LIT, FAIMAN,
         "thermal fit on logged days (module temperature, irradiance, ambient)"),
     u1=(6.84, "W s/(m3 K)", LIT, FAIMAN,
         "thermal fit on logged days WITH an anemometer; without one it cannot be "
         "identified and only an effective u0 is fitted"),
     cell_minus_back_c=(3.0, "degC at 1000 W/m2", LIT,
                        "King, Boyson & Kratochvil, Sandia report SAND2004-3535 (2004): "
                        "cell about 3 degC above the back sheet at 1000 W/m2 for "
                        "glass/cell/polymer modules. Same as config.py",
                        "not measurable on our rig (no cell sensor); kept"),
     wind_fallback_ms=(1.5, "m/s", LIT,
                       "wind speed used when the data has no anemometer value; same as "
                       "config.CUSUM_WIND_CONST (" + NO_SOURCE + ")",
                       "an anemometer beside the module; else folded into the fitted u0"),
     )

# --- spray cooling ----------------------------------------------------------------------------
_add("spray cooling",
     spray_u_module_water=(62.0, "W/(m2 K)", LIT,
                           "NOT from literature. Chosen so that the model reproduces the "
                           "team's working estimate [EST] (CLAUDE.md): 2 L/min on one module "
                           "warms by 5 degC (0.70 kW) when the module is 20 degC above the "
                           "water. Overall conductance module -> water film, per m2 of "
                           "module, at full wetting",
                           "cooling per second of spraying on the 100 W module: module "
                           "temperature drop and water in/out temperature at known flow"),
     spray_full_wetting_lpm=(1.0, "L/min per module", LIT,
                             NO_SOURCE + ": flow per module at which the whole glass is "
                             "wetted; below it the wetted fraction falls linearly",
                             "photos / water-sensitive paper at 30-100 % duty with the real "
                             "nozzles"),
     evap_enabled=(1, "0/1", LIT,
                   "evaporation from the wetted glass is included (1). The project notes "
                   "that the energy case rests on cooling WITH evaporation; set 0 to "
                   "reproduce the no-evaporation estimates of CLAUDE.md",
                   "post-spray temperature decay and tank weight loss"),
     evap_hc0=(5.7, "W/(m2 K)", LIT,
               "wind convection on a flat plate, h = 5.7 + 3.8 v (McAdams, Heat "
               "Transmission, 1954; quoted in Duffie & Beckman, Solar Engineering of "
               "Thermal Processes). Turned into a mass-transfer coefficient with the "
               "Chilton-Colburn analogy (Incropera et al., Fundamentals of Heat and Mass "
               "Transfer)",
               "calibrate the evaporation term from the decay after a spray burst and from "
               "the measured water loss"),
     evap_hc1=(3.8, "W s/(m3 K)", LIT, "see evap_hc0", "see evap_hc0"),
     spray_loss_fraction=(0.05, "-", LIT,
                          NO_SOURCE + ": share of the sprayed water that does not return "
                          "to the tank (drift, splash)",
                          "weigh the tank before and after a known sprayed volume"),
     tank_ua_w_per_k_per_module=(0.5, "W/K per module", LIT,
                                 NO_SOURCE + ": heat loss of the shaded tank to the air, "
                                 "scaled with the number of modules",
                                 "cooling curve of the warm tank over a night"),
     )

# --- pump and valves --------------------------------------------------------------------------
_add("pump and valves",
     pump_curve=(((0.50, 0.875, 3.6), (0.75, 1.3125, 5.4), (1.00, 1.75, 7.2)),
                 "(duty, L/min, W) per pump unit", LIT,
                 "PLACEHOLDER built from " + R385 + ". At duty 1: 1.75 L/min (middle of "
                 "the listed range) and 12 V x 0.6 A = 7.2 W; flow and power are ASSUMED "
                 "proportional to the duty. The flow through OUR nozzles is unknown and "
                 "will be lower than free flow",
                 "Abdullah's bucket test on the R385 with the real nozzles: at each duty "
                 "the volume and time, the voltage and the current -> "
                 "twin/data/pump_curve_R385_measured.csv"),
     pump_min_duty=(0.50, "-", LIT,
                    "lowest listed supply voltage 6 V of 12 V (" + R385 + ")",
                    "minimum duty at which the pump still starts and sprays (bucket test)"),
     plant_pump_power_factor=(1.0, "-", LIT,
                              NO_SOURCE + ": electrical pump power per sprayed module on "
                              "the rooftop divided by the R385's power at the same flow "
                              "per module. 1.0 = the rig multiplied by the number of "
                              "modules. Longer pipes raise it, a larger and more efficient "
                              "pump lowers it",
                              "cannot be measured on the single-module rig; needs the "
                              "hydraulic design of a real installation"),
     valve_hold_w=(5.0, "W per open valve", LIT,
                   "typical coil power of a small 12 V normally-closed solenoid valve ("
                   + NO_SOURCE + " for a specific part)",
                   "coil current of the valve that is bought"),
     )

# --- the simulated rooftop --------------------------------------------------------------------
_add("rooftop",
     n_strings=(6, "-", SCN, "task definition (Asim, 2026-11-13)", ""),
     modules_per_string=(20, "-", SCN,
                         "task definition. In a real design the string length follows "
                         "from the inverter's voltage limits and the cold-day Voc, not "
                         "from a fixed number (PROJECT_STATE.md sec. 8)", ""),
     max_zones_open=(6, "-", SCN,
                     "the shared pump is sized for all six zones at once; a smaller value "
                     "simulates an undersized pump (hottest strings are served first)", ""),
     tank_l_per_module=(20.0, "L per module", SCN,
                        "CLAUDE.md: 20-30 L tank for one module, scaled linearly", ""),
     sensors_per_string=(5, "-", SCN,
                         "five back-sheet sensors per string, on five different modules, "
                         "so that each zone gives the detector the same T1..T5 input as "
                         "our rig", ""),
     mismatch_i_l=({"dist": "normal", "mean": 1.0, "sd": 0.01, "lo": 0.97, "hi": 1.03},
                   "factor on i_l_ref", LIT,
                   NO_SOURCE + ": module-to-module spread of the photocurrent",
                   "I_sc of several modules of the same type under the same sun"),
     mismatch_i_o=({"dist": "lognormal", "median": 1.0, "sigma": 0.05, "lo": 0.85, "hi": 1.18},
                   "factor on i_o_ref", LIT,
                   NO_SOURCE + ": spread of the saturation current (a V_oc spread of "
                   "about 0.2 %)", "V_oc of several modules"),
     soiling_module=({"dist": "normal", "mean": 0.97, "sd": 0.015, "lo": 0.90, "hi": 1.0},
                     "transmission factor", LIT,
                     NO_SOURCE + ": light lost to dust on each module",
                     "our cleaning log: power before and after each cleaning "
                     "(analysis/soiling.py)"),
     soiling_string=({"dist": "uniform", "lo": 0.98, "hi": 1.0}, "transmission factor", LIT,
                     NO_SOURCE + ": dust level common to a whole string",
                     "cannot be measured on one module"),
     wind_exposure_first=(1.0, "factor on wind speed", LIT,
                          NO_SOURCE + ": string 1 at the windward edge sees the full "
                          "wind", "wind speed at several places on a real roof"),
     wind_exposure_last=(0.6, "factor on wind speed", LIT,
                         NO_SOURCE + ": string 6, sheltered by the others; the strings in "
                         "between are interpolated linearly",
                         "wind speed at several places on a real roof"),
     wind_exposure_module=({"dist": "lognormal", "median": 1.0, "sigma": 0.10, "lo": 0.7,
                            "hi": 1.4},
                           "factor on wind speed", LIT,
                           NO_SOURCE + ": module-to-module spread inside a string",
                           "wind speed at several places on a real roof"),
     sensor_noise_c=(0.05, "degC", SCN, "DS18B20 noise, same as config.DS18B20_NOISE_C", ""),
     sensor_offset_sd_c=(0.2, "degC", SCN,
                         "fixed offset per sensor, same as config.DS18B20_OFFSET_C", ""),
     sensor_lsb_c=(0.0625, "degC", SCN, "DS18B20 12-bit step, same as config.py", ""),
     )

# --- controller settings ----------------------------------------------------------------------
_add("controllers",
     cooling_min_g=(50.0, "W/m2", SCN,
                    "no strategy sprays below this irradiance (night, dawn)", ""),
     baseline_duty=(1.0, "-", SCN,
                    "pump duty of the continuous and the fixed-threshold strategy "
                    "(an on/off pump at full speed)", ""),
     fixed_on_c=(45.0, "degC", SCN,
                 "fixed threshold: spray on at 45 degC (Moharram et al., Ain Shams Eng. J. "
                 "4 (2013) 869-877; CLAUDE.md)", ""),
     fixed_off_c=(35.0, "degC", SCN, "fixed threshold: spray off at 35 degC (same source)", ""),
     valve_min_on_s=(30.0, "s", SCN, "a valve stays open at least this long", ""),
     valve_min_off_s=(60.0, "s", SCN, "a valve stays closed at least this long", ""),
     spike_min_burst_s=(20.0, "s", SCN, "shortest burst after a detector alarm", ""),
     spike_max_burst_s=(180.0, "s", SCN, "longest burst after a detector alarm", ""),
     spike_duty=(1.0, "-", SCN, "pump duty of a spike-triggered burst", ""),
     predictive_duties=((0.5, 0.75, 1.0), "-", SCN,
                        "pump duties the predictive controller chooses from", ""),
     predictive_on_ratio=(1.0, "-", SCN,
                          "predictive: open a zone when expected gain / pump power "
                          "exceeds this", ""),
     predictive_off_ratio=(0.8, "-", SCN,
                           "predictive: close it when the ratio falls below this "
                           "(hysteresis against chatter)", ""),
     )

# --- constants --------------------------------------------------------------------------------
_add("constants",
     dt_s=(10.0, "s", SCN, "one sample every 10 s, as the logger (config.DT_S)", ""),
     water_cp=(4180.0, "J/(kg K)", CONST, "liquid water near 30 degC (textbook)", ""),
     water_rho_kg_per_l=(0.996, "kg/L", CONST, "liquid water near 30 degC (textbook)", ""),
     water_h_fg=(2.43e6, "J/kg", CONST, "latent heat of evaporation near 30 degC", ""),
     air_rho=(1.15, "kg/m3", CONST, "air near 35 degC, 1 atm", ""),
     air_cp=(1007.0, "J/(kg K)", CONST, "air", ""),
     lewis_number=(0.87, "-", CONST, "water vapour in air (Incropera et al.)", ""),
     )

PARAMS = dict(_G)


class Params:
    """Parameter values by attribute (p.u0) plus their Param records (p.meta["u0"])."""

    def __init__(self, meta):
        object.__setattr__(self, "meta", dict(meta))

    def __getattr__(self, name):
        try:
            return self.meta[name].value
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        raise AttributeError("Params is read-only; use replace()")

    def replace(self, tag="[OVERRIDE]", source="set by the caller", **values):
        """A copy with some values changed (scenarios, sensitivity runs, tests)."""
        meta = dict(self.meta)
        for name, value in values.items():
            old = meta[name]            # KeyError: unknown parameter name
            meta[name] = Param(value, old.unit, tag, source, old.measure, old.group)
        return Params(meta)

    def placeholders(self):
        """Names of the parameters that still carry [LIT-PLACEHOLDER]."""
        return [n for n, m in self.meta.items() if m.tag == LIT]

    def tags(self):
        out = {}
        for m in self.meta.values():
            out[m.tag] = out.get(m.tag, 0) + 1
        return out


def read_measured(path):
    """The overlay file written by twin/fit.py: {"params": {name: {"value", "tag",
    "source"}}}. Returns {name: Param-fields dict}; a missing file gives {}."""
    path = pathlib.Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("params", {})


def load(measured=MEASURED_PATH, pump_curve=PUMP_CURVE_PATH):
    """The parameters the twin runs with: the placeholders of this file, overlaid by
    `measured` (a params_measured.json) and by the measured pump curve when those files
    exist. Pass measured=None / pump_curve=None for the bare placeholders."""
    meta = copy.deepcopy(PARAMS)
    if measured is not None:
        for name, rec in read_measured(measured).items():
            if name not in meta:
                raise KeyError(f"{measured}: unknown parameter '{name}'")
            old = meta[name]
            value = rec["value"]
            if isinstance(old.value, tuple) and isinstance(value, list):
                value = tuple(tuple(v) if isinstance(v, list) else v for v in value)
            meta[name] = Param(value, old.unit, rec.get("tag", MEASURED),
                               rec.get("source", str(measured)), old.measure, old.group)
    if pump_curve is not None and pathlib.Path(pump_curve).exists():
        from twin import spray              # imported here: spray.py imports this module
        table, min_duty, note = spray.read_pump_curve_csv(pump_curve)
        old = meta["pump_curve"]
        meta["pump_curve"] = Param(table, old.unit, MEASURED, note, old.measure, old.group)
        old = meta["pump_min_duty"]
        meta["pump_min_duty"] = Param(min_duty, old.unit, MEASURED, note, old.measure,
                                      old.group)
    return Params(meta)


# --- generated documentation ------------------------------------------------------------------

def _show(value):
    if isinstance(value, dict):
        return ", ".join(f"{k} {v}" for k, v in value.items())
    if isinstance(value, tuple):
        return "; ".join(_show(v) for v in value) if isinstance(value[0], tuple) \
            else " / ".join(f"{v:g}" for v in value)
    return f"{value:g}" if isinstance(value, float) else str(value)


def parameters_markdown():
    lines = [
        "# Twin parameters",
        "",
        "GENERATED by `python -m twin.params` from `twin/params.py`, the one parameter file",
        "of the twin. Do not edit by hand: `tests/test_twin_params.py` fails if the two differ.",
        "",
        "**Every value is a placeholder.** None is a measured property of our module, our",
        "pump or a real rooftop. The last column says what has to be measured to replace it;",
        "`twin/fit.py` then writes the measured value to `twin/params_measured.json`, which",
        "overrides this table without a code change (see `placeholders.md`).",
        "",
        "| Tag | Meaning |",
        "|---|---|",
        "| `[LIT-PLACEHOLDER]` | physical parameter from literature, a seller listing or a "
        "stated assumption; to be replaced by a measurement |",
        "| `[SCENARIO]` | defines the simulated rooftop or a controller setting; a choice, "
        "not a measurement |",
        "| `[CONST]` | physical constant of water or air |",
    ]
    group = None
    for name, m in PARAMS.items():
        if m.group != group:
            group = m.group
            lines += ["", f"## {group[0].upper()}{group[1:]}", "",
                      "| Parameter | Value | Unit | Tag | Source | To replace it, measure |",
                      "|---|---|---|---|---|---|"]
        lines.append(f"| `{name}` | {_show(m.value)} | {m.unit} | `{m.tag}` | {m.source} | "
                     f"{m.measure or '-'} |")
    n_lit = sum(m.tag == LIT for m in PARAMS.values())
    lines += ["", f"{len(PARAMS)} parameters, {n_lit} of them `[LIT-PLACEHOLDER]`.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text(parameters_markdown(), encoding="utf-8")
    print(f"wrote {DOC_PATH} ({len(PARAMS)} parameters)")
