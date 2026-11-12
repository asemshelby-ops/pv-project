"""Compare the ESP32 record with a reference data-logger export (docs/paper/04 sec. 6-7).

The reference format is not known yet, so the caller passes a mapping:
    {esp column: (reference column, scale)}      e.g. {"irradiance": ("PYR_mV", 1.0)}
Steps: find the clock lag from the irradiance records, shift the reference by it,
interpolate it at the ESP32 row times, then report bias, limits of agreement, RMSE and
regression per channel, plus the same for 10-min temperature rises.
"""
import numpy as np
import pandas as pd

import config
from data.schema import BACK_SENSORS

W = int(config.SPIKE_WINDOW_MIN * 60 / config.DT_S)
CRITERIA = {   # docs/paper/04 sec. 7 -- a PROPOSAL, pending the supervisor
    "temperature": ("bias_abs", 0.5), "rise": ("loa_abs", 0.3), "irradiance": ("bias_rel", 0.01),
    "voltage": ("bias_rel", 0.003), "current": ("bias_rel", 0.01), "lag": ("abs_s", 2.0)}


def _seconds(t, t0):
    return (pd.DatetimeIndex(t) - t0).total_seconds().to_numpy()


def clock_lag(t_esp, g_esp, t_ref, g_ref, max_lag_s=30):
    """Seconds by which the reference clock is AHEAD of the ESP32 clock: the shift that
    best lines up the changes of the two irradiance records (1 s grid)."""
    t0 = min(t_esp.min(), t_ref.min())
    xe, xr = _seconds(t_esp, t0), _seconds(t_ref, t0)
    grid = np.arange(max(xe[0], xr[0]) + max_lag_s, min(xe[-1], xr[-1]) - max_lag_s, 1.0)
    de = np.diff(np.interp(grid, xe, g_esp))
    lags = np.arange(-max_lag_s, max_lag_s + 1)
    score = [np.dot(de, np.diff(np.interp(grid + k, xr, g_ref))) for k in lags]
    return float(lags[int(np.argmax(score))])


def _stats(name, kind, esp, ref):
    ok = ~(np.isnan(esp) | np.isnan(ref))
    d = esp[ok] - ref[ok]
    slope, icpt = np.polyfit(ref[ok], esp[ok], 1)
    bias, sd = float(d.mean()), float(d.std(ddof=1))
    return {"quantity": name, "kind": kind, "n": int(ok.sum()), "bias": bias,
            "loa_low": bias - 1.96 * sd, "loa_high": bias + 1.96 * sd,
            "rmse": float(np.sqrt(np.mean(d ** 2))), "slope": float(slope),
            "intercept": float(icpt), "ref_mean": float(ref[ok].mean())}


def _rise(x):
    s = pd.Series(x)
    return (s - s.rolling(W + 1, min_periods=W + 1).min()).to_numpy()


def _passes(row):
    how, limit = CRITERIA[row["kind"]]
    if how == "bias_abs":
        return abs(row["bias"]) <= limit
    if how == "bias_rel":
        return abs(row["bias"]) <= limit * abs(row["ref_mean"])
    if how == "loa_abs":
        return max(abs(row["loa_low"]), abs(row["loa_high"])) <= limit
    return abs(row["bias"]) <= limit


def compare(esp, ref, mapping, ref_time="timestamp_utc", min_irradiance=200.0):
    """esp: analysis.ingest.read_logger frame (one day). ref: the reference export.
    Returns (table, lag_s). `passes` applies the proposed criteria of docs/paper/04."""
    t_ref = pd.to_datetime(ref[ref_time], utc=True)
    g_col, g_scale = mapping["irradiance"]
    lag = clock_lag(esp["t_utc"], esp["irradiance"].to_numpy(dtype=float),
                    t_ref, ref[g_col].to_numpy(dtype=float) * g_scale)
    t0 = esp["t_utc"].min()
    xe, xr = _seconds(esp["t_utc"], t0), _seconds(t_ref, t0) - lag
    inside = (xe >= xr[0]) & (xe <= xr[-1])
    rows = [{"quantity": "clock lag (reference ahead), s", "kind": "lag", "n": 1, "bias": lag,
             "loa_low": np.nan, "loa_high": np.nan, "rmse": np.nan, "slope": np.nan,
             "intercept": np.nan, "ref_mean": np.nan}]
    for col, (rcol, scale) in mapping.items():
        e = esp[col].to_numpy(dtype=float)[inside]
        r = np.interp(xe[inside], xr, ref[rcol].to_numpy(dtype=float) * scale)
        kind = "temperature" if col in BACK_SENSORS or col == "T_ambient" else col
        if col == "irradiance":
            keep = r >= min_irradiance
            e, r = e[keep], r[keep]
        rows.append(_stats(col, kind, e, r))
        if kind == "temperature":
            rows.append(_stats(f"{col}: rise over {config.SPIKE_WINDOW_MIN} min", "rise",
                               _rise(e), _rise(r)))
    table = pd.DataFrame(rows)
    table["passes"] = table.apply(_passes, axis=1)
    return table, lag
