"""The weather reference for offline analysis (analysis/powerbi.py, analysis/pipeline.py,
twin/validate.py). Used only when config.WX_ENABLED is on (or a --weather flag asks).

model_for(df) gathers the MODEL rows for the local days of a logger frame -- from the disk
cache, from <WX_DIR>/wx_model.csv (what the SCADA already fetched) and, if online, from
Open-Meteo -- and runs the daily PYR20 check on them. Offline it warns once and returns
what it has (possibly nothing); the analysis then carries on with empty model columns.
"""
import time
import warnings

import pandas as pd

import config
from wxref import PREFIX, check, feeds, join, local_today, site_from_config, store
from wxref.check import _local_dates, _seconds
from wxref.openmeteo import ROW_COLUMNS, Client

DAILY_COLUMNS = {  # daily_summary column -> source
    PREFIX + "poa_kWh_m2": "model in-plane insolation (sum of the 24 hourly means / 1000)",
    PREFIX + "ghi_kWh_m2": "model horizontal insolation",
    PREFIX + "mean_wind_10m_ms": "mean model wind at 10 m, 06-18 local",
    PREFIX + "max_temp_2m_C": "max model air temperature at 2 m",
    "pyr20_check_status": "daily PYR20 check status (wxref/check.py)",
    "pyr20_model_ratio": "PYR20 / model in-plane, median of the clear-sky hours",
    "pyr20_ratio_rolling": "rolling median of the daily ratios",
    "pyr20_reference_ratio": "reference ratio of the check",
    "pyr20_drift_pct": "rolling ratio vs reference, %",
    "pyr20_clear_hours": "clear-sky hours used",
}


def model_for(df, site=None, client=None, now=None, fetch=True, log=print):
    """{rows, hourly, checks, hours, errors, site} for the local days of `df` (t_utc +
    irradiance). Never raises for a network problem."""
    site = site if site is not None else site_from_config()
    empty_rows = pd.DataFrame(columns=ROW_COLUMNS)
    t = _seconds(df["t_utc"])
    dates = sorted(set(_local_dates(t))) if len(t) else []
    out = {"rows": empty_rows, "hourly": feeds.hourly(empty_rows),
           "checks": pd.DataFrame(columns=check.CHECK_COLUMNS),
           "hours": pd.DataFrame(columns=check.HOUR_COLUMNS), "errors": [], "site": site}
    if site is None:
        msg = ("weather reference: config.WX_SITE_LAT / WX_SITE_LON not set, no model data; "
               "the model columns stay empty")
        warnings.warn(msg, stacklevel=2)
        out["errors"].append(msg)
        return out
    if not dates:
        return out
    now = time.time() if now is None else now
    parts = [store.read_model_csv()]
    if fetch:
        client = client or Client(clock=lambda: now, log=log)
        rows, errors = feeds.fetch(client, site, dates[0], dates[-1], local_today(now))
        parts.append(rows)
        if errors:
            warnings.warn("weather reference: " + "; ".join(errors[:3])
                          + " -- carrying on with the model data at hand", stacklevel=2)
        out["errors"] += errors
    parts = [p for p in parts if len(p)]
    if not parts:
        return out
    rows = pd.concat(parts, ignore_index=True).drop_duplicates(["wx_source", "t_utc"],
                                                               keep="last")
    lo, hi = int(t.min()) - 3600, int(t.max()) + 3600
    rows = rows[(rows["t_utc"] >= lo) & (rows["t_utc"] <= hi)]
    rows = rows.sort_values(["t_utc", "wx_source"]).reset_index(drop=True)
    checks, hours = check.run(df[["t_utc", "irradiance"]], rows, site, dates=dates)
    out.update(rows=rows, hourly=feeds.hourly(rows), checks=checks, hours=hours)
    return out


def hourly_table(wx, df, source_label):
    """Power BI / pipeline table: one row per model hour, with the PYR20 and ambient means of
    the same hour (the hour ENDING at time_local)."""
    h = wx["hourly"]
    cols = ["source", "date", "time_local"] + [c for c in h.columns if c != "t_utc"] + [
        "measured_poa_wm2", "measured_T_ambient_C"]
    if not len(h):
        return pd.DataFrame(columns=cols)
    t = _seconds(df["t_utc"])
    stamp = -(-t // 3600) * 3600
    m = pd.DataFrame({"h": stamp, "g": pd.to_numeric(df["irradiance"], errors="coerce").to_numpy(),
                      "ta": pd.to_numeric(df["T_ambient"], errors="coerce").to_numpy()
                      if "T_ambient" in df else float("nan")}).groupby("h").mean()
    local = pd.to_datetime(h["t_utc"], unit="s", utc=True).dt.tz_convert(config.TZ)
    out = h.drop(columns="t_utc").copy()
    out.insert(0, "time_local", local.dt.strftime("%Y-%m-%d %H:%M:%S").to_numpy())
    out.insert(0, "date", (local - pd.Timedelta(minutes=30)).dt.strftime("%Y-%m-%d").to_numpy())
    out.insert(0, "source", source_label)
    out["measured_poa_wm2"] = m["g"].reindex(h["t_utc"]).round(1).to_numpy()
    out["measured_T_ambient_C"] = m["ta"].reindex(h["t_utc"]).round(2).to_numpy()
    return out[cols]


def daily_columns(wx, dates):
    """The optional daily_summary columns (DAILY_COLUMNS) for local `dates`."""
    h = wx["hourly"]
    out = pd.DataFrame({"date": list(dates)})
    if len(h):
        local = pd.to_datetime(h["t_utc"], unit="s", utc=True).dt.tz_convert(config.TZ)
        day = (local - pd.Timedelta(minutes=30)).dt.strftime("%Y-%m-%d")
        g = h.assign(date=day.to_numpy(), hour=local.dt.hour.to_numpy())
        by = g.groupby("date")
        # a daily value only from a complete model day (24 hours; 10 of the 13 day hours for
        # the wind): a part day would look like a dark, calm or cool day
        full = by[PREFIX + "poa_wm2"].count() == 24
        for col, src in ((PREFIX + "poa_kWh_m2", PREFIX + "poa_wm2"),
                         (PREFIX + "ghi_kWh_m2", PREFIX + "ghi_wm2")):
            out[col] = out["date"].map((by[src].sum() / 1000).where(full)).round(3)
        day_hours = g[(g["hour"] >= 6) & (g["hour"] <= 18)].groupby("date")
        w = day_hours[PREFIX + "wind_speed_10m_ms"]
        out[PREFIX + "mean_wind_10m_ms"] = out["date"].map(w.mean().where(w.count() >= 10)).round(2)
        ta = by[PREFIX + "temp_2m_c"]
        out[PREFIX + "max_temp_2m_C"] = out["date"].map(ta.max().where(ta.count() >= 20)).round(1)
    else:
        for c in list(DAILY_COLUMNS)[:4]:
            out[c] = float("nan")
    c = wx["checks"].set_index("date") if len(wx["checks"]) else None
    for col, src, nd in (("pyr20_check_status", "status", None),
                         ("pyr20_model_ratio", "pyr20_model_ratio", 4),
                         ("pyr20_ratio_rolling", "ratio_rolling", 4),
                         ("pyr20_reference_ratio", "reference_ratio", 4),
                         ("pyr20_drift_pct", "drift_pct", 2),
                         ("pyr20_clear_hours", "clear_hours", 0)):
        v = out["date"].map(c[src]) if c is not None else pd.Series([None] * len(out))
        out[col] = v.astype(float).round(nd) if nd is not None else v
    return out


def summary_lines(wx):
    """Markdown lines for analysis/pipeline.py's summary.md."""
    lines = ["## Weather reference - Open-Meteo MODEL data, not measurements", "",
             "Optional section (config.WX_ENABLED). Model values come from weather models and "
             "satellite retrievals (Open-Meteo.com, CC BY 4.0); the PYR20 check compares them "
             "with our measurement on clear-sky hours only. Files: wx_model_hourly.csv, "
             "wx_daily_check.csv. None of it enters the detectors.", ""]
    if wx["errors"]:
        lines += [f"- {e}" for e in wx["errors"][:5]] + [""]
    c = wx["checks"]
    if not len(c):
        return lines + ["No daily check: no model data for these days.", ""]
    n = c["status"].value_counts()
    lines.append("Daily PYR20 check: " + ", ".join(f"{k} {v}" for k, v in n.items()) + ".")
    flagged = c[c["status"].isin(check.FLAGS)]
    if len(flagged):
        lines.append("Flagged days: " + ", ".join(f"{r.date} ({r.status}, ratio "
                                                  f"{r.pyr20_model_ratio:.3f})"
                                                  for r in flagged.itertuples()))
    return lines + [""]


def frame_with_model(frame, wx):
    """The analysis frame with the model columns joined by time (wxref.join). For analysis
    and the twin only: the detectors are always given the frame WITHOUT them."""
    return join.add_model_columns(frame, wx["hourly"])

