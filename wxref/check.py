"""Daily PYR20 check: measured in-plane irradiance against the MODEL's, on clear-sky hours.

Per hour (the hour ENDING at the model's stamp, as Open-Meteo averages radiation):
  cs        clear-sky in-plane irradiance: pvlib Ineichen (Linke climatology), transposed
            to the tilt of that day and our azimuth, isotropic sky, albedo 0.2 (the
            model's own assumption) -- the clear-sky index is always taken IN THE PLANE
            (CLAUDE.md), never against horizontal GHI
  measured  mean of the PYR20 rows inside the hour; coverage = share of 10 s rows present
  clear     the hour counts only if ALL hold (limits in config.py, WX_CS_*):
              cs >= WX_CS_MIN_POA_WM2                      sun high enough
              model_poa / cs >= WX_CS_MIN_K_MODEL          the model sees no cloud
              model cloud cover <= WX_CS_MAX_CLOUD_PCT     at both ends of the hour (when a
                                                           weather source gives it)
              std/mean of (PYR20 / cs) per minute <= WX_CS_MAX_CV    steady sky at the rig
              coverage >= WX_CS_MIN_COVERAGE
            The steadiness test is scale-free, and the measured LEVEL is not a criterion, so
            a dirty dome (reading 10 % low) does not hide its own hours.
  ratio_h   measured mean / model in-plane irradiance

Per local day: the ratio is the median of ratio_h over the clear hours (at least
WX_CHECK_MIN_HOURS, else "no clear hours" -- a cloudy or dusty day is skipped, not flagged).

Over days (assess): the reference is WX_RATIO_REFERENCE, or the median of the first
WX_BASELINE_DAYS valid days after the start or after the last KNOWN EVENT (a tilt change of
WX_TILT_SCHEDULE, or a WX_KNOWN_EVENTS entry): the event day itself is reported as
KNOWN_EVENT, never as a fault, and the reference starts again. The rolling ratio (median of
the last WX_DRIFT_DAYS valid days) outside reference x (1 +/- WX_RATIO_BAND) is a DRIFT:
likely a dirty dome, a tilt or level change, or a sensor fault. A daily ratio outside
WX_RATIO_SANE is flagged at once. The trend is a straight line through the last
WX_TREND_DAYS valid days, in % of the reference per week.

The ratio is NOT expected to be 1: the model has its own errors (aerosol, the transposition)
and the PYR20 is a silicon sensor (400-1100 nm) compared with a broadband model value. The
check watches the CHANGE of the ratio, not its value.
"""
import numpy as np
import pandas as pd

import config
from wxref import PREFIX

STATUSES = {
    "OK": "rolling ratio inside the band around the reference",
    "DRIFT": "rolling ratio outside the band: likely a dirty dome, a tilt/level change or a "
             "sensor fault; inspect the PYR20",
    "OUT_OF_RANGE": "daily ratio outside the sane range: scaling, wiring or sensor fault",
    "BASELINE": "reference being built from the first valid days",
    "KNOWN_EVENT": "known event (not a fault); the reference starts again",
    "NO_CLEAR_HOURS": "too few clear-sky hours: not checked",
    "NO_DATA": "no measured irradiance or no model data for the day",
}
FLAGS = {"DRIFT", "OUT_OF_RANGE"}
CHECK_COLUMNS = ["date", "status", "pyr20_model_ratio", "ratio_rolling", "reference_ratio",
                 "drift_pct", "trend_pct_per_week", "clear_hours", "daylight_hours",
                 "meas_clear_mean_wm2", PREFIX + "clear_mean_wm2", "ratio_iqr", "wx_source",
                 "tilt_deg", "epoch", "event", "message"]
HOUR_COLUMNS = ["t_utc", "date", "tilt_deg", "cs_poa_wm2", "meas_mean_wm2", "coverage",
                "meas_cv", PREFIX + "poa_wm2", PREFIX + "cloud_cover_pct", "k_model", "clear",
                "ratio"]


def _seconds(t):
    """Unix seconds (int64 array) from Unix numbers or tz-aware datetimes."""
    s = pd.Series(t)
    if pd.api.types.is_datetime64_any_dtype(s):
        if s.dt.tz is None:
            raise ValueError("naive timestamps: give t_utc as UTC")
        return s.astype("datetime64[s, UTC]").astype("int64").to_numpy()
    return pd.to_numeric(s).astype("int64").to_numpy()


def _local_dates(t_s):
    return (pd.to_datetime(t_s, unit="s", utc=True).tz_convert(config.TZ)
            .strftime("%Y-%m-%d").to_numpy())


def clear_sky_poa(t_s, site):
    """Clear-sky in-plane irradiance (W/m2) at Unix seconds t_s, with the tilt of each local
    day. pvlib Ineichen + Linke climatology, isotropic transposition, albedo 0.2."""
    import pvlib
    t_s = np.asarray(t_s, dtype="int64")
    out = np.zeros(len(t_s))
    if not len(t_s):
        return out
    times = pd.to_datetime(t_s, unit="s", utc=True)
    loc = pvlib.location.Location(site.lat, site.lon, tz="UTC")
    cs = loc.get_clearsky(times, model="ineichen")
    sp = loc.get_solarposition(times)
    tilt = np.array([site.tilt_on(d) for d in _local_dates(t_s)])
    for tl in np.unique(tilt):
        m = tilt == tl
        poa = pvlib.irradiance.get_total_irradiance(
            tl, site.azimuth_deg, sp.apparent_zenith[m], sp.azimuth[m], cs.dni[m], cs.ghi[m],
            cs.dhi[m], albedo=0.2)
        out[m] = poa["poa_global"].fillna(0.0).clip(lower=0.0).to_numpy()
    return out


def hours(measured, model_rows, site, source=None):
    """Hourly table (HOUR_COLUMNS) for every model hour of `source` that has measured rows.
    measured: DataFrame with t_utc (Unix s or UTC datetimes) and irradiance (W/m2).
    model_rows: Open-Meteo rows (wxref.openmeteo.ROW_COLUMNS) of any sources."""
    source = source or config.WX_CHECK_SOURCE
    m = pd.DataFrame({"t": _seconds(measured["t_utc"]),
                      "g": pd.to_numeric(measured["irradiance"], errors="coerce")})
    m = m[m["g"].notna()]
    src = model_rows[model_rows["wx_source"] == source]
    src = src[src[PREFIX + "poa_wm2"].notna()]
    if not len(m) or not len(src):
        return pd.DataFrame(columns=HOUR_COLUMNS)
    m["hour"] = -(-m["t"] // 3600) * 3600                     # ceil: (t-1h, t] -> t
    per_hour = m.groupby("hour")["g"].agg(["mean", "size"])
    # per-minute steadiness of PYR20 / clear-sky (minute midpoints)
    m["minute"] = m["t"] // 60
    mins = m.groupby("minute")["g"].mean()
    mid = mins.index.to_numpy() * 60 + 30
    cs_min = clear_sky_poa(mid, site)
    k = pd.Series(np.where(cs_min > 10.0, mins.to_numpy() / np.maximum(cs_min, 1e-9), np.nan),
                  index=-(-mid // 3600) * 3600)
    kk = k.groupby(level=0)
    cv = kk.std() / kk.mean()

    t = src["t_utc"].to_numpy("int64")
    keep = np.isin(t, per_hour.index.to_numpy())
    src, t = src[keep], t[keep]
    if not len(t):
        return pd.DataFrame(columns=HOUR_COLUMNS)
    # clear-sky hour mean: 1-min samples inside (t-1h, t]
    grid = (t[:, None] - 3600 + 30 + 60 * np.arange(60)[None, :]).ravel()
    cs_hour = clear_sky_poa(grid, site).reshape(len(t), 60).mean(axis=1)
    model_poa = src[PREFIX + "poa_wm2"].to_numpy(float)
    cloud = _cloud_ends(model_rows, t)
    meas = per_hour.reindex(t)
    out = pd.DataFrame({
        "t_utc": t, "date": _local_dates(t - 1800),
        "tilt_deg": src[PREFIX + "tilt_deg"].to_numpy(float), "cs_poa_wm2": cs_hour,
        "meas_mean_wm2": meas["mean"].to_numpy(),
        "coverage": meas["size"].to_numpy() / (3600.0 / config.DT_S),
        "meas_cv": cv.reindex(t).to_numpy(), PREFIX + "poa_wm2": model_poa,
        PREFIX + "cloud_cover_pct": cloud})
    out["k_model"] = np.where(cs_hour > 0, model_poa / np.maximum(cs_hour, 1e-9), np.nan)
    out["clear"] = ((out["cs_poa_wm2"] >= config.WX_CS_MIN_POA_WM2)
                    & (out["k_model"] >= config.WX_CS_MIN_K_MODEL)
                    & ~(out[PREFIX + "cloud_cover_pct"] > config.WX_CS_MAX_CLOUD_PCT)
                    & (out["meas_cv"] <= config.WX_CS_MAX_CV)
                    & (out["coverage"] >= config.WX_CS_MIN_COVERAGE))
    out["ratio"] = np.where(model_poa > 0, out["meas_mean_wm2"] / np.where(model_poa > 0,
                                                                          model_poa, 1), np.nan)
    return out[HOUR_COLUMNS].reset_index(drop=True)


def _cloud_ends(model_rows, t):
    """Max of the model cloud cover at both ends of each hour (forecast, else archive);
    NaN where no weather source gives it (the cloud test is then skipped)."""
    col = PREFIX + "cloud_cover_pct"
    out = np.full(len(t), np.nan)
    for s in ("archive", "forecast"):                  # forecast applied last = preferred
        g = model_rows[(model_rows["wx_source"] == s) & model_rows[col].notna()]
        if not len(g):
            continue
        c = g.set_index("t_utc")[col]
        both = np.fmax(c.reindex(t).to_numpy(float), c.reindex(t - 3600).to_numpy(float))
        out = np.where(np.isnan(both), out, both)
    return out


def daily(hour_table, dates=None, source=None):
    """One row per local date: ratio over clear hours and the counts (no status yet)."""
    source = source or config.WX_CHECK_SOURCE
    h = hour_table
    dates = sorted(set(h["date"]) | set(dates or []))
    rows = []
    for d in dates:
        g = h[h["date"] == d]
        day = g[g["cs_poa_wm2"] >= config.WX_CS_MIN_POA_WM2]
        c = g[g["clear"].astype(bool)]
        r = c["ratio"].to_numpy(float)
        enough = len(c) >= config.WX_CHECK_MIN_HOURS
        rows.append({
            "date": d, "clear_hours": len(c), "daylight_hours": len(day),
            "pyr20_model_ratio": float(np.median(r)) if enough else np.nan,
            "ratio_iqr": float(np.subtract(*np.percentile(r, [75, 25]))) if enough else np.nan,
            "meas_clear_mean_wm2": float(c["meas_mean_wm2"].mean()) if len(c) else np.nan,
            PREFIX + "clear_mean_wm2": float(c[PREFIX + "poa_wm2"].mean()) if len(c) else np.nan,
            "wx_source": source if len(g) else None,
            "tilt_deg": float(g["tilt_deg"].iloc[0]) if len(g) else np.nan,
            "has_data": bool(len(g))})
    return pd.DataFrame(rows, columns=["date", "clear_hours", "daylight_hours",
                                       "pyr20_model_ratio", "ratio_iqr", "meas_clear_mean_wm2",
                                       PREFIX + "clear_mean_wm2", "wx_source", "tilt_deg",
                                       "has_data"])


def assess(days, site, reference=None):
    """Status, reference, rolling ratio, drift and trend for each day (CHECK_COLUMNS)."""
    reference = config.WX_RATIO_REFERENCE if reference is None else reference
    band, (lo, hi) = config.WX_RATIO_BAND, config.WX_RATIO_SANE
    events = site.events()
    out, epoch, valid, ref = [], 0, [], reference
    for r in days.sort_values("date").to_dict("records"):
        r = {**r, "epoch": epoch, "event": None, "reference_ratio": np.nan,
             "ratio_rolling": np.nan, "drift_pct": np.nan, "trend_pct_per_week": np.nan}
        ratio = r["pyr20_model_ratio"]
        if r["date"] in events:
            epoch += 1
            valid, ref = [], reference
            r.update(epoch=epoch, event=events[r["date"]], status="KNOWN_EVENT",
                     message=f"{events[r['date']]}: known event, not a fault; the reference "
                             "starts again from the next valid days")
        elif not r.get("has_data", True):
            r.update(status="NO_DATA", message=STATUSES["NO_DATA"])
        elif not np.isfinite(ratio):
            r.update(status="NO_CLEAR_HOURS",
                     message=f"{r['clear_hours']} clear-sky hour(s), at least "
                             f"{config.WX_CHECK_MIN_HOURS} needed: not checked")
        elif not lo <= ratio <= hi:
            r.update(status="OUT_OF_RANGE",
                     message=f"PYR20/model {ratio:.3f} outside {lo:g}..{hi:g}: scaling, wiring "
                             "or sensor fault?")
        else:
            valid.append((r["date"], ratio))
            if ref is None and len(valid) >= config.WX_BASELINE_DAYS:
                ref = float(np.median([v for _, v in valid[:config.WX_BASELINE_DAYS]]))
            roll = float(np.median([v for _, v in valid[-config.WX_DRIFT_DAYS:]]))
            r["ratio_rolling"] = roll
            if ref is None:
                r.update(status="BASELINE",
                         message=f"building the reference: {len(valid)} of "
                                 f"{config.WX_BASELINE_DAYS} valid days")
            else:
                drift = roll / ref - 1.0
                r.update(reference_ratio=ref, drift_pct=100 * drift)
                tail = valid[-config.WX_TREND_DAYS:]
                if len(tail) >= 5:
                    x = (pd.to_datetime([d for d, _ in tail]) - pd.Timestamp(tail[0][0])).days
                    slope = np.polyfit(np.asarray(x, float), [v for _, v in tail], 1)[0]
                    r["trend_pct_per_week"] = 100 * 7 * slope / ref
                if abs(drift) > band:
                    why = ("a dirty dome, shading, a tilt/level change or a sensor fault"
                           if drift < 0 else "a tilt/level change, a reflection or a sensor "
                                             "fault")
                    r.update(status="DRIFT",
                             message=f"PYR20/model {roll:.3f} is {100 * drift:+.1f} % from the "
                                     f"reference {ref:.3f} (band +/-{100 * band:g} %): likely "
                                     f"{why}. Inspect the PYR20.")
                else:
                    r.update(status="OK", message=f"PYR20/model {roll:.3f}, {100 * drift:+.1f} % "
                                                  f"from the reference {ref:.3f}")
        out.append(r)
    res = pd.DataFrame(out)
    if not len(res):
        return pd.DataFrame(columns=CHECK_COLUMNS)
    return res[CHECK_COLUMNS]


def run(measured, model_rows, site, dates=None, source=None, reference=None):
    """measured + model rows -> (daily check table, hourly table)."""
    h = hours(measured, model_rows, site, source)
    return assess(daily(h, dates, source), site, reference), h
