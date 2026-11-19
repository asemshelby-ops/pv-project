"""MODEL columns joined to logger rows by timestamp, for analysis and the twin.

The added columns all start with "wx_model_" and never replace a column of ours: the
logger's `wind` (NaN while there is no anemometer) stays as it is, so no detector -- on the
board or offline -- ever sees model wind (tests/test_wxref_join.py).

  instant variables (temperature, wind speed, cloud cover): linear in time between the
      hourly stamps on both sides of the row; NaN if either side is missing
  wind direction: interpolated as a unit vector, so 350 and 10 deg give 0, not 180
  radiation (hour means): the value of the hour the row falls in, i.e. the stamp t with
      t - 1 h < row time <= t
"""
import numpy as np
import pandas as pd

from wxref import PREFIX
from wxref.check import _seconds

INSTANT = [PREFIX + "temp_2m_c", PREFIX + "wind_speed_10m_ms", PREFIX + "cloud_cover_pct"]
DIRECTION = PREFIX + "wind_dir_10m_deg"
HOUR_MEAN = [PREFIX + "ghi_wm2", PREFIX + "poa_wm2"]
DEFAULT = INSTANT + [DIRECTION] + HOUR_MEAN


def _interp(t, ht, hv):
    """Linear interpolation at t between neighbouring hourly stamps; NaN when a neighbour is
    missing or more than an hour away."""
    ok = np.isfinite(hv)
    out = np.full(len(t), np.nan)
    if ok.sum() < 1:
        return out
    k = np.searchsorted(ht, t, side="right")          # ht[k-1] <= t < ht[k]
    lo, hi = np.clip(k - 1, 0, len(ht) - 1), np.clip(k, 0, len(ht) - 1)
    exact = (k - 1 >= 0) & (ht[lo] == t)
    inside = (k - 1 >= 0) & (k < len(ht)) & (ht[hi] - ht[lo] <= 3600)
    w = np.where(inside, (t - ht[lo]) / np.maximum(ht[hi] - ht[lo], 1), 0.0)
    val = hv[lo] * (1 - w) + hv[hi] * w
    out[inside] = val[inside]
    out[exact] = hv[lo][exact]
    return out


def add_model_columns(df, hourly_table, columns=DEFAULT):
    """A copy of `df` (it needs t_utc: Unix s or UTC datetimes) with the model columns added.
    `hourly_table`: wxref.feeds.hourly(). Rows outside the model's hours get NaN."""
    bad = [c for c in columns if not c.startswith(PREFIX)]
    if bad:
        raise ValueError(f"only model columns ({PREFIX}*) can be joined, not {bad}")
    out = df.copy()
    t = _seconds(df["t_utc"]).astype(float)
    h = hourly_table.sort_values("t_utc")
    ht = h["t_utc"].to_numpy(float)
    for c in columns:
        hv = pd.to_numeric(h[c], errors="coerce").to_numpy(float) if c in h else \
            np.full(len(h), np.nan)
        if not len(ht):
            out[c] = np.nan
        elif c in HOUR_MEAN:
            stamp = np.ceil(t / 3600.0) * 3600.0
            out[c] = pd.Series(hv, index=ht).reindex(stamp).to_numpy()
        elif c == DIRECTION:
            rad = np.deg2rad(hv)
            u, v = _interp(t, ht, np.sin(rad)), _interp(t, ht, np.cos(rad))
            out[c] = np.round(np.rad2deg(np.arctan2(u, v)) % 360.0, 1)
        else:
            out[c] = _interp(t, ht, hv)
    return out
