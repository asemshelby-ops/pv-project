"""Temperature coefficient of P_mp from I-V sweeps (docs/paper/02).

Inputs: the sweep files the load firmware will write (docs/paper/07 sec. 7):
  /SWYYYYMMDD.csv   sweep_id,k,D,V,I                      raw points
  /SSYYYYMMDD.csv   sweep_id,date,time,uptime_ms,T1..T5,G_before,G_after,V_oc,...,flags
and the gamma runs marked on the log sheet ("gamma uncover" ... "gamma end").

Per sweep: P_mp from a parabola through the top of P(V); I_sc by extrapolating the flat
part of I(V) to V = 0. Per run: least squares of irradiance-corrected P_mp on cell
temperature; gamma = slope / value at 25 degC. Cross-check without the pyranometer:
P_mp / I_sc on temperature gives gamma - alpha.
"""
import pathlib

import numpy as np
import pandas as pd

import config
from data.schema import BACK_SENSORS

DROP_AFTER_UNCOVER_S = 120      # [ASSUMPTION] cells lead the back sheet at first
G_STABLE = 0.02                 # sweep and run irradiance stability, +/- 2 %
MIN_SPAN_C = 12.0               # a run needs this temperature span
U_B_REL = 0.028                 # [EST] systematic part common to all runs (docs/paper/02 sec. 5)


def read_sweeps(src):
    """Returns (points, summary); summary gets a naive local timestamp `t_local`."""
    src = pathlib.Path(src)
    pts = [pd.read_csv(f) for f in sorted(src.glob("SW" + "[0-9]" * 8 + ".csv"))]
    summ = [pd.read_csv(f, dtype={"date": str, "time": str})
            for f in sorted(src.glob("SS" + "[0-9]" * 8 + ".csv"))]
    if not pts or not summ:
        return pd.DataFrame(), pd.DataFrame()
    points, summary = pd.concat(pts, ignore_index=True), pd.concat(summ, ignore_index=True)
    t = pd.to_datetime(summary["date"] + " " + summary["time"], utc=True)
    summary["t_local"] = t.dt.tz_convert(config.TZ).dt.tz_localize(None)
    return points, summary


def sweep_parameters(points, summary, r_cable=0.0):
    """P_mp, V_mp, I_sc, fill factor per sweep, joined to its summary line.
    r_cable: measured cable resistance (ohm) between module and INA226, added back to V."""
    rows = []
    for sid, g in points.groupby("sweep_id"):
        v = g["V"].to_numpy() + g["I"].to_numpy() * r_cable
        i = g["I"].to_numpy()
        p = v * i
        top = p >= 0.97 * p.max()
        if top.sum() >= 5:
            c2, c1, c0 = np.polyfit(v[top], p[top], 2)
            v_mp = -c1 / (2 * c2)
            p_mp = c0 - c1 * c1 / (4 * c2)
            if not (c2 < 0 and v[top].min() <= v_mp <= v[top].max()):
                v_mp, p_mp = v[np.argmax(p)], p.max()      # fit not trusted: best point
        else:
            v_mp, p_mp = v[np.argmax(p)], p.max()
        rows.append({"sweep_id": sid, "P_mp": p_mp, "V_mp": v_mp, "n_top": int(top.sum()),
                     "v_min": v.min(), "_v": v, "_i": i})
    out = pd.DataFrame(rows).merge(summary, on="sweep_id", how="inner")
    i_sc = []
    for _, r in out.iterrows():
        flat = r["_v"] < 0.5 * r["V_oc"]
        if flat.sum() >= 3:
            slope, icpt = np.polyfit(r["_v"][flat], r["_i"][flat], 1)
            i_sc.append(icpt)
        else:
            i_sc.append(np.nan)
    out["I_sc"] = i_sc
    out["FF"] = out["P_mp"] / (out["V_oc"] * out["I_sc"])
    out["G"] = (out["G_before"] + out["G_after"]) / 2
    out["G_stable"] = (out["G_after"] - out["G_before"]).abs() / out["G_before"] <= G_STABLE
    out["T_module"] = out[BACK_SENSORS].mean(axis=1)
    out["T_cell"] = out["T_module"] + config.CELL_BACK_DT_C * out["G"] / 1000.0
    return out.drop(columns=["_v", "_i"])


def _fit(x, y):
    """y = a + b x by least squares; returns a, b, standard uncertainty of b, residual sd."""
    A = np.column_stack([np.ones_like(x), x])
    coef, res, *_ = np.linalg.lstsq(A, y, rcond=None)
    s = np.sqrt(res[0] / (len(x) - 2)) if len(res) else 0.0
    return coef[0], coef[1], s / np.sqrt(np.sum((x - x.mean()) ** 2)), s


def gamma_run(sweeps, t_uncover, t_end):
    """One heating run. Returns a dict of results, or a dict with `valid` False and why."""
    w = sweeps[(sweeps["t_local"] >= t_uncover + pd.Timedelta(seconds=DROP_AFTER_UNCOVER_S))
               & (sweeps["t_local"] <= t_end) & (sweeps["flags"] == 0) & sweeps["G_stable"]]
    out = {"t_uncover": t_uncover, "n_sweeps": len(w), "valid": False}
    if len(w) < 10:
        return {**out, "note": "fewer than 10 usable sweeps"}
    g_ref = w["G"].mean()
    w = w[(w["G"] - g_ref).abs() / g_ref <= G_STABLE]
    x = w["T_cell"].to_numpy() - 25.0
    span = float(x.max() - x.min())
    if span < MIN_SPAN_C:
        return {**out, "note": f"temperature span {span:.1f} degC below {MIN_SPAN_C}"}
    a, b, u_b, s = _fit(x, (w["P_mp"] * g_ref / w["G"]).to_numpy())
    a_i, b_i, *_ = _fit(x, (w["I_sc"] * g_ref / w["G"]).to_numpy())
    a_v, b_v, *_ = _fit(x, w["V_oc"].to_numpy())
    a_r, b_r, *_ = _fit(x, (w["P_mp"] / w["I_sc"]).to_numpy())
    alpha = b_i / a_i
    return {**out, "valid": True, "note": "", "n_sweeps": len(w), "G_ref": g_ref,
            "T_span_C": span, "P25_W": a, "gamma": b / a, "u_gamma_fit": u_b / a,
            "resid_sd_rel": s / a, "alpha": alpha, "beta": b_v / a_v,
            "gamma_isc_normalised": b_r / a_r + alpha,
            "T_cell": w["T_cell"].to_numpy(), "P_star": (w["P_mp"] * g_ref / w["G"]).to_numpy()}


def gamma_runs(sweeps, markers):
    """All runs between `gamma uncover` and the next `gamma end` on the joined log sheet."""
    runs = []
    for _, g in markers.groupby("date_local"):
        ends = g[g["event"] == "gamma end"]["t_local"].tolist()
        for t0 in g[g["event"] == "gamma uncover"]["t_local"]:
            later = [t for t in ends if t > t0]
            if later:
                runs.append(gamma_run(sweeps, t0, later[0]))
    return runs


def gamma_summary(runs):
    """Mean over valid runs with its standard and expanded (k = 2) uncertainty, 1/degC."""
    ok = [r for r in runs if r["valid"]]
    if not ok:
        return {"n_runs": 0}
    g = np.array([r["gamma"] for r in ok])
    s_runs = float(g.std(ddof=1)) if len(g) > 1 else float("nan")
    u = float(np.sqrt(np.nan_to_num(s_runs) ** 2 / len(g) + (U_B_REL * abs(g.mean())) ** 2))
    return {"n_runs": len(g), "n_days": len({r["t_uncover"].date() for r in ok}),
            "gamma": float(g.mean()), "s_runs": s_runs, "u": u, "U_k2": 2 * u,
            "gamma_isc_normalised": float(np.mean([r["gamma_isc_normalised"] for r in ok])),
            "alpha": float(np.mean([r["alpha"] for r in ok])),
            "beta": float(np.mean([r["beta"] for r in ok]))}
