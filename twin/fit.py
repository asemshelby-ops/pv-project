"""Replace placeholder parameters by values fitted to rig files, without a code change.

    python -m twin.fit --data <copy of the SD card> --real
        -> twin/params_measured.json, tag [MEASURED]. twin/params.py load() overlays this
           file on the placeholders from then on: every twin program uses the new values.
    python -m twin.fit --data out/twin/validation_demo/raw
        -> out/twin/fit_synthetic.json, tag [FIT-SYNTHETIC] (a test of this routine only;
           it is NOT picked up automatically).
    python -m twin.fit --listing
        prints the single-diode values that reproduce the seller listing (the placeholders)
    python -m twin.fit --real --set spray_u_module_water=48 --source "spray test 2027-01-12"
        enters a value measured some other way (any scalar parameter of twin/params.py)

What is fitted, and from which files
  electrical   the I-V sweep files SW*/SS* (docs/paper/07): i_l_ref, i_o_ref, r_s,
               r_sh_ref, a_ref, alpha_sc_rel and, when the sweeps span >= 10 degC of cell
               temperature, eg_ref (which sets the temperature coefficient of the power).
  gamma        --gamma <1/degC> sets eg_ref so that the MODEL's gamma equals the gamma
               measured by analysis/gamma.py, e.g. when no sweep fit is possible.
  thermal      the 10 s day files: heat capacity (i.e. the time constant) and u0; u1 too
               when the files hold wind speed. Without an anemometer u1 keeps its
               placeholder and u0 becomes an effective value at wind_fallback_ms.

The fit only reads the files; analysis/ is imported, not changed.
"""
import argparse
import datetime
import json
import pathlib

import numpy as np
import pvlib
from scipy.optimize import brentq, least_squares

from twin import electrical, params, thermal

MIN_T_SPAN_EG = 10.0        # degC of cell temperature needed before eg_ref is fitted
MAX_SWEEPS = 80             # sweeps used by the electrical fit (evenly spread)
MAX_DAYS = 14               # days used by the thermal fit (evenly spread)
MIN_G_THERMAL = 50.0        # W/m2: rows below are not scored (night, dawn)
ROBUST_SCALE_A = 0.02       # A: current residuals beyond this count less (soft-L1)


# --- electrical: the listing ------------------------------------------------------------------

def electrical_from_listing(i_sc=5.88, v_oc=21.5, i_mp=5.55, v_mp=18.0, r_sh=None, p=None):
    """Single-diode values at reference conditions that pass through the four listing
    points: short circuit, open circuit, the maximum power point and dP/dV = 0 there.
    The model has five parameters and the listing four numbers: r_sh is given."""
    p = p or params.load(None, None)
    r_sh = p.r_sh_ref if r_sh is None else r_sh

    def resid(x):
        il, ln_io, rs, a = x
        io = np.exp(ln_io)

        def f(v, i):
            return il - io * np.expm1((v + i * rs) / a) - (v + i * rs) / r_sh - i
        g = io / a * np.exp((v_mp + i_mp * rs) / a) + 1.0 / r_sh
        return [f(0.0, i_sc), f(v_oc, 0.0), f(v_mp, i_mp), i_mp - v_mp * g / (1.0 + g * rs)]

    s = least_squares(resid, [i_sc, np.log(1e-9), 0.2, 1.0], x_scale=[1, 1, 0.1, 0.1],
                      xtol=1e-14, ftol=1e-14, gtol=1e-14)
    if s.cost > 1e-16:
        raise ValueError("no single-diode curve passes through these four points")
    il, ln_io, rs, a = s.x
    return {"i_l_ref": float(il), "i_o_ref": float(np.exp(ln_io)), "r_s": float(rs),
            "a_ref": float(a), "r_sh_ref": float(r_sh)}


# --- electrical: I-V sweeps -------------------------------------------------------------------

ELECTRICAL = ["i_l_ref", "i_o_ref", "r_s", "r_sh_ref", "a_ref", "alpha_sc_rel"]
_LOG = {"i_o_ref", "r_sh_ref"}          # fitted as logarithms: they span decades


def _current(p, v, g, t_cell):
    il, io, rs, rsh, a = electrical.desoto(p, g, t_cell)
    return pvlib.pvsystem.i_from_v(v, il, io, rs, rsh, a)


def electrical_from_sweeps(points, sweeps, p=None, max_sweeps=MAX_SWEEPS):
    """Least-squares fit of the single-diode parameters to measured I-V points.

    points: sweep_id, V, I (the SW files). sweeps: one row per sweep with sweep_id, G and
    T_cell (analysis.gamma.sweep_parameters, already filtered by usable_sweeps).
    Residual: modelled minus measured current at each measured voltage.
    Returns (values, diagnostics)."""
    p = p or params.load(None, None)
    ids = sweeps["sweep_id"].to_numpy()
    if len(ids) < 3:
        raise ValueError("fewer than 3 usable sweeps")
    if len(ids) > max_sweeps:
        ids = ids[np.linspace(0, len(ids) - 1, max_sweeps).round().astype(int)]
    cond = sweeps.set_index("sweep_id").loc[ids, ["G", "T_cell"]]
    pts = points[points["sweep_id"].isin(ids)]
    v, i = pts["V"].to_numpy(float), pts["I"].to_numpy(float)
    g = cond["G"].reindex(pts["sweep_id"]).to_numpy(float)
    t = cond["T_cell"].reindex(pts["sweep_id"]).to_numpy(float)
    t_span = float(cond["T_cell"].max() - cond["T_cell"].min())
    names = ELECTRICAL + (["eg_ref"] if t_span >= MIN_T_SPAN_EG else [])

    def pack(q):
        return [np.log(getattr(q, n)) if n in _LOG else getattr(q, n) for n in names]

    def unpack(x):
        return p.replace(**{n: float(np.exp(x[k]) if n in _LOG else x[k])
                            for k, n in enumerate(names)})

    def resid(x):
        return _current(unpack(x), v, g, t) - i

    bounds = {"i_l_ref": (0.1, 50.0), "i_o_ref": (np.log(1e-16), np.log(1e-4)),
              "r_s": (0.0, 5.0), "r_sh_ref": (np.log(5.0), np.log(1e5)),
              "a_ref": (0.2, 10.0), "alpha_sc_rel": (-0.002, 0.005), "eg_ref": (0.6, 2.0)}
    lo, hi = zip(*(bounds[n] for n in names))
    scale = [{"i_l_ref": 1.0, "r_s": 0.1, "a_ref": 0.1, "alpha_sc_rel": 1e-3,
              "eg_ref": 0.1}.get(n, 1.0) for n in names]
    # soft-L1 loss: a sweep whose cell temperature is off (the cells lead the back sheet
    # right after the module is uncovered) must not pull the whole fit
    s = least_squares(resid, np.clip(pack(p), lo, hi), bounds=(lo, hi), x_scale=scale,
                      loss="soft_l1", f_scale=ROBUST_SCALE_A)
    fitted = unpack(s.x)
    values = {n: getattr(fitted, n) for n in names}
    diag = {"sweeps_used": int(len(ids)), "points": int(len(v)),
            "rmse_current_a": float(np.sqrt(np.mean(resid(s.x) ** 2))),
            "t_cell_span_c": t_span, "eg_ref_fitted": "eg_ref" in names,
            "g_range_wm2": [float(cond["G"].min()), float(cond["G"].max())],
            "model_gamma_per_c": electrical.gamma_pmp(fitted),
            "model_stc": electrical.stc(fitted)}
    return values, diag


def calibrate_gamma(p, gamma):
    """eg_ref for which the model's temperature coefficient of P_mp equals `gamma`
    (1/degC, negative), everything else unchanged."""
    def f(eg):
        return electrical.gamma_pmp(p.replace(eg_ref=eg)) - gamma
    lo, hi = 0.3, 2.5
    if f(lo) * f(hi) > 0:
        raise ValueError(f"gamma {gamma:g} 1/degC is outside what eg_ref can reach "
                         f"({f(lo) + gamma:.5f} .. {f(hi) + gamma:.5f})")
    return float(brentq(f, lo, hi, xtol=1e-9))


# --- thermal: logged days ---------------------------------------------------------------------

def thermal_arrays(frame, p, max_days=MAX_DAYS):
    """The arrays the thermal replay needs, from an ingested frame (analysis/ingest.py
    read_logger, or validate.frame_from_days). Rows no detector could use (segment < 0)
    are left out. Returns a dict of arrays, with `first` marking each segment start and
    `dt` the seconds to the next row."""
    from data.schema import BACK_SENSORS
    f = frame[frame["segment"] >= 0]
    dates = sorted(f["date"].unique())
    if max_days and len(dates) > max_days:
        keep = {dates[k] for k in np.linspace(0, len(dates) - 1, max_days).round().astype(int)}
        f = f[f["date"].isin(keep)]
    f = f.reset_index(drop=True)
    seg = f["segment"].to_numpy()
    t_s = (f["t_utc"] - f["t_utc"].iloc[0]).dt.total_seconds().to_numpy()
    first = np.r_[True, seg[1:] != seg[:-1]]
    dt = np.r_[np.diff(t_s), p.dt_s]
    dt[np.r_[first[1:], True]] = p.dt_s             # last row of a segment: unused value
    wind = f["wind"].to_numpy(float) if "wind" in f else np.full(len(f), np.nan)
    has_wind = bool(np.isfinite(wind).mean() > 0.9)
    return {"t_meas": f[BACK_SENSORS].mean(axis=1).to_numpy(float),
            "g": f["irradiance"].to_numpy(float), "ta": f["T_ambient"].to_numpy(float),
            "wind": np.where(np.isfinite(wind), wind, p.wind_fallback_ms) if has_wind
            else np.full(len(f), p.wind_fallback_ms),
            "has_wind": has_wind, "first": first, "dt": dt, "index": f.index.to_numpy(),
            "frame": f}


def replay_temperature(p, arr):
    """Modelled module temperature for every row: each segment starts at its measured
    temperature and is then driven by irradiance, ambient and wind only."""
    k = p.area_m2 * thermal.u_loss(p, arr["wind"])
    t_ss = arr["ta"] + p.area_m2 * arr["g"] / k
    e = np.exp(-arr["dt"] * k / p.heat_capacity_j_per_k)
    out = np.empty(len(t_ss))
    t_meas, first = arr["t_meas"], arr["first"]
    t = 0.0
    for i in range(len(out)):
        if first[i]:
            t = t_meas[i]
        out[i] = t
        t = t_ss[i] + (t - t_ss[i]) * e[i]
    return out


def thermal_from_days(frame, p=None, max_days=MAX_DAYS):
    """Fit heat capacity and the Faiman coefficients to logged days.
    Returns (values, diagnostics)."""
    p = p or params.load(None, None)
    arr = thermal_arrays(frame, p, max_days)
    names = ["heat_capacity_j_per_k", "u0"] + (["u1"] if arr["has_wind"] else [])
    score = arr["g"] >= MIN_G_THERMAL
    if score.sum() < 500:
        raise ValueError("fewer than 500 daylight rows for the thermal fit")

    def unpack(x):
        return p.replace(**{n: float(v) for n, v in zip(names, x)})

    def resid(x):
        return (replay_temperature(unpack(x), arr) - arr["t_meas"])[score]

    lo = {"heat_capacity_j_per_k": 500.0, "u0": 5.0, "u1": 0.0}
    hi = {"heat_capacity_j_per_k": 1e5, "u0": 100.0, "u1": 40.0}
    s = least_squares(resid, [getattr(p, n) for n in names],
                      bounds=([lo[n] for n in names], [hi[n] for n in names]),
                      x_scale=[1000.0, 1.0, 1.0][:len(names)])
    fitted = unpack(s.x)
    values = {n: getattr(fitted, n) for n in names}
    diag = {"rows": int(score.sum()), "days": int(arr["frame"]["date"].nunique()),
            "wind_measured": arr["has_wind"],
            "rmse_c": float(np.sqrt(np.mean(s.fun ** 2))),
            "rmse_placeholder_c": float(np.sqrt(np.mean(
                (replay_temperature(p, arr) - arr["t_meas"])[score] ** 2))),
            "time_constant_min_at_fallback_wind":
                float(thermal.time_constant(fitted, p.wind_fallback_ms) / 60.0)}
    return values, diag


# --- the overlay file -------------------------------------------------------------------------

def write_overlay(path, values, tag, source, diagnostics):
    """Write (or extend) a params_measured.json. Values already in the file are kept
    unless this call fits them again."""
    path = pathlib.Path(path)
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    recs = old.get("params", {})
    for name, value in values.items():
        if name not in params.PARAMS:
            raise KeyError(name)
        recs[name] = {"value": value, "tag": tag, "source": source}
    data = {"_about": "Overlay for twin/params.py, written by twin/fit.py. Values here "
                      "replace the placeholders of the same name (twin.params.load).",
            "written": datetime.date.today().isoformat(), "tag": tag,
            "params": recs,
            "diagnostics": {**old.get("diagnostics", {}), **diagnostics}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def fit_folder(folder, p=None, gamma=None, electrical_fit=True, thermal_fit=True, log=print):
    """Everything that can be fitted from one folder of rig files.
    Returns (values, diagnostics, parameters with the values applied)."""
    from analysis import gamma as gamma_mod
    from analysis import ingest
    p = p or params.load(None, None)
    values, diag = {}, {}
    if electrical_fit:
        points, summary = gamma_mod.read_sweeps(folder)
        if len(points):
            sweeps = gamma_mod.usable_sweeps(gamma_mod.sweep_parameters(points, summary))
            v, d = electrical_from_sweeps(points, sweeps, p)
            values.update(v)
            diag["electrical"] = d
            p = p.replace(**v)
            log(f"electrical: {d['sweeps_used']} sweeps, {d['points']} points, rmse "
                f"{1000 * d['rmse_current_a']:.1f} mA, model gamma "
                f"{100 * d['model_gamma_per_c']:.3f} %/degC")
        else:
            log("electrical: no sweep files (SW*/SS*) in the folder - placeholders kept")
    if gamma is not None:
        values["eg_ref"] = calibrate_gamma(p, gamma)
        p = p.replace(eg_ref=values["eg_ref"])
        diag["gamma"] = {"target_per_c": gamma, "model_gamma_per_c": electrical.gamma_pmp(p)}
        log(f"gamma: eg_ref set to {values['eg_ref']:.4f} eV -> model gamma "
            f"{100 * diag['gamma']['model_gamma_per_c']:.3f} %/degC")
    if thermal_fit:
        v, d = thermal_from_days(ingest.read_logger(folder), p)
        values.update(v)
        diag["thermal"] = d
        p = p.replace(**v)
        log(f"thermal: {d['days']} days, rmse {d['rmse_c']:.2f} degC (placeholders: "
            f"{d['rmse_placeholder_c']:.2f}), time constant "
            f"{d['time_constant_min_at_fallback_wind']:.1f} min"
            + ("" if d["wind_measured"] else "; no wind in the files: u1 not fitted"))
    return values, diag, p


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", help="folder with the logger's files (day files, SW*/SS*)")
    ap.add_argument("--real", action="store_true",
                    help="the folder holds REAL rig files: tag [MEASURED] and write "
                         f"{params.MEASURED_PATH.name} next to params.py")
    ap.add_argument("--out", help="overlay file to write (default: see above)")
    ap.add_argument("--gamma", type=float,
                    help="measured gamma of P_mp in 1/degC (e.g. -0.0041): sets eg_ref")
    ap.add_argument("--no-electrical", action="store_true")
    ap.add_argument("--no-thermal", action="store_true")
    ap.add_argument("--listing", action="store_true",
                    help="print the single-diode values of the seller listing and stop")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                    help="enter a measured scalar parameter by hand (repeatable)")
    ap.add_argument("--source", help="where the --set values come from (required with --set)")
    a = ap.parse_args()
    if a.listing:
        for k, v in electrical_from_listing().items():
            print(f"{k} = {v:.6e}")
        return
    if not a.data and a.gamma is None and not a.set:
        ap.error("give --data, or --gamma alone, or --set, or --listing")
    tag = params.MEASURED if a.real else params.FIT_SYNTH
    out = pathlib.Path(a.out) if a.out else (
        params.MEASURED_PATH if a.real else pathlib.Path("out/twin/fit_synthetic.json"))
    if a.set:
        if not a.source:
            ap.error("--set needs --source: say which measurement the value comes from")
        manual = {}
        for item in a.set:
            name, _, value = item.partition("=")
            old = params.PARAMS.get(name)
            if old is None or isinstance(old.value, (tuple, dict)):
                ap.error(f"--set: '{name}' is not a scalar parameter of twin/params.py")
            manual[name] = type(old.value)(float(value))
        write_overlay(out, manual, tag, a.source, {})
        print(f"{len(manual)} parameter(s) {tag} -> {out}")
        if not a.data and a.gamma is None:
            return
    if a.data:
        values, diag, _ = fit_folder(a.data, gamma=a.gamma,
                                     electrical_fit=not a.no_electrical,
                                     thermal_fit=not a.no_thermal)
        source = f"twin.fit on {a.data}"
    else:
        p = params.load(None, None)
        values = {"eg_ref": calibrate_gamma(p, a.gamma)}
        diag = {"gamma": {"target_per_c": a.gamma}}
        source = f"twin.fit --gamma {a.gamma:g}"
    write_overlay(out, values, tag, source, diag)
    print(f"{len(values)} parameter(s) {tag} -> {out}")
    if not a.real:
        print("Not picked up automatically. Use it with --params on the twin programs.")


if __name__ == "__main__":
    main()
