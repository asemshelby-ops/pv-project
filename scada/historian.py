"""The historian: one SQLite file (standard library, no server to install).

Tables
  samples        one row per 10 s logger row, keyed by its UTC second (t_utc). A second
                 copy of the same row (the SD file after the serial stream, a file ingested
                 twice) is ignored, as analysis/ingest.py drops duplicate times.
  unplaced_rows  rows the logger wrote without RTC time that cannot be put on the time axis
                 yet (kept, never dropped; see scada/ingest.py)
  markers        /markers.csv: button presses and LOAD OFF / LOAD ON marks
  logger_events  /events.csv and the serial "# event:" lines: boot, sensor_fail, sd_lost,
                 sd_back, ring_full, ring_flushed, ... (the same event from both is kept once)
  ingest_files   how far each file has been read, so a restart goes on where it stopped
  alarms         one row per alarm occurrence (scada/alarms.py)
  alarm_journal  every RAISED / CLEARED / ACKED event: the alarm history
  meta           small key-value store (mode, alarm cursor)
Times: t_utc and the alarm times are Unix seconds UTC taken from the DATA; *_wall columns
are the PC clock (when the SCADA received or the operator acted).
"""
import pathlib
import sqlite3

from scada import model

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS samples (
    t_utc INTEGER PRIMARY KEY,
    time_source TEXT NOT NULL,              -- rtc | uptime | pc
    {", ".join(f"{c} REAL" for c in model.REAL_COLUMNS)},
    {", ".join(f"{c} INTEGER" for c in model.INT_COLUMNS)},
    load_mode TEXT,
    source TEXT NOT NULL,
    ingested_wall REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS unplaced_rows (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    boot_no INTEGER NOT NULL,
    line_no INTEGER NOT NULL,
    uptime_ms INTEGER,
    row_json TEXT NOT NULL,
    ingested_wall REAL NOT NULL,
    UNIQUE (source, line_no)
);
CREATE TABLE IF NOT EXISTS markers (
    id INTEGER PRIMARY KEY,
    t_utc_ms INTEGER,                       -- NULL: pressed without RTC time
    millis INTEGER NOT NULL,
    note TEXT,                              -- 'load off' / 'load on' (known from serial only)
    source TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS markers_once ON markers (ifnull(t_utc_ms, -1), millis);
CREATE TABLE IF NOT EXISTS logger_events (
    id INTEGER PRIMARY KEY,
    t_utc INTEGER,                          -- NULL: logged without RTC time
    uptime_ms INTEGER,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    ingested_wall REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS logger_events_once
    ON logger_events (ifnull(t_utc, -1), ifnull(uptime_ms, -1), event);
CREATE TABLE IF NOT EXISTS ingest_files (
    path TEXT PRIMARY KEY,
    byte_offset INTEGER NOT NULL,
    state_json TEXT NOT NULL,               -- parser state (header, boot, anchor, counters)
    updated_wall REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS alarms (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL,
    priority INTEGER NOT NULL,
    text TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    t_raised INTEGER,                       -- NULL: raised by rows that have no time
    t_last INTEGER,                         -- last row at which the condition was on
    t_cleared INTEGER,                      -- NULL while active
    cleared INTEGER NOT NULL DEFAULT 0,
    clear_count INTEGER NOT NULL DEFAULT 0, -- rows in a row without the condition so far
    n_rows INTEGER NOT NULL DEFAULT 0,      -- rows with the condition on
    raised_wall REAL NOT NULL,
    acked_wall REAL,                        -- NULL = not acknowledged
    acked_by TEXT,
    ack_note TEXT
);
CREATE INDEX IF NOT EXISTS alarms_open ON alarms (cleared, acked_wall);
CREATE TABLE IF NOT EXISTS alarm_journal (
    id INTEGER PRIMARY KEY,
    alarm_id INTEGER NOT NULL REFERENCES alarms (id),
    code TEXT NOT NULL,
    event TEXT NOT NULL,                    -- RAISED | CLEARED | ACKED
    t_data INTEGER,                         -- data time of the event (NULL for ACKED)
    t_wall REAL NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
SAMPLE_COLUMNS = (["t_utc", "time_source"] + model.REAL_COLUMNS + model.INT_COLUMNS
                  + ["load_mode", "source", "ingested_wall"])
INSERT_SAMPLE = (f"INSERT OR IGNORE INTO samples ({', '.join(SAMPLE_COLUMNS)}) "
                 f"VALUES ({', '.join(':' + c for c in SAMPLE_COLUMNS)})")


def connect(path):
    """Open (and create if needed) the historian. One connection per thread."""
    if str(path) != ":memory:":
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")     # the HMI reads while the ingest writes
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    _add_missing_columns(conn)
    return conn


def _add_missing_columns(conn):
    """A historian made by an older version lacks the columns added since (the SD-card
    health tags of 2026-11-21): add them, empty (NULL = not recorded) for the old rows."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(samples)")}
    for c in model.REAL_COLUMNS + model.INT_COLUMNS:
        if c not in have:
            kind = "REAL" if c in model.REAL_COLUMNS else "INTEGER"
            conn.execute(f"ALTER TABLE samples ADD COLUMN {c} {kind}")
    conn.commit()


def insert_event(conn, row):
    """row: t_utc, uptime_ms, event, detail, source, ingested_wall. 1 if it was new."""
    cur = conn.execute("INSERT OR IGNORE INTO logger_events (t_utc, uptime_ms, event, detail, "
                       "source, ingested_wall) VALUES (:t_utc, :uptime_ms, :event, :detail, "
                       ":source, :ingested_wall)", row)
    return cur.rowcount


def get_meta(conn, key, default=None):
    r = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else default


def set_meta(conn, key, value):
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                 "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, str(value)))


def insert_samples(conn, rows):
    """rows: dicts with every SAMPLE_COLUMNS key. Returns how many were new."""
    before = conn.total_changes
    conn.executemany(INSERT_SAMPLE, rows)
    return conn.total_changes - before


def latest(conn):
    r = conn.execute("SELECT * FROM samples ORDER BY t_utc DESC LIMIT 1").fetchone()
    return dict(r) if r else None
