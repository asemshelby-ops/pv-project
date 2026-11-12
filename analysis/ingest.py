"""Read the ESP32 logger's files into tables the analysis code can use.

The logger writes UTC (firmware/logger). Everything downstream works in Makkah local
time, because the detector features (solar geometry) and the day split are local:
  * `date`, `time`      rewritten to LOCAL time (the schema the detect/ code expects)
  * `date_utc`, `time_utc`, `t_utc`   the logger's own timestamps, kept
  * `t_local`           naive local timestamp
  * `segment`           id of an unbroken run of complete in-window rows (>= 0), else -1.
                        A new segment starts at a new local day, after a gap above
                        config.GAP_S, or after a row with a missing detector input --
                        the same moments at which the firmware resets NASIM.
Raw values are never changed, except for optional per-sensor calibration offsets.
"""
import pathlib

import numpy as np
import pandas as pd

import config
from data.schema import BACK_SENSORS

REQUIRED = BACK_SENSORS + ["T_ambient", "irradiance"]     # inputs of every detector
DAY_FILE = "[0-9]" * 8 + ".csv"


def read_logger(src, calib=None):
    """src: one logger CSV or a folder of /YYYYMMDD.csv files.
    calib: optional {"T1": offset_C, ...}; the offset is SUBTRACTED from the reading."""
    src = pathlib.Path(src)
    files = sorted(src.glob(DAY_FILE)) if src.is_dir() else [src]
    if not files:
        raise FileNotFoundError(f"no logger files in {src}")
    raw = pd.concat([pd.read_csv(f, comment="#", dtype=str, skip_blank_lines=True)
                     for f in files], ignore_index=True)
    raw = raw[raw["date"].notna() & raw["time"].notna() & (raw["date"] != "date")]
    t_utc = pd.to_datetime(raw["date"] + " " + raw["time"], utc=True, errors="coerce")
    df = raw.assign(t_utc=t_utc)[t_utc.notna()]
    df = df.drop_duplicates("t_utc").sort_values("t_utc").reset_index(drop=True)
    for c in df.columns.difference(["date", "time", "t_utc"]):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for name, off in (calib or {}).items():
        df[name] = df[name] - off

    local = df["t_utc"].dt.tz_convert(config.TZ)
    df["date_utc"], df["time_utc"] = df["date"], df["time"]
    df["date"], df["time"] = local.dt.strftime("%Y-%m-%d"), local.dt.strftime("%H:%M:%S")
    df["t_local"] = local.dt.tz_localize(None)

    in_window = (df["time"] >= config.DAY_START) & (df["time"] < config.DAY_END)
    use = in_window & df[REQUIRED].notna().all(axis=1)
    step = df["t_utc"].diff().dt.total_seconds()
    new = use & (~use.shift(fill_value=False) | (step > config.GAP_S)
                 | (df["date"] != df["date"].shift()))
    df["segment"] = np.where(use, new.cumsum() - 1, -1)
    return df


def detector_frame(df):
    """Only the rows a detector may see, in order."""
    return df[df["segment"] >= 0].reset_index(drop=True)


def day_quality(df):
    """Per local day: rows in the day window, share of the expected rows, usable rows,
    number of segments (1 = no interruption)."""
    w = df[(df["time"] >= config.DAY_START) & (df["time"] < config.DAY_END)]
    start, end = (pd.Timedelta(x) for x in (config.DAY_START, config.DAY_END))
    expected = int((end - start).total_seconds() / config.DT_S)
    g = w.groupby("date")
    q = pd.DataFrame({"rows": g.size(),
                      "usable_rows": g["segment"].apply(lambda s: int((s >= 0).sum())),
                      "segments": g["segment"].apply(lambda s: s[s >= 0].nunique())})
    q["completeness"] = q["rows"] / expected
    return q.reset_index()


# --- markers and the daily log sheet ----------------------------------------------------

def read_markers(path):
    """/markers.csv (date,time,millis; UTC with milliseconds). Returns (table, number of
    presses that had no RTC time). `marker` numbers the presses of each LOCAL day from 1."""
    m = pd.read_csv(path, dtype=str)
    t = pd.to_datetime(m["date"].fillna("") + " " + m["time"].fillna(""), utc=True,
                       errors="coerce")
    no_time = int(t.isna().sum())
    m = m.assign(t_utc=t)[t.notna()].sort_values("t_utc").reset_index(drop=True)
    local = m["t_utc"].dt.tz_convert(config.TZ)
    m["t_local"] = local.dt.tz_localize(None)
    m["date_local"] = local.dt.strftime("%Y-%m-%d")
    m["marker"] = m.groupby("date_local").cumcount() + 1
    return m[["t_utc", "t_local", "date_local", "marker"]], no_time


def join_logsheets(markers, sheet_dir):
    """Attach `event` and `notes` from <sheet_dir>/YYYY-MM-DD.csv (columns
    marker,local_time,event,notes). Returns (table, problems); a day whose sheet is missing
    or whose number of lines differs from the number of presses is reported, not guessed."""
    sheet_dir, parts, problems = pathlib.Path(sheet_dir), [], []
    for date, g in markers.groupby("date_local"):
        f = sheet_dir / f"{date}.csv"
        if not f.exists():
            problems.append(f"{date}: {len(g)} marker presses but no log sheet")
            parts.append(g.assign(event=np.nan, notes=np.nan))
            continue
        sheet = pd.read_csv(f, dtype={"event": str, "notes": str})
        if len(sheet) != len(g):
            problems.append(f"{date}: {len(g)} presses, {len(sheet)} log-sheet lines")
        parts.append(g.merge(sheet[["marker", "event", "notes"]], on="marker", how="left"))
    out = pd.concat(parts, ignore_index=True) if parts else markers.assign(event=[], notes=[])
    out["event"] = (out["event"].astype("string").str.strip().str.lower()
                    .fillna("").astype(object))
    return out, problems


def _pairs(m, first, second):
    """(start, end) for each `first` marker and the next `second` marker of the same day."""
    out = []
    for _, g in m.groupby("date_local"):
        ends = g[g["event"] == second]["t_local"].tolist()
        for t0 in g[g["event"] == first]["t_local"]:
            later = [t for t in ends if t > t0]
            out.append((t0, later[0] if later else None))
    return out


def excluded_windows(m):
    """Periods left out of spike statistics and detector scoring (docs/paper/03 sec. 4)."""
    pad = pd.Timedelta(minutes=config.CLEANING_EXCLUDE_MIN)
    rows = [(a, (b or a + pd.Timedelta(minutes=15)) + pad, "cleaning")
            for a, b in _pairs(m, "cleaning start", "cleaning end")]
    for a, b in _pairs(m, "gamma shade on", "gamma end"):
        rows.append((a, (b or a + pd.Timedelta(minutes=45)) + pad, "gamma run"))
    rows += [(t, t + pad, "maintenance") for t in m[m["event"] == "maintenance"]["t_local"]]
    return pd.DataFrame(rows, columns=["start", "end", "reason"])


def controlled_tests(m):
    """Controlled spike tests: the rise is expected after `mesh off` and during a windbreak."""
    rows = [(a, b, "ctl_mesh") for a, b in _pairs(m, "mesh on", "mesh off") if b is not None]
    rows += [(a, b, "ctl_windbreak")
             for a, b in _pairs(m, "windbreak on", "windbreak off") if b is not None]
    rows += [(t, t, "negative_control")
             for t in m[m["event"] == "negative control"]["t_local"]]
    return pd.DataFrame(rows, columns=["on", "off", "origin"])
