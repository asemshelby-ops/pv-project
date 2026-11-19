"""Where the MODEL data are kept: two CSV files of their own and two historian tables of
their own. Nothing here ever writes into a logger file, the logger's folder, or the
historian's `samples` table; the tables are created only when the weather reference runs.

  <WX_DIR>/wx_model.csv          every hourly model row, one per (wx_source, t_utc)
  <WX_DIR>/wx_daily_check.csv    the daily PYR20 check (wxref/check.py)
  historian table wx_model       the same rows as wx_model.csv
  historian table wx_check       the same rows as wx_daily_check.csv
"""
import os
import pathlib

import numpy as np
import pandas as pd

import config
from wxref.check import CHECK_COLUMNS
from wxref.openmeteo import ROW_COLUMNS

MODEL_CSV = "wx_model.csv"
CHECK_CSV = "wx_daily_check.csv"
_REAL = [c for c in ROW_COLUMNS if c not in ("t_utc", "wx_source")]
_CHECK_TEXT = {"date", "status", "wx_source", "event", "message"}
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS wx_model (\n"
    "    wx_source TEXT NOT NULL,               -- forecast | archive | satellite (MODEL data)\n"
    "    t_utc INTEGER NOT NULL,                -- hour stamp, Unix s; radiation = mean of the\n"
    "                                           -- hour ENDING here, the rest instant\n"
    + "".join(f"    {c} REAL,\n" for c in _REAL)
    + "    fetched_wall REAL NOT NULL,\n"
    "    PRIMARY KEY (wx_source, t_utc)\n);\n"
    "CREATE TABLE IF NOT EXISTS wx_check (\n"
    "    date TEXT PRIMARY KEY,                 -- local (Makkah) date\n"
    + ",\n".join(f"    {c} {'TEXT' if c in _CHECK_TEXT else 'REAL'}"
                 for c in CHECK_COLUMNS if c != "date")
    + "\n);\n")


def out_dir(folder=None):
    return pathlib.Path(folder or config.WX_DIR)


def _atomic_csv(df, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8")
    os.replace(tmp, path)


def _read_csv(path, columns):
    try:
        return pd.read_csv(path, encoding="utf-8")[columns]
    except (OSError, ValueError, KeyError):
        return pd.DataFrame(columns=columns)


def merge_model_csv(rows, folder=None):
    """Add model rows to wx_model.csv (a newer fetch of the same hour replaces the older).
    Returns the whole table."""
    path = out_dir(folder) / MODEL_CSV
    old = _read_csv(path, ROW_COLUMNS)
    parts = [d for d in (old, rows[ROW_COLUMNS]) if len(d)]
    if not parts:
        return pd.DataFrame(columns=ROW_COLUMNS)
    allr = pd.concat(parts, ignore_index=True)
    allr = (allr.drop_duplicates(["wx_source", "t_utc"], keep="last")
            .sort_values(["t_utc", "wx_source"]).reset_index(drop=True))
    _atomic_csv(allr, path)
    return allr


def read_model_csv(folder=None):
    return _read_csv(out_dir(folder) / MODEL_CSV, ROW_COLUMNS)


def write_check_csv(checks, folder=None):
    _atomic_csv(checks[CHECK_COLUMNS], out_dir(folder) / CHECK_CSV)


def read_check_csv(folder=None):
    df = _read_csv(out_dir(folder) / CHECK_CSV, CHECK_COLUMNS)
    for c in CHECK_COLUMNS:
        if c not in _CHECK_TEXT:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


# --- historian tables (scada/historian.py file, own tables) -----------------------------------

def has_tables(conn):
    n = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name IN "
                     "('wx_model', 'wx_check')").fetchone()[0]
    return n == 2


def ensure_tables(conn):
    conn.executescript(SCHEMA)


def _clean(v):
    if v is None:
        return None
    if isinstance(v, (float, np.floating)) and not np.isfinite(v):
        return None
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    return v


def upsert_model(conn, rows, fetched_wall):
    cols = ROW_COLUMNS + ["fetched_wall"]
    sql = (f"INSERT OR REPLACE INTO wx_model ({', '.join(cols)}) "
           f"VALUES ({', '.join('?' * len(cols))})")
    conn.executemany(sql, [tuple(_clean(v) for v in r) + (fetched_wall,)
                           for r in rows[ROW_COLUMNS].itertuples(index=False)])


def read_model(conn, t0=None, t1=None):
    """Model rows with t0 <= t_utc <= t1 (all if None), as ROW_COLUMNS."""
    if not has_tables(conn):
        return pd.DataFrame(columns=ROW_COLUMNS)
    q, args = f"SELECT {', '.join(ROW_COLUMNS)} FROM wx_model", []
    if t0 is not None:
        q += " WHERE t_utc >= ? AND t_utc <= ?"
        args = [int(t0), int(t1)]
    rows = conn.execute(q + " ORDER BY t_utc, wx_source", args).fetchall()
    df = pd.DataFrame([tuple(r) for r in rows], columns=ROW_COLUMNS)
    for c in _REAL:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


def replace_checks(conn, checks):
    """The check table is recomputed as a whole (the rolling values depend on every day)."""
    conn.execute("DELETE FROM wx_check")
    sql = (f"INSERT INTO wx_check ({', '.join(CHECK_COLUMNS)}) "
           f"VALUES ({', '.join('?' * len(CHECK_COLUMNS))})")
    conn.executemany(sql, [tuple(_clean(v) for v in r)
                           for r in checks[CHECK_COLUMNS].itertuples(index=False)])


def read_checks(conn):
    if not has_tables(conn):
        return pd.DataFrame(columns=CHECK_COLUMNS)
    rows = conn.execute(f"SELECT {', '.join(CHECK_COLUMNS)} FROM wx_check ORDER BY date")
    df = pd.DataFrame([tuple(r) for r in rows.fetchall()], columns=CHECK_COLUMNS)
    for c in CHECK_COLUMNS:
        if c not in _CHECK_TEXT:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df

