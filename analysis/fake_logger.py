"""Write SYNTHETIC data in the exact file formats of the real rig, so the whole analysis
pipeline is exercised on files, not on in-memory tables:

  /YYYYMMDD.csv      10 s rows, UTC, one file per UTC day (firmware/logger)
  /markers.csv       button presses, UTC with milliseconds
  logsheets/*.csv    the typed daily log sheet (docs/paper/05)
  /SWYYYYMMDD.csv, /SSYYYYMMDD.csv   I-V sweep points and summaries (docs/paper/07)
  reference.csv      what a commercial data logger might export (docs/paper/04)

Every value is synthetic. The module model for the sweeps is a De Soto single-diode
model fitted to the seller listing with ASSUMED temperature coefficients: it exists only
to check that analysis/gamma.py recovers the coefficient the generator used.
"""
import math
import pathlib

import numpy as np
import pandas as pd
import pvlib
from scipy.optimize import brentq

import config
from data.schema import BACK_SENSORS, MARKER_CODES

HEADER = ["date", "time", *BACK_SENSORS, "T_ambient", "humidity", "irradiance",
          "voltage", "current", "power", "wind", "p", "alarm"]
EXTRA = ["D", "load_mode", "sweep_id", "uptime_ms", "status"]   # logger rows since 2026-11-12
SYN_ALPHA_ISC = 0.0005      # [ASSUMPTION, synthetic only] 1/degC
R_SWEEP = 0.75              # ohm, both load branches (firmware pv_config.h)


def _utc(local_naive):
    return pd.DatetimeIndex(local_naive).tz_localize(config.TZ).tz_convert("UTC")


def write_logger_files(days, out, extra_columns=True):
    """days: schema table in LOCAL time (data/synth.py). Writes /YYYYMMDD.csv in UTC.
    extra_columns=False writes the format used before 2026-11-12 (no D ... status).
    The five extra columns are what the logger writes with no load fitted and nothing
    wrong: NaN, none, 0, uptime of one boot 5 s before the first row, status 0."""
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    t = _utc(pd.to_datetime(days["date"] + " " + days["time"]))
    d = days.assign(date=t.strftime("%Y-%m-%d"), time=t.strftime("%H:%M:%S"),
                    wind="NaN", p="", alarm=0, D="NaN", load_mode="none", sweep_id=0,
                    uptime_ms=((t - t[0]).total_seconds() * 1000 + 5000).astype("int64"),
                    status=0)
    for key, g in d.groupby(d["date"].str.replace("-", "")):
        g[HEADER + EXTRA if extra_columns else HEADER].to_csv(out / f"{key}.csv", index=False)


def write_markers(schedule, out, codes=False):
    """schedule: {local date: [(HH:MM:SS, event, notes), ...]}. Writes /markers.csv (UTC,
    ms) and logsheets/<date>.csv, numbering the presses of each day in time order.
    codes=True writes the four-column file of a logger built with MARKER_CODES_ENABLED 1:
    the `code` of the event's name (data/schema.py, MARKER_CODES), 0 for every other row."""
    out = pathlib.Path(out)
    (out / "logsheets").mkdir(parents=True, exist_ok=True)
    code_of = {name: code for code, name in MARKER_CODES.items()}
    lines, millis = ["date,time,millis,code" if codes else "date,time,millis"], 100000
    for date in sorted(schedule):
        sheet = []
        for k, (hms, event, notes) in enumerate(sorted(schedule[date]), start=1):
            t = _utc([pd.Timestamp(f"{date} {hms}")])[0]
            millis += 60000 + 137 * k
            lines.append(f"{t:%Y-%m-%d},{t:%H:%M:%S}.{(137 * k) % 1000:03d},{millis}"
                         + (f",{code_of.get(event, 0)}" if codes else ""))
            sheet.append({"marker": k, "local_time": hms[:5], "event": event, "notes": notes})
        pd.DataFrame(sheet).to_csv(out / "logsheets" / f"{date}.csv", index=False)
    (out / "markers.csv").write_text("\n".join(lines) + "\n")


def add_load_off_tests(days, tests, rise_ss_c=6.0, tau_s=380.0):
    """SYNTHETIC controlled open-circuit tests (docs/paper/03 sec. 11, a proposal).
    tests: [(local date, 'HH:MM:SS', minutes)]. While the load is off, the power the module
    was delivering stays in it as heat: the back sheet rises towards `rise_ss_c` above its
    path with the thermal time constant, and falls back after the load is on again; the
    irradiance does not change. rise_ss_c = 6 degC is INVENTED so that the rise within
    10 min passes the 3 degC of the spike definition; the estimate of 03 sec. 11 for the
    real module is 2.4 to 5.9 degC. Returns (days, schedule): the changed table and the
    marker lines `load off` / `load on` to merge into a write_markers() schedule."""
    d = days.copy()
    t = pd.to_datetime(d["date"] + " " + d["time"])
    extra, off, schedule = np.zeros(len(d)), np.zeros(len(d), dtype=bool), {}
    for date, hms, minutes in tests:
        t0 = pd.Timestamp(f"{date} {hms}")
        s = (t - t0).dt.total_seconds().to_numpy()
        dur = 60.0 * minutes
        same_day = (d["date"] == date).to_numpy()
        during = same_day & (s >= 0) & (s <= dur)
        after = same_day & (s > dur)
        peak = rise_ss_c * (1 - math.exp(-dur / tau_s))
        extra += np.where(during, rise_ss_c * (1 - np.exp(-np.clip(s, 0, None) / tau_s)), 0.0)
        extra += np.where(after, peak * np.exp(-np.clip(s - dur, 0, None) / tau_s), 0.0)
        off |= during
        schedule.setdefault(date, []).extend([
            (hms, "load off", "synthetic"),
            (str((t0 + pd.Timedelta(minutes=minutes)).time()), "load on", "synthetic")])
    d[BACK_SENSORS] = np.round((d[BACK_SENSORS].to_numpy() + extra[:, None])
                               / config.DS18B20_LSB_C) * config.DS18B20_LSB_C
    d.loc[off, ["current", "power"]] = 0.0           # open circuit
    return d, schedule


# --- I-V sweeps of a gamma run -------------------------------------------------------------

def module_model():
    """De Soto parameters chosen by hand so that the curve resembles the seller listing
    (V_oc ~21.5 V, I_sc ~5.9 A, ~90-100 W): n = 1.1, 36 cells [ASSUMPTION, synthetic only]."""
    a_ref = 1.1 * 36 * 0.025693            # n * Ns * kT/q at 25 degC, volts
    i_l = 5.9
    return {"alpha_sc": SYN_ALPHA_ISC * i_l, "a_ref": a_ref, "I_L_ref": i_l,
            "I_o_ref": i_l * math.exp(-21.5 / a_ref), "R_s": 0.25, "R_sh_ref": 300.0}


def curve(model, g, t_cell):
    """Single-diode parameters (IL, I0, Rs, Rsh, nNsVth) at irradiance g and cell temperature."""
    return pvlib.pvsystem.calcparams_desoto(
        g, t_cell, alpha_sc=model["alpha_sc"], a_ref=model["a_ref"],
        I_L_ref=model["I_L_ref"], I_o_ref=model["I_o_ref"],
        R_sh_ref=model["R_sh_ref"], R_s=model["R_s"])


def true_pmp(model, g, t_cell):
    return float(pvlib.pvsystem.singlediode(*curve(model, g, t_cell))["p_mp"])


def _operating_point(params, v_oc, r):
    f = lambda v: float(pvlib.pvsystem.i_from_v(v, *params)) - v / r   # noqa: E731
    v = brentq(f, 1e-6, v_oc)
    return v, v / r


def sweep_points(model, g, t_cell, rng, n_coarse=24, n_fine=24):
    """One sweep as the firmware spec defines it: V_oc, a logarithmic coarse pass over the
    duty, then a linear fine pass around the best coarse point. Returns (points, v_oc)."""
    params = curve(model, g, t_cell)
    v_oc = float(pvlib.pvsystem.singlediode(*params)["v_oc"])
    duty = list(np.geomspace(0.02, 1.0, n_coarse))
    pts = [_operating_point(params, v_oc, R_SWEEP / d) for d in duty]
    m = int(np.argmax([v * i for v, i in pts]))
    fine = np.linspace(duty[max(m - 1, 0)], duty[min(m + 1, n_coarse - 1)], n_fine)
    duty += list(fine)
    pts += [_operating_point(params, v_oc, R_SWEEP / d) for d in fine]
    v, i = np.array(pts).T
    v = v + rng.normal(0, 0.005, len(v))
    i = i + rng.normal(0, 0.002, len(i))
    return pd.DataFrame({"k": np.arange(len(duty)), "D": np.round(duty, 5),
                         "V": np.round(v, 4), "I": np.round(i, 4)}), v_oc


def write_gamma_runs(runs, out, seed=0, period_s=15, minutes=20):
    """runs: [(local date, 'HH:MM:SS' of uncovering)]. Simulates the module heating after
    it is uncovered and writes the sweep files. Returns the generator's own gamma (1/degC,
    relative to P at 25 degC, at the run irradiance) for the test to compare with."""
    out, rng, model = pathlib.Path(out), np.random.default_rng(seed), module_model()
    g0, tau, sid = 900.0, 380.0, 0
    points, summary = {}, {}
    for date, hms in runs:
        t0 = pd.Timestamp(f"{date} {hms}")
        ta, rise = rng.uniform(28, 34), rng.uniform(19, 24)
        offsets = rng.normal(0, 0.15, len(BACK_SENSORS))
        for s in np.arange(0, minutes * 60 + 1, period_s):
            sid += 1
            g = g0 * (1 + 0.003 * math.sin(s / 400.0)) + rng.normal(0, 1.5)
            t_cell = ta + 2.0 + rise * (1 - math.exp(-s / tau))
            t_back = t_cell - config.CELL_BACK_DT_C * g / 1000 * (1 - math.exp(-s / 60.0))
            pts, v_oc = sweep_points(model, g, t_cell, rng)
            best = (pts["V"] * pts["I"]).idxmax()
            t = _utc([t0 + pd.Timedelta(seconds=float(s))])[0]
            key = t.strftime("%Y%m%d")
            points.setdefault(key, []).append(pts.assign(sweep_id=sid))
            temps = np.round(t_back + offsets + rng.normal(0, 0.05, len(offsets)), 4)
            summary.setdefault(key, []).append(
                [sid, f"{t:%Y-%m-%d}", f"{t:%H:%M:%S}", int(s * 1000), *temps,
                 round(g + rng.normal(0, 1)), round(g + rng.normal(0, 1)), round(v_oc, 4),
                 pts.at[best, "V"], pts.at[best, "I"], pts.at[best, "D"], 0])
    cols = ["sweep_id", "date", "time", "uptime_ms", *BACK_SENSORS, "G_before", "G_after",
            "V_oc", "V_best", "I_best", "D_best", "flags"]
    for key in points:
        pd.concat(points[key])[["sweep_id", "k", "D", "V", "I"]].to_csv(
            out / f"SW{key}.csv", index=False)
        pd.DataFrame(summary[key], columns=cols).to_csv(out / f"SS{key}.csv", index=False)
    temps = np.linspace(40.0, 55.0, 7)
    slope, at25 = np.polyfit(temps - 25.0, [true_pmp(model, g0, t) for t in temps], 1)
    return slope / at25


# --- a data-logger export for the validation day --------------------------------------------

def write_reference_export(days, date, out, seed=0, clock_ahead_s=3.0, period_s=5.0,
                           tc_offset_c=0.4, g_gain=1.005):
    """A reference record of one local day: 5 s sampling, its clock `clock_ahead_s` ahead
    of the ESP32, thermocouples reading `tc_offset_c` high, the PYR20 analog output in mV."""
    rng = np.random.default_rng(seed)
    d = days[days["date"] == date]
    t = _utc(pd.to_datetime(d["date"] + " " + d["time"]))
    x = (t - t[0]).total_seconds().to_numpy()
    grid = np.arange(0.0, x[-1], period_s)
    ref = pd.DataFrame({"timestamp_utc": (t[0] + pd.to_timedelta(grid + clock_ahead_s, unit="s"))
                        .strftime("%Y-%m-%d %H:%M:%S")})
    for src, dst in (("T1", "TC1"), ("T3", "TC2"), ("T5", "TC3")):
        ref[dst] = np.round(np.interp(grid, x, d[src]) + tc_offset_c
                            + rng.normal(0, 0.1, len(grid)), 2)
    ref["PYR_mV"] = np.round(np.interp(grid, x, d["irradiance"]) * g_gain
                             + rng.normal(0, 1.0, len(grid)), 1)
    ref["V_dc"] = np.round(np.interp(grid, x, d["voltage"]) + rng.normal(0, 0.01, len(grid)), 3)
    ref["I_dc"] = np.round(np.interp(grid, x, d["current"]) * 1.004
                           + rng.normal(0, 0.003, len(grid)), 4)
    ref.to_csv(pathlib.Path(out) / "reference.csv", index=False)
