"""Read the ESP32 logger's files into tables the analysis code can use.

The logger writes UTC (firmware/logger). Everything downstream works in Makkah local
time, because the detector features (solar geometry) and the day split are local:
  * `date`, `time`      rewritten to LOCAL time (the schema the detect/ code expects)
  * `date_utc`, `time_utc`, `t_utc`   the logger's own timestamps, kept
  * `t_local`           naive local timestamp
  * `segment`           id of the detector run a usable row belongs to (>= 0), else -1.
                        Usable = in the day window, every detector input present, time
                        from the RTC. The rule is the board's (firmware/logger): a row
                        that is not usable, or absent from the file, is skipped and the
                        run goes on; config.DET_RESET_AFTER_FAILS (5) such rows in a row
                        start a new segment, i.e. up to 4 are bridged. A new local day
                        or a restart of the board (uptime_ms falls) also starts one.
                        Skipped rows stay at -1, so inside a segment two rows can be up
                        to 50 s apart: the detectors count samples, as on the board.
  * `D`, `load_mode`, `sweep_id`, `uptime_ms`, `status`   the five columns the logger
                        appends since 2026-11-12 (docs/paper/07 sec. 7). Files written
                        before that load too; the columns are then empty (NaN / <NA>).
                        Decode `status` with data.schema.decode_status / has_status.
  * `time_source`       "rtc", or "uptime" for a row the logger wrote without RTC time:
                        its time is rebuilt from `uptime_ms` and the nearest row of the
                        same boot that has a time (good to about 1 s, the RTC's step).
  * `csv_version`       format of the block the row was read from: 2 = the logger since
                        2026-11-21 (its blocks start with "# nasim-logger-csv v2"), 1 = the
                        21 columns written from 2026-11-12, 0 = the 16 columns before.
  * `T6`, `T7`, `pyr_mV`, `sens_fail`, `f_T1` ... `f_sd`, `ring_rows`, `ring_dropped`,
    `cycle_ms`          the columns CSV v2 appends (data/schema.py LOGGER_V2_COLUMNS;
                        docs/paper/13-logger-robustness.md). Empty in older files. Decode
                        `sens_fail` with data.schema.decode_sensors / has_sensor_fail.
Rows whose time cannot be rebuilt (no uptime_ms, or no timed row in the same boot and
file, as in /nodate.csv) cannot be placed on the time axis: they are left out and
COUNTED in `df.attrs["rows_without_time"]`, with a warning. `df.attrs["rows_time_rebuilt"]`
counts the rebuilt ones.
A file is read as a sequence of BLOCKS. A block starts at a header line (`date,time,...`),
with the version line before it if there is one; the logger writes such a block at the
top of a new file and again with the first rows after every boot, so one file can hold
blocks of different formats (a firmware update in the middle of a day). A line that does
not have as many fields as its block's header is not a row (a write cut off by a power
loss, a line damaged on the card): it is left out and COUNTED in
`df.attrs["rows_malformed"]`, with a warning.
Raw values are never changed, except for optional per-sensor calibration offsets.
"""
import io
import pathlib
import warnings

import numpy as np
import pandas as pd

import config
from data.schema import (BACK_SENSORS, FAIL_COUNTER_COLUMNS, LOGGER_EXTRA_COLUMNS,
                         LOGGER_V2_COLUMNS, LOGGER_VERSION_PREFIX)

REQUIRED = BACK_SENSORS + ["T_ambient", "irradiance"]     # inputs of every detector
DAY_FILE = "[0-9]" * 8 + ".csv"


def read_logger(src, calib=None):
    """src: one logger CSV or a folder of /YYYYMMDD.csv files.
    calib: optional {"T1": offset_C, ...}; the offset is SUBTRACTED from the reading."""
    src = pathlib.Path(src)
    files = sorted(src.glob(DAY_FILE)) if src.is_dir() else [src]
    if not files:
        raise FileNotFoundError(f"no logger files in {src}")
    if src.is_dir() and (src / "nodate.csv").exists():
        files.append(src / "nodate.csv")           # rows of boots that never had RTC time
    blocks, malformed = [], 0
    for k, f in enumerate(files):
        tables, bad = _read_blocks(f)
        blocks += [t.assign(_file=k) for t in tables]
        malformed += bad
    if not blocks:
        raise ValueError(f"no rows in the logger files of {src}")
    if malformed:
        warnings.warn(f"{malformed} lines in the logger files are not complete rows and are "
                      f"left out (see df.attrs['rows_malformed'])", stacklevel=2)
    raw = pd.concat(blocks, ignore_index=True)
    for c in LOGGER_EXTRA_COLUMNS + LOGGER_V2_COLUMNS:   # blocks older than the column
        if c not in raw:
            raw[c] = np.nan
    t_utc, source = _row_times(raw)
    lost = int(t_utc.isna().sum())
    if lost:
        warnings.warn(f"{lost} logger rows have no usable time and are left out "
                      f"(see df.attrs['rows_without_time'])", stacklevel=2)
    df = raw.assign(t_utc=t_utc, time_source=source)[t_utc.notna()].drop(columns="_file")
    df = df.drop_duplicates("t_utc").sort_values("t_utc").reset_index(drop=True)
    for c in df.columns.difference(["date", "time", "t_utc", "time_source", "load_mode"]):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("sweep_id", "uptime_ms", "status", "sens_fail", *FAIL_COUNTER_COLUMNS,
              "ring_rows", "ring_dropped", "cycle_ms"):
        df[c] = df[c].astype("Int64")              # whole numbers, <NA> where not recorded
    df["csv_version"] = df["csv_version"].astype("int64")
    for name, off in (calib or {}).items():
        df[name] = df[name] - off
    df.attrs["rows_without_time"] = lost
    df.attrs["rows_time_rebuilt"] = int((df["time_source"] == "uptime").sum())
    df.attrs["rows_malformed"] = malformed

    local = df["t_utc"].dt.tz_convert(config.TZ)
    df["date_utc"] = df["t_utc"].dt.strftime("%Y-%m-%d")
    df["time_utc"] = df["t_utc"].dt.strftime("%H:%M:%S")
    df["date"], df["time"] = local.dt.strftime("%Y-%m-%d"), local.dt.strftime("%H:%M:%S")
    df["t_local"] = local.dt.tz_localize(None)

    in_window = (df["time"] >= config.DAY_START) & (df["time"] < config.DAY_END)
    use = in_window & df[REQUIRED].notna().all(axis=1) & (df["time_source"] == "rtc")
    u = df[use]
    # rows the detector did not get between two usable rows: unusable ones and absent ones
    skipped = (u["t_utc"].diff().dt.total_seconds() / config.DT_S).round() - 1
    new = (skipped.isna() | (skipped >= config.DET_RESET_AFTER_FAILS)
           | (u["date"] != u["date"].shift())
           | (u["uptime_ms"].astype("float64").diff() < 0))
    df["segment"] = -1
    df.loc[use, "segment"] = new.cumsum() - 1
    return df


def _read_blocks(path):
    """One logger file -> ([table of text cells per block], number of malformed lines).
    Each table has the columns of its own header and `csv_version`."""
    tables, bad = [], 0
    header, version, announced, lines = None, 0, None, []

    def close():
        if header and lines:
            text = "\n".join([",".join(header)] + lines)
            tables.append(pd.read_csv(io.StringIO(text), dtype=str).assign(csv_version=version))

    for line in pathlib.Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith(LOGGER_VERSION_PREFIX):
            digits = line[len(LOGGER_VERSION_PREFIX):].split()
            announced = int(digits[0]) if digits and digits[0].isdigit() else None
        elif not line or line.startswith("#"):
            continue
        elif line.startswith("date,"):                 # a header line: a new block starts
            close()
            header, lines = line.split(","), []
            version = announced if announced is not None else int("status" in header)
            announced = None
        elif header is None or line.count(",") != len(header) - 1:
            bad += 1                                   # cut-off or damaged line: not a row
        else:
            lines.append(line)
    close()
    return tables, bad


def _row_times(raw):
    """UTC time of every row and where it comes from ("rtc" / "uptime" / "none").
    A row without RTC time takes it from uptime_ms: within one boot (uptime_ms rising, in
    one file) `time - uptime` is constant, so it is copied from the nearest earlier row
    that has both, else the nearest later one."""
    rtc = pd.to_datetime(raw["date"].fillna("") + " " + raw["time"].fillna(""), utc=True,
                         errors="coerce")
    up = pd.to_numeric(raw["uptime_ms"], errors="coerce")
    boot = (up.ffill().diff() < 0).groupby(raw["_file"]).cumsum()   # uptime fell = reboot
    boot_start = rtc - pd.to_timedelta(up, unit="ms")               # NaT where either is missing
    boot_start = boot_start.groupby([raw["_file"], boot]).transform(lambda s: s.ffill().bfill())
    rebuilt = (boot_start + pd.to_timedelta(up, unit="ms")).dt.round("s")
    t = rtc.fillna(rebuilt)
    source = np.where(rtc.notna(), "rtc", np.where(t.notna(), "uptime", "none"))
    return t, source


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
