"""Soiling (dust) housekeeping checks. NOT a research result: two small checks that tell
whether dust may have disturbed a day (docs/paper/05 sec. 9).

  dust_days()       local days flagged because a "dust storm" line is on the log sheet:
                    the day of the line and every day after it up to and including the day
                    of the next logged cleaning (its morning was still dusty).
  cleaning_check()  for every logged cleaning: power per unit irradiance around solar
                    noon on the last day before it and on the first day after it.

Limits of the check, to keep in mind when reading it:
  * the module and the PYR20 dome are cleaned together, so P / G shows the dust on the
    module MINUS the dust on the dome; equal dust on both cancels;
  * it is not corrected for temperature (gamma of this module is not known yet): the
    column dT_module_C tells how different the two middays were;
  * it needs the load: without it the logged power is ~0 and the check is "no power".
"""
import numpy as np
import pandas as pd

import config
from data.schema import BACK_SENSORS


def dust_days(joined):
    """joined: markers joined to the log sheets (ingest.join_logsheets). Returns a table
    date (local, text), dust_line (time of the "dust storm" line that flags it)."""
    rows = []
    cleanings = joined[joined["event"] == "cleaning end"]["t_local"].sort_values()
    last = joined["t_local"].max()
    for t in joined[joined["event"] == "dust storm"]["t_local"].sort_values():
        later = cleanings[cleanings > t]
        end = later.iloc[0] if len(later) else last      # never cleaned since: to the end
        for d in pd.date_range(t.normalize(), end.normalize(), freq="D"):
            rows.append({"date": d.strftime("%Y-%m-%d"), "dust_line": t})
    out = pd.DataFrame(rows, columns=["date", "dust_line"])
    return out.drop_duplicates("date").reset_index(drop=True)


def _midday(df, date):
    """Median P / G, median module temperature and row count in the midday window of one
    local day, from rows with enough sun and real power (load holding)."""
    w = df[(df["date"] == date) & (df["time"] >= config.SOIL_NOON_WINDOW[0])
           & (df["time"] < config.SOIL_NOON_WINDOW[1])]
    ok = (w["irradiance"] >= config.SOIL_MIN_G_WM2) & (w["power"] > config.SOIL_MIN_POWER_W)
    if "load_mode" in w:                                 # not while off, tripped or sweeping
        ok &= ~w["load_mode"].isin(["off", "trip", "sweep"])
    w = w[ok]
    if len(w) < config.SOIL_MIN_ROWS:
        return np.nan, np.nan, len(w)
    return (float((w["power"] / w["irradiance"]).median()),
            float(w[BACK_SENSORS].mean(axis=1).median()), len(w))


def cleaning_check(df, joined):
    """One line per logged cleaning (a "cleaning start" line). df: ingest.read_logger().
    change = (P/G after) / (P/G before) - 1; `note` says "check" when its size exceeds
    config.SOIL_CHECK_REL, or why it could not be computed."""
    dates = sorted(df["date"].unique())
    noon_end = pd.Timedelta(config.SOIL_NOON_WINDOW[1] + ":00")
    noon_start = pd.Timedelta(config.SOIL_NOON_WINDOW[0] + ":00")
    dusty = set(dust_days(joined)["date"])
    rows = []
    for t in joined[joined["event"] == "cleaning start"]["t_local"].sort_values():
        day = t.normalize()
        # last day whose midday window ended before the cleaning; first whose began after it
        before = [d for d in dates if pd.Timestamp(d) + noon_end <= t]
        after = [d for d in dates if pd.Timestamp(d) + noon_start >= t]
        row = {"cleaning": t, "date": day.strftime("%Y-%m-%d"),
               "after_dust": day.strftime("%Y-%m-%d") in dusty,
               "day_before": before[-1] if before else "", "day_after": after[0] if after else ""}
        if not before or not after:
            rows.append({**row, "note": "no day before or after in the data"})
            continue
        r0, t0, n0 = _midday(df, before[-1])
        r1, t1, n1 = _midday(df, after[0])
        row.update({"rows_before": n0, "rows_after": n1, "P_per_G_before": r0,
                    "P_per_G_after": r1, "dT_module_C": t1 - t0})
        if np.isnan(r0) or np.isnan(r1):
            row["note"] = "no power or not enough sun at midday (load off, cloud, night)"
        else:
            row["change"] = r1 / r0 - 1.0
            row["note"] = "check" if abs(row["change"]) > config.SOIL_CHECK_REL else ""
        rows.append(row)
    cols = ["cleaning", "date", "after_dust", "day_before", "day_after", "rows_before",
            "rows_after", "P_per_G_before", "P_per_G_after", "change", "dT_module_C", "note"]
    return pd.DataFrame(rows, columns=cols)
