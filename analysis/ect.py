"""Equivalent cell temperature (ECT) from the open-circuit voltage of the I-V sweeps, and
its comparison with the back-sheet sensors. NOT ADOPTED: prepared 2026-11-17, tested on
SYNTHETIC sweeps only. Method, sources and limits: docs/phase-b/03-ect-from-voc.md.

Why. The DS18B20 sit on the back sheet; the cells are a few degrees warmer and react
sooner. The open-circuit voltage is set by the cells themselves, so it gives their
temperature without a sensor: the "equivalent cell temperature" of IEC 60904-5. Every
sweep already measures V_oc (docs/paper/07 sec. 4), so this costs nothing extra.

Equation (IEC 60904-5:2011, Edition 2.0, Eq. (4), read in the publisher's preview):

    ECT = T_ref + (1 / beta) * [ V_oc / V_oc,ref - 1 - a * ln(G / G_ref) ]

  V_oc,ref   open-circuit voltage at the reference condition (G_ref, T_ref)
  beta       relative temperature coefficient of V_oc, 1/degC (negative)
  a          the standard's irradiance constant ("thermal diode voltage", relative to V_oc)
The standard also allows ln(I_sc / I_sc,ref) in place of ln(G / G_ref) ("self-reference",
config.ECT_USE_ISC), which takes the pyranometer out of the result.

What we can and cannot claim. The standard requires V_oc,ref, beta and a to come from a
calibration of the module under test, and says that the calibration of another module of
the same type is not enough (its example: 0.3 % in V_oc is 1 degC). We have a seller
listing and no temperature coefficient at all. So:
  * with the [PLACEHOLDER] values of config.py the LEVEL of the ECT is not trustworthy;
    its changes over minutes are (they depend on beta alone), and so is the time lag;
  * fit_parameters() replaces the placeholders by values fitted on this module's own
    sweeps, taken while the temperature is steady and using the back-sheet sensors plus
    the Sandia cell-to-back offset as the temperature. The fitted ECT then agrees with the
    sensors on average BY CONSTRUCTION: its level is not an independent measurement
    either. The lag and the behaviour during fast changes remain informative;
  * no conformity with IEC 60904-5 is claimed. Amendment 1 (2022) changes the method (a
    quadratic term in ln G, the lower irradiance limit raised from 200 to 400 W/m2); its
    equations were not legible in the preview and are not implemented. Only the limit is
    taken over (config.ECT_MIN_G_WM2).
"""
import math
import pathlib

import numpy as np
import pandas as pd

import config
from analysis import gamma
from data.schema import BACK_SENSORS

SWEEP_COLUMNS = ["sweep_id", "t_local", "G", "V_oc", "I_sc", "T_back_C", "ECT_placeholder_C",
                 "ECT_fitted_C"]
SPIKE_COLUMNS = ["event_id", "start", "cause", "n_sweeps", "lag_placeholder_s",
                 "r_placeholder", "lag_fitted_s", "r_fitted"]


def placeholder_parameters():
    """The values of config.py: seller listing for V_oc and I_sc (assumed to be at STC), an
    illustrative beta and a. NOT this module's calibration."""
    return {"V_ref": config.ECT_VOC_REF_V, "G_ref": config.ECT_G_REF_WM2,
            "T_ref": config.ECT_T_REF_C, "beta": config.ECT_BETA_VOC_REL, "a": config.ECT_A_IRR,
            "I_sc_ref": config.ECT_ISC_REF_A, "source": "placeholder"}


def usable(sweeps):
    """Sweeps an ECT may be computed from: usable for any sweep result (no firmware flag,
    steady irradiance; gamma.usable_sweeps), a measured V_oc, and irradiance at or above
    config.ECT_MIN_G_WM2 (the standard's lower limit)."""
    u = gamma.usable_sweeps(sweeps)
    return u[(u["G"] >= config.ECT_MIN_G_WM2) & u["V_oc"].notna()]


def ect(v_oc, g, par, i_sc=None, use_isc=None):
    """Eq. (4). g: in-plane irradiance of the sweep, W/m2. With use_isc (default
    config.ECT_USE_ISC) and i_sc given, the irradiance ratio is replaced by I_sc / I_sc,ref."""
    use_isc = config.ECT_USE_ISC if use_isc is None else use_isc
    if use_isc and i_sc is not None:
        x = np.log(np.asarray(i_sc, dtype=float) / par["I_sc_ref"])
    else:
        x = np.log(np.asarray(g, dtype=float) / par["G_ref"])
    return par["T_ref"] + (np.asarray(v_oc, dtype=float) / par["V_ref"] - 1.0 - par["a"] * x) / par["beta"]


def fit_parameters(sweeps, use_isc=None):
    """V_oc,ref, beta and a fitted on this module's own sweeps: least squares of
        V_oc = V_ref * [1 + beta * (T - T_ref) + a * ln(G / G_ref)]
    on usable sweeps taken while the back-sheet temperature is steady (slower than
    config.ECT_FIT_MAX_RATE_C_MIN before and after), with T = back-sheet mean + the Sandia
    cell-to-back offset (gamma.sweep_parameters, `T_cell`). Returns the parameters with
    `source` "fitted", or a dict with `source` None and `note` saying why there is no fit."""
    use_isc = config.ECT_USE_ISC if use_isc is None else use_isc
    u = usable(sweeps).sort_values("t_local")
    minutes = u["t_local"].diff().dt.total_seconds() / 60.0
    rate = (u["T_module"].diff() / minutes).abs()
    steady = (rate <= config.ECT_FIT_MAX_RATE_C_MIN) & (rate.shift(-1) <= config.ECT_FIT_MAX_RATE_C_MIN)
    w = u[steady.fillna(False)]
    par = placeholder_parameters()
    out = {**par, "source": None, "n": len(w)}
    if len(w) < config.ECT_FIT_MIN_SWEEPS:
        return {**out, "note": f"{len(w)} steady usable sweeps, {config.ECT_FIT_MIN_SWEEPS} needed"}
    x_t = w["T_cell"].to_numpy(dtype=float) - par["T_ref"]
    ratio = (w["I_sc"] / par["I_sc_ref"]) if use_isc else (w["G"] / par["G_ref"])
    x_g = np.log(ratio.to_numpy(dtype=float))
    if np.ptp(x_t) < 5.0 or np.ptp(x_g) < 0.2:
        return {**out, "note": f"steady sweeps span only {np.ptp(x_t):.1f} degC and a factor "
                               f"{math.exp(np.ptp(x_g)):.2f} in irradiance: too little for a fit"}
    A = np.column_stack([np.ones(len(w)), x_t, x_g])
    y = w["V_oc"].to_numpy(dtype=float)
    c, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ c
    beta = float(c[1] / c[0])
    return {**par, "V_ref": float(c[0]), "beta": beta, "a": float(c[2] / c[0]), "source": "fitted",
            "n": len(w), "T_span_C": float(np.ptp(x_t)), "G_span_factor": float(math.exp(np.ptp(x_g))),
            "resid_sd_V": float(resid.std(ddof=3)),
            "resid_sd_C": float(resid.std(ddof=3) / abs(beta * c[0])), "note": ""}


def lag_seconds(t_sweep_s, x_sweep, t_back_s, x_back, max_lag_s=config.ECT_LAG_MAX_S,
                step_s=config.DT_S):
    """Seconds by which the back-sheet series follows the series known at the sweep times
    (positive = the back sheet lags). Cross-correlation: for each trial lag L the sweep
    values are paired with the back-sheet temperature at (sweep time + L), read from the
    densely sampled back-sheet series, and the lag with the highest correlation is taken,
    refined by a parabola through its two neighbours. Only sweeps whose shifted times stay
    inside the back-sheet series for every trial lag are used, so all lags see the same
    sweeps. Returns (lag, correlation at the lag, sweeps used); NaN if it cannot be found."""
    ts, xs = np.asarray(t_sweep_s, dtype=float), np.asarray(x_sweep, dtype=float)
    tb, xb = np.asarray(t_back_s, dtype=float), np.asarray(x_back, dtype=float)
    ok = ~np.isnan(xb)
    tb, xb = tb[ok], xb[ok]
    if len(tb) < 3:
        return np.nan, np.nan, 0
    keep = ~np.isnan(xs) & (ts - max_lag_s >= tb[0]) & (ts + max_lag_s <= tb[-1])
    ts, xs = ts[keep], xs[keep]
    if len(ts) < 4 or np.ptp(xs) == 0:
        return np.nan, np.nan, int(len(ts))
    lags = np.arange(-max_lag_s, max_lag_s + step_s / 2, step_s)
    r = np.array([np.corrcoef(xs, np.interp(ts + L, tb, xb))[0, 1] for L in lags])
    if np.all(np.isnan(r)):
        return np.nan, np.nan, int(len(ts))
    k = int(np.nanargmax(r))
    lag = float(lags[k])
    if 0 < k < len(lags) - 1:                    # parabola through the peak and its neighbours
        y0, y1, y2 = r[k - 1], r[k], r[k + 1]
        den = y0 - 2 * y1 + y2
        if den < 0:
            lag += float(0.5 * (y0 - y2) / den * step_s)
    return lag, float(r[k]), int(len(ts))


def sweep_table(sweeps, fitted=None):
    """One row per usable sweep: ECT with the placeholder values and, if a fit exists, with
    the fitted ones, next to the back-sheet mean stored with the sweep."""
    u = usable(sweeps).sort_values("t_local")
    par = placeholder_parameters()
    out = pd.DataFrame({"sweep_id": u["sweep_id"].to_numpy(), "t_local": u["t_local"].to_numpy(),
                        "G": u["G"].to_numpy(), "V_oc": u["V_oc"].to_numpy(),
                        "I_sc": u["I_sc"].to_numpy(), "T_back_C": u["T_module"].to_numpy()})
    out["ECT_placeholder_C"] = ect(out["V_oc"], out["G"], par, out["I_sc"])
    out["ECT_fitted_C"] = (ect(out["V_oc"], out["G"], fitted, out["I_sc"])
                           if fitted and fitted.get("source") == "fitted" else np.nan)
    return out[SWEEP_COLUMNS]


def offset_summary(table, column):
    """ECT minus the back-sheet mean over all usable sweeps, and how it grows with
    irradiance (a straight line in G / 1000). The Sandia model expects about
    config.CELL_MINUS_BACK_C degC per 1000 W/m2 for this kind of module."""
    d = (table[column] - table["T_back_C"]).to_numpy(dtype=float)
    g = table["G"].to_numpy(dtype=float) / 1000.0
    ok = ~np.isnan(d)
    if ok.sum() < 3:
        return {"n": int(ok.sum())}
    slope, icpt = np.polyfit(g[ok], d[ok], 1)
    return {"n": int(ok.sum()), "median_C": float(np.median(d[ok])), "mean_C": float(d[ok].mean()),
            "sd_C": float(d[ok].std(ddof=1)), "per_1000_Wm2_C": float(slope),
            "at_zero_G_C": float(icpt)}


def spike_lags(table, frame, events):
    """For every valid spike: the lag of the back-sheet mean (10 s rows of `frame`) behind
    the ECT, by cross-correlation over the spike and config.ECT_SPIKE_PAD_MIN minutes on
    each side. A spike needs config.ECT_LAG_MIN_SWEEPS usable sweeps in that window."""
    if not len(table) or events is None or not len(events):
        return pd.DataFrame(columns=SPIKE_COLUMNS)
    pad = pd.Timedelta(minutes=config.ECT_SPIKE_PAD_MIN)
    reach = pd.Timedelta(seconds=config.ECT_LAG_MAX_S)
    t_back = frame["t_local"]
    back = frame[BACK_SENSORS].mean(axis=1).to_numpy(dtype=float)
    t0 = t_back.iloc[0]
    tb_s = (t_back - t0).dt.total_seconds().to_numpy()
    ts_s = (table["t_local"] - t0).dt.total_seconds().to_numpy()
    rows = []
    for _, e in events[(events["class"] == "spike") & events["valid"]].iterrows():
        a, b = pd.Timestamp(e["start"]) - pad, pd.Timestamp(e["end"]) + pad
        sel = ((table["t_local"] >= a) & (table["t_local"] <= b)).to_numpy()
        in_back = ((t_back >= a - reach) & (t_back <= b + reach)).to_numpy()
        row = {"event_id": e["event_id"], "start": e["start"], "cause": e["cause"],
               "n_sweeps": int(sel.sum())}
        for name in ("placeholder", "fitted"):
            x = table[f"ECT_{name}_C"].to_numpy(dtype=float)[sel]
            if sel.sum() < config.ECT_LAG_MIN_SWEEPS or np.all(np.isnan(x)):
                row[f"lag_{name}_s"], row[f"r_{name}"] = np.nan, np.nan
                continue
            lag, r, _ = lag_seconds(ts_s[sel], x, tb_s[in_back], back[in_back])
            row[f"lag_{name}_s"], row[f"r_{name}"] = lag, r
        rows.append(row)
    return pd.DataFrame(rows, columns=SPIKE_COLUMNS)


def run(sweeps, frame, events, out=None):
    """The ECT step of the pipeline: per-sweep table, fit, offset and lag. Writes
    ect_sweeps.csv and ect_spikes.csv to `out`."""
    fitted = fit_parameters(sweeps)
    table = sweep_table(sweeps, fitted)
    lags = spike_lags(table, frame, events)
    result = {"placeholder": placeholder_parameters(), "fitted": fitted, "sweeps": table,
              "spikes": lags, "offset": {}, "lag": {}}
    for name in ("placeholder", "fitted"):
        result["offset"][name] = offset_summary(table, f"ECT_{name}_C")
        v = lags[f"lag_{name}_s"].dropna()
        result["lag"][name] = ({"n": len(v), "median_s": float(v.median()),
                                "q25_s": float(v.quantile(0.25)), "q75_s": float(v.quantile(0.75))}
                               if len(v) else {"n": 0})
    if out is not None:
        out = pathlib.Path(out)
        table.to_csv(out / "ect_sweeps.csv", index=False)
        lags.to_csv(out / "ect_spikes.csv", index=False)
    return result


def markdown(result, synthetic=False):
    """The ECT section of summary.md."""
    f, ph = result["fitted"], result["placeholder"]
    lines = ["## Equivalent cell temperature from V_oc (IEC 60904-5 approach; NOT ADOPTED)", ""]
    if synthetic:
        lines += ["**SYNTHETIC DATA: the sweeps come from a module model with assumed "
                  "coefficients and an invented cell-to-back lag. This shows that the "
                  "calculation runs; it says nothing about the real module.**", ""]
    lines += [f"Usable sweeps (G >= {config.ECT_MIN_G_WM2:.0f} W/m2): {len(result['sweeps'])}.",
              f"Placeholder values [NOT this module's calibration]: V_oc,ref {ph['V_ref']:.2f} V at "
              f"{ph['G_ref']:.0f} W/m2 and {ph['T_ref']:.0f} degC, beta {100 * ph['beta']:.3f} %/degC, "
              f"a {ph['a']:.3f}."]
    if f.get("source") == "fitted":
        lines.append(f"Fitted on {f['n']} steady sweeps (span {f['T_span_C']:.1f} degC, factor "
                     f"{f['G_span_factor']:.2f} in irradiance): V_oc,ref {f['V_ref']:.3f} V, beta "
                     f"{100 * f['beta']:.3f} %/degC, a {f['a']:.4f}; residual {f['resid_sd_C']:.2f} degC. "
                     "The fit uses the back-sheet sensors as its temperature, so the fitted ECT "
                     "agrees with them on average by construction.")
    else:
        lines.append(f"No fit: {f.get('note', '')}. Only the placeholder ECT is given.")
    lines += ["", "| parameters | ECT minus back sheet: median (sd), degC | per 1000 W/m2, degC | "
              "lag of the back sheet during spikes: median (IQR), s | spikes |", "|---|---|---|---|---|"]
    for name in ("placeholder", "fitted"):
        o, lag = result["offset"][name], result["lag"][name]
        if "median_C" not in o:
            continue
        lag_text = (f"{lag['median_s']:+.0f} ({lag['q25_s']:+.0f} to {lag['q75_s']:+.0f})"
                    if lag["n"] else "-")
        lines.append(f"| {name} | {o['median_C']:+.2f} ({o['sd_C']:.2f}) | {o['per_1000_Wm2_C']:+.2f} "
                     f"| {lag_text} | {lag['n']} |")
    lines += ["", "With placeholder values only the lag and the changes over minutes mean "
              "something; the level (the offset) needs this module's own calibration.", ""]
    return lines
