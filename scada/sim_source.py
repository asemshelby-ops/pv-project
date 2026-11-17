"""The rooftop twin as a SIMULATION source of the SCADA (page /rooftop).

Nothing in this file is measured. It runs the digital twin of twin/ (120 simulated
modules, placeholder parameters) step by step and shows it on its own page. The REAL
module stays the only real data source of the SCADA:

  * the simulation writes to its OWN file (out/scada/simulation.sqlite, emptied at every
    start); it never opens the live or the replay historian and never writes to `samples`;
  * its tables have their own names (sim_plant, sim_strings); its alarm codes all start
    with SIM_ and every alarm text starts with "SIMULATION:";
  * every API answer under /api/sim/ carries the label, and the page shows it at all times.

The alarm life cycle (raised / cleared / acknowledged) is the one of scada/alarms.py; only
the conditions differ, because they come from simulated rows and not from logger bits.
Alarm limits below are display choices [ASSUMPTION], not properties of any hardware.
"""
import json
import math
import pathlib
import time

import numpy as np
import pandas as pd

import config
from scada import alarms, historian
from scada.model import AlarmDef

SIM_DB = "out/scada/simulation.sqlite"
ROW_S = int(config.DT_S)
HOT_LIMIT_C = 55.0          # [ASSUMPTION] hottest module of a string at or above this
TANK_WARM_C = 38.0          # [ASSUMPTION] spray water at or above this cools poorly
CONTROLLERS = ("predictive", "nasim", "threshold", "continuous", "none")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sim_plant (
    t_utc INTEGER PRIMARY KEY,          -- SIMULATED time (the synthetic weather day), not now
    g REAL, ta REAL, rh REAL, wind REAL,
    p_dc REAL, p_ref REAL,              -- with the controller / same rooftop without cooling
    p_pump REAL, p_valves REAL, duty REAL, flow_lpm REAL, t_tank REAL,
    e_net_wh REAL, e_ref_wh REAL,       -- sums of the simulated day so far
    water_used_l REAL, water_circulated_l REAL, pump_s REAL,
    ingested_wall REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sim_strings (
    t_utc INTEGER NOT NULL, string INTEGER NOT NULL,
    p REAL, v REAL, i REAL, t_mean REAL, t_max REAL, t_zone REAL,
    valve INTEGER, alarm INTEGER, bypassed INTEGER,
    PRIMARY KEY (t_utc, string)
);
"""


def alarm_defs(n_strings):
    """Alarm definitions of the simulation; one set per string plus the tank."""
    out = []
    for z in range(1, n_strings + 1):
        out += [
            AlarmDef(f"SIM_SPIKE_Z{z}", f"SIMULATION: thermal spike detected in zone {z}", 2,
                     "detector", "sim", "spike", 1),
            AlarmDef(f"SIM_HOT_Z{z}", f"SIMULATION: string {z} has a module at or above "
                     f"{HOT_LIMIT_C:g} degC", 2, "plant", "sim", "hot", 6),
            AlarmDef(f"SIM_BYPASS_Z{z}", f"SIMULATION: string {z} has a bypassed module", 2,
                     "plant", "sim", "bypass", 6),
        ]
    out.append(AlarmDef("SIM_TANK_WARM", f"SIMULATION: spray water at or above "
                        f"{TANK_WARM_C:g} degC", 3, "plant", "sim", "tank", 6))
    return out


def open_db(path=SIM_DB):
    """A fresh simulation file. Refuses any file that is not a simulation file."""
    path = pathlib.Path(path)
    if path.exists():
        conn = historian.connect(path)
        found = historian.get_meta(conn, "mode")
        conn.close()
        if found != "simulation":
            raise SystemExit(f"{path} is a '{found}' historian, not a simulation file: "
                             f"not touched.")
        for p in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            p.unlink(missing_ok=True)
    conn = historian.connect(path)
    conn.executescript(SCHEMA)
    historian.set_meta(conn, "mode", "simulation")
    conn.commit()
    return conn


def make_controller(name, p):
    """One of CONTROLLERS as a twin controller."""
    from twin import controllers as c
    if name == "predictive":
        return c.Predictive()
    if name == "nasim":
        return c.SpikeTriggered(c.nasim_factory(), "Spike-triggered (NASIM-GBT)")
    if name == "threshold":
        return c.SpikeTriggered(c.threshold_factory(config.THR_RISE_C),
                                "Spike-triggered (threshold rule)")
    if name == "continuous":
        return c.Continuous()
    if name == "none":
        return c.NoCooling()
    raise SystemExit(f"unknown controller '{name}'; choose from {', '.join(CONTROLLERS)}")


class ZoneMonitor:
    """A spike detector per zone that only WATCHES (it drives nothing), so the page shows
    detector alarms whatever controller runs -- as the logger's detectors do on the real
    module. A zone is not watched while its valve is open and for spike_blank_s after it
    closes, and its detector is then restarted: the re-heating after a spray is the
    controller's own doing, not a spike (the same rule as twin/controllers.py)."""

    def __init__(self, p, n, make_detector):
        self.p = p
        self.det = [make_detector() for _ in range(n)]
        self.reset()

    def reset(self):
        for d in self.det:
            d.reset()
        self.blank = np.zeros(len(self.det))

    def step(self, obs, valves):
        from detect.stream import Sample
        out = np.zeros(len(self.det), dtype=bool)
        was = self.blank > 0
        self.blank = np.where(valves, self.p.spike_blank_s,
                              np.maximum(self.blank - self.p.dt_s, 0.0))
        for z, d in enumerate(self.det):
            if self.blank[z] > 0:
                continue
            if was[z]:
                d.reset()
            out[z] = bool(d.update(Sample(tuple(obs.t_sensors[z]), obs.ta, obs.g, float("nan"),
                                          obs.doy, obs.minute)))
        return out


def monitor_detector():
    """(factory, name) of the watching detector: NASIM-GBT of the paper pipeline's run when
    that run exists on this computer, else the threshold rule with the team's fixed rise."""
    from twin import controllers as c
    try:
        return c.nasim_factory(), "NASIM-GBT (trained on synthetic data)"
    except FileNotFoundError:
        return (c.threshold_factory(config.THR_RISE_C),
                f"threshold rule, rise >= {config.THR_RISE_C:g} degC in 10 min")


# --- alarms ------------------------------------------------------------------------------------

def conditions(rec, n_strings):
    """Alarm conditions of one simulated step: {code: bool}."""
    out = {"SIM_TANK_WARM": bool(rec["t_tank"] >= TANK_WARM_C)}
    for z in range(n_strings):
        out[f"SIM_SPIKE_Z{z + 1}"] = bool(rec["alarm"][z])
        out[f"SIM_HOT_Z{z + 1}"] = bool(rec["t_string_max"][z] >= HOT_LIMIT_C)
        out[f"SIM_BYPASS_Z{z + 1}"] = bool(rec["bypassed"][z] > 0)
    return out


def _detail(code, rec):
    if code == "SIM_TANK_WARM":
        return f"tank {rec['t_tank']:.1f} degC"
    z = int(code.rsplit("Z", 1)[1]) - 1
    return (f"zone sensors {rec['t_zone'][z]:.1f} degC, hottest module "
            f"{rec['t_string_max'][z]:.1f} degC, G {rec['g']:.0f} W/m2 (simulated)")


class AlarmEngine:
    """Raised / cleared bookkeeping for the simulation, on the tables and the journal of
    scada/alarms.py (acknowledging uses alarms.acknowledge unchanged)."""

    def __init__(self, n_strings):
        self.defs = {d.code: d for d in alarm_defs(n_strings)}
        self.open = {}

    def evaluate(self, conn, t, rec, wall):
        n = len(rec["alarm"])
        for code, on in conditions(rec, n).items():
            d, a = self.defs[code], self.open.get(code)
            if on and a is None:
                alarm_id = alarms._raise(conn, d, t, wall, _detail(code, rec), 1)
                self.open[code] = {"id": alarm_id, "t_last": t, "clear": 0, "n": 1}
            elif on:
                a.update(t_last=t, clear=0, n=a["n"] + 1)
            elif a is not None:
                a["clear"] += 1
                if a["clear"] >= d.clear_rows:
                    conn.execute("UPDATE alarms SET cleared = 1, t_cleared = ?, t_last = ?, "
                                 "n_rows = ?, clear_count = ? WHERE id = ?",
                                 (t, a["t_last"], a["n"], a["clear"], a["id"]))
                    alarms._journal(conn, a["id"], code, "CLEARED", t, wall)
                    del self.open[code]
        for a in self.open.values():
            conn.execute("UPDATE alarms SET t_last = ?, n_rows = ?, clear_count = ? WHERE id = ?",
                         (a["t_last"], a["n"], a["clear"], a["id"]))


# --- the feeder ----------------------------------------------------------------------------------

class Feeder:
    """Runs the twin and writes one row set per simulated 10 s step."""

    def __init__(self, db=SIM_DB, controller="predictive", days=2, seed=None, roof_seed=None,
                 p=None, weathers=None, weather_note=None):
        """weathers: a list of twin.rooftop.Weather to use instead of synthetic days (a
        day measured by the rig, later; short test days), described by weather_note."""
        from data import synth
        from twin import compare, controllers, params, rooftop
        self.p = p or params.load()
        self.roof = rooftop.build(self.p, compare.ROOF_SEED if roof_seed is None else roof_seed)
        seed = compare.WEATHER_SEED if seed is None else seed
        if weathers is None:
            table, _ = synth.generate(seed, n_days=days, hard=True)
            weathers = rooftop.weather_days(table)
            weather_note = f"synthetic days of data/synth.py (hard variant, seed {seed})"
        self.weathers = list(weathers)
        # clear-sky irradiance on the module plane per sample: only for the page's drawing of
        # cloud shadows (measured irradiance / clear sky); the plant never reads it
        from twin import showcase
        self.g_clear = [showcase.clear_sky(w) for w in self.weathers]
        self.controller = make_controller(controller, self.p)
        self.reference = controllers.NoCooling()
        self.plant = rooftop.Plant(self.roof, compare.NOISE_SEED)
        self.ref_plant = rooftop.Plant(self.roof, compare.NOISE_SEED)
        self.engine = AlarmEngine(self.roof.shape[0])
        if isinstance(self.controller, controllers.SpikeTriggered):
            self.monitor, detector = None, self.controller.name     # its own alarms are shown
        else:
            factory, detector = monitor_detector()
            self.monitor = ZoneMonitor(self.p, self.roof.shape[0], factory)
        self.db, self.conn = db, None       # opened by the thread that steps (SQLite rule)
        conn = open_db(db)
        self.day, self.k = 0, -1
        self.info = {
            "controller": self.controller.name, "controller_key": controller,
            "alarm_detector": detector,
            "weather": weather_note or "weather given by the caller",
            "n_strings": int(self.roof.shape[0]), "modules_per_string": int(self.roof.shape[1]),
            "sensor_idx": self.roof.sensor_idx.tolist(),
            "soiling": np.round(self.roof.soiling, 4).tolist(),
            "wind_factor": np.round(self.roof.wind_factor, 3).tolist(),
            "parameter_tags": self.p.tags(), "days": len(self.weathers),
            "hot_limit_c": HOT_LIMIT_C, "tank_warm_c": TANK_WARM_C}
        historian.set_meta(conn, "sim_info", json.dumps(self.info))
        conn.commit()
        conn.close()

    def _start_day(self):
        w = self.weathers[self.day]
        self.plant.reset(w.ta[0])
        self.ref_plant.reset(w.ta[0])
        self.controller.reset(self.roof)
        self.reference.reset(self.roof)
        if self.monitor is not None:
            self.monitor.reset()
        self.sums = {"e_net_wh": 0.0, "e_ref_wh": 0.0, "water_used_l": 0.0,
                     "water_circulated_l": 0.0, "pump_s": 0.0}

    def step(self, wall=None):
        """Advance one sample and store it. Returns False when the last day is over."""
        if self.day >= len(self.weathers):
            return False
        if self.conn is None:
            self.conn = historian.connect(self.db)
        if self.k < 0:
            self._start_day()
            self.k = 0
        w, k, dt = self.weathers[self.day], self.k, self.p.dt_s
        wall = time.time() if wall is None else wall
        obs = self.plant.observe(w, k)
        rec = self.plant.apply(w, k, self.controller.step(obs), obs.t_zone)
        if self.monitor is not None:
            rec["alarm"] = self.monitor.step(obs, rec["valve"])
        ref_obs = self.ref_plant.observe(w, k)
        ref = self.ref_plant.apply(w, k, self.reference.step(ref_obs), ref_obs.t_zone)
        s = self.sums
        s["e_net_wh"] += (rec["p_dc"] - rec["p_pump"] - rec["p_valves"]) * dt / 3600
        s["e_ref_wh"] += ref["p_dc"] * dt / 3600
        s["water_used_l"] += rec["water_used_l"]
        s["water_circulated_l"] += rec["water_circulated_l"]
        s["pump_s"] += dt if rec["p_pump"] > 0 else 0.0
        t = int(pd.Timestamp(w.t_local[k]).tz_localize(config.TZ).timestamp())
        wind = None if not math.isfinite(w.wind[k]) else float(w.wind[k])
        c = self.conn
        c.execute("INSERT OR REPLACE INTO sim_plant VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (t, rec["g"], rec["ta"], float(w.rh[k]), wind, rec["p_dc"], ref["p_dc"],
                   rec["p_pump"], rec["p_valves"], rec["duty"], rec["flow_lpm"], rec["t_tank"],
                   s["e_net_wh"], s["e_ref_wh"], s["water_used_l"], s["water_circulated_l"],
                   s["pump_s"], wall))
        c.executemany(
            "INSERT OR REPLACE INTO sim_strings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(t, z + 1, float(rec["p_string"][z]), float(rec["v_string"][z]),
              float(rec["i_string"][z]), float(rec["t_string"][z]), float(rec["t_string_max"][z]),
              float(rec["t_zone"][z]), int(rec["valve"][z]), int(rec["alarm"][z]),
              int(rec["bypassed"][z])) for z in range(self.roof.shape[0])])
        historian.set_meta(c, "sim_modules", json.dumps(np.round(self.plant.t, 2).tolist()))
        historian.set_meta(c, "sim_progress", json.dumps(
            {"day": self.day + 1, "date": w.date, "finished": False,
             "g_clear": round(float(self.g_clear[self.day][k]), 1)}))
        self.engine.evaluate(c, t, rec, wall)
        c.commit()
        self.k += 1
        if self.k >= len(w):
            self.day, self.k = self.day + 1, -1
            if self.day >= len(self.weathers):
                historian.set_meta(c, "sim_progress", json.dumps(
                    {"day": self.day, "date": w.date, "finished": True}))
                c.commit()
        return True

    def run(self, stop, speed=120.0):
        """Feed until the days are over or `stop` is set; one step every 10 / speed s."""
        pause = self.p.dt_s / max(speed, 1e-6)
        nxt = time.monotonic()
        while not stop.is_set() and self.step():
            nxt += pause
            delay = nxt - time.monotonic()
            if delay > 0:
                stop.wait(delay)
            else:
                nxt = time.monotonic()          # the computer cannot keep up: no catch-up burst
        self.close()

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None


# --- queries for the HMI -------------------------------------------------------------------------

def _label():
    from twin import SIM_NOTICE
    return SIM_NOTICE


def state(conn, extra=None):
    """Newest simulated step: plant values, the six strings, module temperatures."""
    info = json.loads(historian.get_meta(conn, "sim_info", "{}"))
    row = conn.execute("SELECT * FROM sim_plant ORDER BY t_utc DESC LIMIT 1").fetchone()
    out = {"available": True, "simulation": True, "label": _label(), **info, **(extra or {}),
           "server_wall": time.time(), "latest": None, "strings": [], "modules": None,
           "progress": json.loads(historian.get_meta(conn, "sim_progress", "{}")),
           "alarms": alarms.summary(conn)}
    if row is None:
        return out
    r = dict(row)
    r["p_net"] = r["p_dc"] - r["p_pump"] - r["p_valves"]
    out["latest"] = r
    out["strings"] = [dict(x) for x in conn.execute(
        "SELECT * FROM sim_strings WHERE t_utc = ? ORDER BY string", (r["t_utc"],))]
    out["modules"] = json.loads(historian.get_meta(conn, "sim_modules", "null"))
    return out


def series(conn, span, end=None, points=600):
    """Time series of the last `span` simulated seconds, bucketed like scada/server.py:
    analog values are averaged, valve and alarm flags are OR-ed."""
    newest = conn.execute("SELECT MAX(t_utc) FROM sim_plant").fetchone()[0]
    span = int(min(max(span, 600), 31 * 86400))
    keys = ["g", "p_dc", "p_ref", "p_net", "p_pump", "t_tank", "t_mean", "t_min", "t_max"]
    out = {"simulation": True, "label": _label(), "t": [], **{k: [] for k in keys},
           "valve": [], "alarm": [], "span": span, "bucket_s": ROW_S, "end": end, "newest": newest}
    if newest is None:
        return out
    end = newest if end is None else int(end)
    start = end - span
    bucket = max(ROW_S, math.ceil(span / points / ROW_S) * ROW_S)
    out.update(end=end, bucket_s=bucket)
    plant = conn.execute("SELECT t_utc, g, p_dc, p_ref, p_pump, p_valves, t_tank FROM sim_plant "
                         "WHERE t_utc > ? AND t_utc <= ? ORDER BY t_utc", (start, end)).fetchall()
    per = {}
    for r in conn.execute("SELECT t_utc, string, t_mean, valve, alarm FROM sim_strings "
                          "WHERE t_utc > ? AND t_utc <= ? ORDER BY t_utc, string", (start, end)):
        per.setdefault(r["t_utc"], []).append(r)
    groups = {}
    for r in plant:
        groups.setdefault(r["t_utc"] // bucket, []).append(r)
    mean = lambda v: sum(v) / len(v)                                   # noqa: E731
    for key, g in groups.items():
        out["t"].append(g[0]["t_utc"] if bucket == ROW_S else key * bucket)
        for k in ("g", "p_dc", "p_ref", "p_pump", "t_tank"):
            out[k].append(mean([r[k] for r in g]))
        out["p_net"].append(mean([r["p_dc"] - r["p_pump"] - r["p_valves"] for r in g]))
        rows = [per.get(r["t_utc"], []) for r in g]
        n = max((len(x) for x in rows), default=0)
        temps = [[x[z]["t_mean"] for x in rows if len(x) > z] for z in range(n)]
        string_means = [mean(t) for t in temps if t]
        out["t_mean"].append(mean(string_means) if string_means else None)
        out["t_min"].append(min(string_means) if string_means else None)
        out["t_max"].append(max(string_means) if string_means else None)
        out["valve"].append([int(any(x[z]["valve"] for x in rows if len(x) > z)) for z in range(n)])
        out["alarm"].append([int(any(x[z]["alarm"] for x in rows if len(x) > z)) for z in range(n)])
    return out
