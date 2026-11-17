"""The simulated rooftop: strings of module twins, one spray zone and valve per string, one
shared pump and tank. SIMULATION, placeholder values (twin/params.py).

Every module is the module twin (twin/electrical.py, twin/thermal.py) with its own draw
from the spread distributions of params.py:
  mismatch       factor on the photocurrent and on the saturation current
  soiling        transmission factor (string level x module level); it reduces the light
                 that reaches the cells, the heating is left unchanged
  wind exposure  factor on the wind speed: string 1 at the windward edge, string 6
                 sheltered, plus a module-to-module spread

One step (dt = 10 s), at sample k:
  1. observe   sensors (five back-sheet sensors per string, with offset, noise and the
               DS18B20 step) and the string powers, from the state and weather of sample k
  2. decide    the controller sees ONLY the observation and answers with the valves it
               wants open and one pump duty
  3. actuate   valve dwell times and the pump limit are enforced here, the same for every
               controller; the pump table gives flow per module and electrical power
  4. advance   module temperatures (exact step), tank temperature, water balance

Weather is a day table in the logger's schema (irradiance, T_ambient, humidity, wind):
synthetic days today (data/synth.py), a day measured by our rig later.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from twin import electrical, spray, thermal


# --- the spread distributions -----------------------------------------------------------------

def draw(spec, rng, size):
    """Sample a distribution given as a dict (params.py). Kinds: normal (mean, sd),
    lognormal (median, sigma), uniform (lo, hi), const (value). lo/hi clip the others."""
    kind = spec["dist"]
    if kind == "normal":
        x = rng.normal(spec["mean"], spec["sd"], size)
    elif kind == "lognormal":
        x = spec["median"] * np.exp(rng.normal(0.0, spec["sigma"], size))
    elif kind == "uniform":
        return rng.uniform(spec["lo"], spec["hi"], size)
    elif kind == "const":
        return np.full(size, float(spec["value"]))
    else:
        raise ValueError(f"unknown distribution '{kind}'")
    return np.clip(x, spec.get("lo", -np.inf), spec.get("hi", np.inf))


@dataclass
class Rooftop:
    """One draw of the rooftop. Arrays have shape (strings, modules per string)."""
    p: object
    seed: int
    f_il: np.ndarray
    f_io: np.ndarray
    soiling: np.ndarray
    wind_factor: np.ndarray
    sensor_idx: np.ndarray          # (strings, sensors): module index inside the string
    sensor_offset: np.ndarray       # (strings, sensors), degC

    @property
    def shape(self):
        return self.f_il.shape

    @property
    def n_modules(self):
        return self.f_il.size


def build(p, seed=0):
    """Draw a rooftop. The same (p, seed) gives the same rooftop."""
    rng = np.random.default_rng(seed)
    s, m = int(p.n_strings), int(p.modules_per_string)
    string_wind = np.linspace(p.wind_exposure_first, p.wind_exposure_last, s)[:, None]
    k = int(p.sensors_per_string)
    return Rooftop(
        p=p, seed=seed,
        f_il=draw(p.mismatch_i_l, rng, (s, m)),
        f_io=draw(p.mismatch_i_o, rng, (s, m)),
        soiling=draw(p.soiling_string, rng, (s, 1)) * draw(p.soiling_module, rng, (s, m)),
        wind_factor=string_wind * draw(p.wind_exposure_module, rng, (s, m)),
        sensor_idx=np.stack([np.sort(rng.choice(m, size=k, replace=False)) for _ in range(s)]),
        sensor_offset=rng.normal(0.0, p.sensor_offset_sd_c, (s, k)))


# --- weather ----------------------------------------------------------------------------------

@dataclass
class Weather:
    """One day of weather at the logger's sample period."""
    date: str
    t_local: np.ndarray             # naive local timestamps
    g: np.ndarray                   # in-plane irradiance, W/m2
    ta: np.ndarray                  # ambient temperature, degC
    rh: np.ndarray                  # relative humidity, %
    wind: np.ndarray                # m/s; NaN where not measured
    doy: int
    minute: np.ndarray              # minutes since local midnight

    def __len__(self):
        return len(self.g)


def weather_days(days):
    """Split a schema day table (local time) into one Weather per date."""
    out = []
    for date, d in days.groupby("date", sort=True):
        ts = pd.to_datetime(d["date"] + " " + d["time"])
        rh = d["humidity"].to_numpy(float) if "humidity" in d else np.full(len(d), np.nan)
        wind = d["wind"].to_numpy(float) if "wind" in d else np.full(len(d), np.nan)
        out.append(Weather(
            date=date, t_local=ts.to_numpy(), g=np.clip(d["irradiance"].to_numpy(float), 0, None),
            ta=d["T_ambient"].to_numpy(float), rh=np.where(np.isfinite(rh), rh, 40.0),
            wind=wind, doy=int(ts.iloc[0].day_of_year),
            minute=(ts.dt.hour * 60 + ts.dt.minute + ts.dt.second / 60).to_numpy(float)))
    return out


# --- observation and command ------------------------------------------------------------------

@dataclass
class Obs:
    """What a controller may use at one sample: measurements only, never the true state."""
    k: int
    doy: int
    minute: float
    g: float
    ta: float
    rh: float
    wind: float                     # NaN without an anemometer
    t_sensors: np.ndarray           # (strings, sensors) back-sheet readings, degC
    p_string: np.ndarray            # (strings,) DC power, W
    t_water: float                  # tank temperature, degC
    valves: np.ndarray              # (strings,) valve state now
    duty: float

    @property
    def t_zone(self):
        """Zone temperature as a controller sees it: mean of the string's sensors."""
        return self.t_sensors.mean(axis=1)


@dataclass
class Command:
    valves: np.ndarray              # (strings,) bool: valves the controller wants open
    duty: float = 0.0               # pump duty, 0..1
    alarms: np.ndarray = field(default=None)     # (strings,) bool: detector alarm this sample


# --- the plant --------------------------------------------------------------------------------

class Plant:
    def __init__(self, roof, noise_seed=0):
        self.roof, self.p = roof, roof.p
        self.noise_seed = noise_seed
        self.reset(25.0)

    def reset(self, ta0):
        s = self.roof.shape[0]
        self.t = np.full(self.roof.shape, float(ta0))        # module temperatures
        self.t_tank = float(ta0)                             # [ASSUMPTION] tank starts at ambient
        self.valves = np.zeros(s, dtype=bool)
        self.dwell = np.full(s, 1e9)                         # seconds in the present valve state
        self.duty = 0.0
        self.rng = np.random.default_rng(self.noise_seed)
        self._elec = None

    def wind_at_modules(self, wind):
        v = wind if np.isfinite(wind) else self.p.wind_fallback_ms
        return v * self.roof.wind_factor

    def electrical(self, g):
        """String maximum power points for the present temperatures."""
        p, r = self.p, self.roof
        if g < electrical.G_DARK:
            z = np.zeros(r.shape[0])
            return {"p": z, "v": z, "i": z, "bypassed": z.astype(int)}
        t_cell = self.t + p.cell_minus_back_c * g / 1000.0
        il, io, rs, rsh, a = electrical.desoto(p, g * r.soiling, t_cell, r.f_il, r.f_io)
        return electrical.mpp_strings(il, io, rs, rsh, a, p.bypass_drop_v)

    def observe(self, w, k):
        p, r = self.p, self.roof
        self._elec = self.electrical(w.g[k])
        true = np.take_along_axis(self.t, r.sensor_idx, axis=1)
        reading = true + r.sensor_offset + self.rng.normal(0.0, p.sensor_noise_c, true.shape)
        reading = np.round(reading / p.sensor_lsb_c) * p.sensor_lsb_c
        return Obs(k=k, doy=w.doy, minute=float(w.minute[k]), g=float(w.g[k]), ta=float(w.ta[k]),
                   rh=float(w.rh[k]), wind=float(w.wind[k]), t_sensors=reading,
                   p_string=self._elec["p"], t_water=self.t_tank, valves=self.valves.copy(),
                   duty=self.duty)

    def _valves(self, want, t_zone):
        """Valve states after the dwell times and the pump limit."""
        p = self.p
        may_open = ~self.valves & (self.dwell >= p.valve_min_off_s)
        must_stay = self.valves & (self.dwell < p.valve_min_on_s)
        new = (want & (self.valves | may_open)) | must_stay
        limit = int(p.max_zones_open)
        if new.sum() > limit:                    # undersized pump: hottest strings first
            keep = must_stay.copy()
            for z in np.argsort(-t_zone):
                if keep.sum() >= limit:
                    break
                if new[z]:
                    keep[z] = True
            new = keep
        return new

    def apply(self, w, k, cmd, t_zone):
        """Actuate and advance one step. Returns the record of sample k."""
        p, r, dt = self.p, self.roof, self.p.dt_s
        g, ta, rh = float(w.g[k]), float(w.ta[k]), float(w.rh[k])
        new = self._valves(np.asarray(cmd.valves, dtype=bool), t_zone)
        self.dwell = np.where(new == self.valves, self.dwell + dt, dt)
        opened = int((new & ~self.valves).sum())
        self.valves = new
        flow, p_unit = spray.pump_point(p, float(cmd.duty)) if new.any() else (0.0, 0.0)
        self.duty = float(np.clip(cmd.duty, 0.0, 1.0)) if flow > 0 else 0.0
        m = r.shape[1]
        n_sprayed = int(new.sum()) * m if flow > 0 else 0
        p_pump = n_sprayed * p_unit * p.plant_pump_power_factor
        p_valves = int(new.sum()) * p.valve_hold_w

        flow_mod = np.where(new[:, None], flow, 0.0) * np.ones(r.shape)
        v = self.wind_at_modules(float(w.wind[k]))
        k_w, q_evap, m_evap = spray.cooling(p, self.t, self.t_tank, flow_mod, ta, rh, v)
        t_new, t_mean = thermal.step(p, self.t, g, ta, v, dt, k_w, self.t_tank, q_evap)

        # tank: the returning water brings the heat it took; the lost share does not return
        n = r.n_modules
        mass = p.tank_l_per_module * n * p.water_rho_kg_per_l
        to_water = float((k_w * (t_mean - self.t_tank)).sum()) * dt * (1 - p.spray_loss_fraction)
        to_air = p.tank_ua_w_per_k_per_module * n * (self.t_tank - ta) * dt
        used_kg = float(m_evap.sum()) * dt + p.spray_loss_fraction * flow_mod.sum() \
            * p.water_rho_kg_per_l / 60.0 * dt
        t_tank = self.t_tank + (to_water - to_air) / (mass * p.water_cp)
        t_tank += used_kg / mass * (ta - t_tank)            # make-up water at ambient
        e = self._elec
        rec = {"g": g, "ta": ta, "p_dc": float(e["p"].sum()), "p_pump": p_pump,
               "p_valves": p_valves, "duty": self.duty, "flow_lpm": float(flow_mod.sum()),
               "t_tank": self.t_tank, "water_used_l": used_kg / p.water_rho_kg_per_l,
               "water_circulated_l": float(flow_mod.sum()) * dt / 60.0,
               "q_water_w": float((k_w * (t_mean - self.t_tank)).sum()),
               "q_evap_w": float(q_evap.sum()), "opened": opened,
               "p_string": e["p"], "v_string": e["v"], "i_string": e["i"],
               "bypassed": e["bypassed"], "t_string": self.t.mean(axis=1),
               "t_string_max": self.t.max(axis=1), "t_zone": t_zone, "valve": new.copy(),
               "alarm": (np.zeros(len(new), dtype=bool) if cmd.alarms is None
                         else np.asarray(cmd.alarms, dtype=bool))}
        self.t, self.t_tank = t_new, t_tank
        return rec


SCALARS = ["g", "ta", "p_dc", "p_pump", "p_valves", "duty", "flow_lpm", "t_tank",
           "water_used_l", "water_circulated_l", "q_water_w", "q_evap_w", "opened"]
PER_STRING = ["p_string", "v_string", "i_string", "bypassed", "t_string", "t_string_max",
              "t_zone", "valve", "alarm"]


def simulate_day(roof, w, controller, noise_seed=0, plant=None, on_step=None):
    """Run one controller over one day of weather. Returns (table, summary): the table has
    one row per sample with the scalar columns and <name>_<string number> columns.
    on_step(k, plant, rec), when given, is called after every sample with the plant's
    state BEFORE the step (rec["t_modules"]); it only reads (twin/showcase.py)."""
    plant = plant or Plant(roof, noise_seed)
    plant.reset(w.ta[0])
    controller.reset(roof)
    n, s = len(w), roof.shape[0]
    sc = {c: np.zeros(n) for c in SCALARS}
    ps = {c: np.zeros((n, s)) for c in PER_STRING}
    for k in range(n):
        obs = plant.observe(w, k)
        t_modules = plant.t if on_step is None else plant.t.copy()
        rec = plant.apply(w, k, controller.step(obs), obs.t_zone)
        if on_step is not None:
            on_step(k, plant, {**rec, "t_modules": t_modules})
        for c in SCALARS:
            sc[c][k] = rec[c]
        for c in PER_STRING:
            ps[c][k] = rec[c]
    table = pd.DataFrame({"t_local": w.t_local, **sc})
    for c in PER_STRING:
        for z in range(s):
            table[f"{c}_{z + 1}"] = ps[c][:, z]
    return table, summarise(table, roof.p, s)


def summarise(table, p, n_strings):
    """Energies, water and pump time of one simulated day (or of several, concatenated)."""
    h = p.dt_s / 3600.0
    valve = table[[f"valve_{z + 1}" for z in range(n_strings)]].to_numpy()
    e_dc, e_pump = table["p_dc"].sum() * h, table["p_pump"].sum() * h
    e_valves = table["p_valves"].sum() * h
    tz = table[[f"t_string_{z + 1}" for z in range(n_strings)]].to_numpy().mean(axis=1)
    lit = table["g"].to_numpy() >= 200.0
    return {"e_dc_wh": float(e_dc), "e_pump_wh": float(e_pump), "e_valves_wh": float(e_valves),
            "e_net_wh": float(e_dc - e_pump - e_valves),
            "water_used_l": float(table["water_used_l"].sum()),
            "water_circulated_l": float(table["water_circulated_l"].sum()),
            "pump_hours": float((table["p_pump"] > 0).sum() * h),
            "zone_hours": float(valve.sum() * h),
            "valve_openings": int(table["opened"].sum()),
            "alarms": int(table[[f"alarm_{z + 1}" for z in range(n_strings)]].to_numpy().sum()),
            "t_module_mean_c": float(tz[lit].mean()) if lit.any() else float("nan"),
            "t_module_max_c": float(table[[f"t_string_max_{z + 1}" for z in range(n_strings)]]
                                    .to_numpy().max()),
            "t_tank_max_c": float(table["t_tank"].max())}
