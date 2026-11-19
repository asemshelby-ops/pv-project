"""Which Open-Meteo endpoint covers which days, and one hourly table per site.

Two feeds, both MODEL data:
  irradiance   config.WX_CHECK_SOURCE ("satellite" by default) for past days and today;
               the forecast covers the days ahead (a satellite cannot)
  weather      temperature, wind, cloud cover (and the model's own irradiance): the forecast
               endpoint for days from FORECAST_PAST_LIMIT_DAYS ago to FORECAST_MAX_DAYS ahead,
               the archive endpoint for older days
Each request is split where the tilt schedule changes, so the in-plane model irradiance
always uses the tilt the module had that day.
"""
import datetime

import numpy as np
import pandas as pd

import config
from wxref import PREFIX
from wxref.openmeteo import MODEL_COLUMNS, ROW_COLUMNS

FORECAST_PAST_LIMIT_DAYS = 90     # docs: past_days up to 92 on the forecast endpoint; margin 2
FORECAST_MAX_DAYS = 15            # docs: forecast_days up to 16 (today + 15)
RADIATION_COLUMNS = [PREFIX + "ghi_wm2", PREFIX + "poa_wm2", PREFIX + "ghi_clearsky_wm2"]
MET_COLUMNS = [c for c in MODEL_COLUMNS if c not in RADIATION_COLUMNS]
HOURLY_COLUMNS = (["t_utc"] + MODEL_COLUMNS
                  + [PREFIX + "tilt_deg", PREFIX + "irr_source", PREFIX + "met_source"])


def _d(x):
    return x if isinstance(x, datetime.date) else datetime.date.fromisoformat(str(x))


def tilt_periods(site, start, end):
    """[(start, end, tilt)] covering local dates start..end, split at tilt changes."""
    start, end = _d(start), _d(end)
    out, s = [], start
    for d, _ in site.tilt_schedule:
        d = _d(d)
        if s < d <= end:
            out.append((s, d - datetime.timedelta(days=1), site.tilt_on(s)))
            s = d
    out.append((s, end, site.tilt_on(s)))
    return out


def segments(start, end, today, irr_source=None):
    """[(source, start, end)] needed for local dates start..end, seen from `today`."""
    start, end, today = _d(start), _d(end), _d(today)
    irr_source = irr_source or config.WX_CHECK_SOURCE
    end = min(end, today + datetime.timedelta(days=FORECAST_MAX_DAYS))
    if start > end:
        return []
    out = []
    cut = today - datetime.timedelta(days=FORECAST_PAST_LIMIT_DAYS)
    if start < cut:
        out.append(("archive", start, min(end, cut - datetime.timedelta(days=1))))
    if end >= cut:
        out.append(("forecast", max(start, cut), end))
    if irr_source == "satellite" and start <= today:
        out.append(("satellite", start, min(end, today)))
    return out


def fetch(client, site, start, end, today, irr_source=None):
    """All MODEL rows for local dates start..end. Returns (rows, [error texts]); rows may be
    empty (offline, nothing cached). Never raises for a network problem."""
    parts, errors = [], []
    for source, s, e in segments(start, end, today, irr_source):
        for ps, pe, tilt in tilt_periods(site, s, e):
            rows, err = client.get(source, site, ps.isoformat(), pe.isoformat(), tilt)
            if err:
                errors.append(f"{source} {ps}..{pe}: {err}")
            if len(rows):
                parts.append(rows)
    rows = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=ROW_COLUMNS)
    rows = rows.drop_duplicates(["wx_source", "t_utc"], keep="last")
    return rows.sort_values(["t_utc", "wx_source"]).reset_index(drop=True), errors


def hourly(rows, irr_source=None):
    """One row per hour (t_utc, Unix s) from the rows of all sources. Irradiance from
    WX_CHECK_SOURCE where it has a value, else the forecast, else the archive; weather
    (temperature, wind, cloud) from the forecast, else the archive. The two *_source columns
    say which endpoint each value came from."""
    irr_source = irr_source or config.WX_CHECK_SOURCE
    if rows is None or not len(rows):
        return pd.DataFrame(columns=HOURLY_COLUMNS)
    times = np.sort(rows["t_utc"].unique())
    out = pd.DataFrame({"t_utc": times})
    by = {s: g.set_index("t_utc") for s, g in rows.groupby("wx_source")}
    out[PREFIX + "irr_source"] = None
    out[PREFIX + "met_source"] = None
    for c in MODEL_COLUMNS + [PREFIX + "tilt_deg"]:
        out[c] = np.nan
    order_irr = [irr_source] + [s for s in ("forecast", "archive") if s != irr_source]
    for src_col, cols, order in ((PREFIX + "irr_source", RADIATION_COLUMNS, order_irr),
                                 (PREFIX + "met_source", MET_COLUMNS, ["forecast", "archive"])):
        for s in reversed(order):                    # the first in `order` is applied last
            if s not in by:
                continue
            g = by[s].reindex(times)
            key = PREFIX + ("poa_wm2" if cols is RADIATION_COLUMNS else "wind_speed_10m_ms")
            has = g[key].notna().to_numpy()
            for c in cols:
                out.loc[has, c] = g[c].to_numpy()[has]
            if cols is RADIATION_COLUMNS:
                out.loc[has, PREFIX + "tilt_deg"] = g[PREFIX + "tilt_deg"].to_numpy()[has]
            out.loc[has, src_col] = s
    return out[HOURLY_COLUMNS]
