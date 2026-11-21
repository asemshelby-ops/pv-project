"""Alarm engine: turns the bits of the logger rows into alarm occurrences with a life cycle.

An occurrence (one row of `alarms`) goes through:

    RAISED    the condition is on at a row and no occurrence of that code is open
    CLEARED   the condition has been off for `clear_rows` rows in a row (scada/model.py:
              1 for the detector alarms, which last one row; 3 for faults, so a sensor
              that fails every other row gives one alarm, not a flood)
    ACKED     an operator acknowledged it (before or after it cleared)

State shown by the HMI: active (not cleared) x acknowledged (or not). An occurrence leaves
the alarm list when it is both cleared and acknowledged; it stays in the history, and every
event is one line of `alarm_journal`.

Rows are evaluated once, in time order, from the newest row already evaluated ("alarm
cursor"). Rows that arrive later with an OLDER time (an SD card copied after live data)
are stored and plotted but raise no alarms. A row that cannot tell (its `status` or `alarm`
was not recorded: old file format) neither raises nor clears.

Times: t_raised / t_cleared come from the DATA (UTC seconds of the row); acked_wall and
raised_wall are the PC clock. In a replay the two differ by design.
The alarms report what the logger wrote. They drive nothing.
"""
import time

from data.schema import BACK_SENSORS
from scada import historian, model

# what a row's alarm conditions and details read: the fixed fields, every counter column an
# alarm watches (ring_rows, ring_dropped), and f_sd for the SD_OUTAGE detail
ROW_COLUMNS = ", ".join(dict.fromkeys(
    ["t_utc", "time_source", "alarm", "status", "load_mode", "p", "irradiance", *BACK_SENSORS,
     *(d.field for d in model.ALARM_DEFS if d.on_at), "f_sd"]))


def _journal(conn, alarm_id, code, event, t_data, wall, detail=""):
    conn.execute("INSERT INTO alarm_journal (alarm_id, code, event, t_data, t_wall, detail) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (alarm_id, code, event, t_data, wall, detail))


def _raise(conn, d, t_data, wall, detail, n_rows):
    cur = conn.execute(
        "INSERT INTO alarms (code, priority, text, detail, t_raised, t_last, n_rows, "
        "raised_wall) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (d.code, d.priority, d.text, detail, t_data, t_data, n_rows, wall))
    _journal(conn, cur.lastrowid, d.code, "RAISED", t_data, wall, detail)
    return cur.lastrowid


def evaluate(conn, wall=None):
    """Evaluate every sample newer than the alarm cursor. Returns the number of rows read.
    The caller commits."""
    wall = time.time() if wall is None else wall
    cursor = int(historian.get_meta(conn, "alarm_cursor", -1))
    open_ = {r["code"]: dict(r) for r in conn.execute(
        "SELECT id, code, t_last, clear_count, n_rows FROM alarms WHERE cleared = 0")}
    changed, n, t = set(), 0, cursor
    for row in conn.execute(f"SELECT {ROW_COLUMNS} FROM samples WHERE t_utc > ? ORDER BY t_utc",
                            (cursor,)).fetchall():
        row, n = dict(row), n + 1
        t = row["t_utc"]
        for code, on in model.conditions(row).items():
            a = open_.get(code)
            if on and a is None:
                alarm_id = _raise(conn, model.ALARM_BY_CODE[code], t, wall,
                                  model.detail(code, row), 1)
                open_[code] = {"id": alarm_id, "code": code, "t_last": t, "clear_count": 0,
                               "n_rows": 1}
            elif on:
                a["t_last"], a["clear_count"], a["n_rows"] = t, 0, a["n_rows"] + 1
                changed.add(code)
            elif a is not None:
                a["clear_count"] += 1
                changed.add(code)
                if a["clear_count"] >= model.ALARM_BY_CODE[code].clear_rows:
                    conn.execute("UPDATE alarms SET cleared = 1, t_cleared = ?, t_last = ?, "
                                 "n_rows = ?, clear_count = ? WHERE id = ?",
                                 (t, a["t_last"], a["n_rows"], a["clear_count"], a["id"]))
                    _journal(conn, a["id"], code, "CLEARED", t, wall)
                    del open_[code]
    for code in changed & open_.keys():      # still open: save the counters once per batch
        a = open_[code]
        conn.execute("UPDATE alarms SET t_last = ?, n_rows = ?, clear_count = ? WHERE id = ?",
                     (a["t_last"], a["n_rows"], a["clear_count"], a["id"]))
    if n:
        historian.set_meta(conn, "alarm_cursor", t)
    return n


def raise_untimed(conn, code, wall, detail):
    """Raise `code` from rows that have no time (t_raised stays NULL). If an occurrence is
    already open only its detail is updated. It clears like any other, at the first timed
    rows that show the condition off."""
    open_ = conn.execute("SELECT id FROM alarms WHERE code = ? AND cleared = 0",
                         (code,)).fetchone()
    if open_:
        conn.execute("UPDATE alarms SET detail = ? WHERE id = ?", (detail, open_["id"]))
        return open_["id"]
    return _raise(conn, model.ALARM_BY_CODE[code], None, wall, detail, 0)


def check_no_data(conn, wall=None, limit_s=model.NO_DATA_LIMIT_S):
    """The "no data arriving" alarm. Raised when the last new row was received more than
    `limit_s` ago on the PC clock (or, with an empty historian, when none came within
    `limit_s` of the first check); cleared as soon as a new row has been received.
    The PC clock is used, not the rows' own time, so it works the same in a replay.
    Called by every ingest loop about once a second. The caller commits."""
    wall = time.time() if wall is None else wall
    ref = historian.get_meta(conn, "last_row_wall")
    if ref is None:
        ref = historian.get_meta(conn, "watch_start_wall")
        if ref is None:
            historian.set_meta(conn, "watch_start_wall", wall)
            ref = wall
    late = wall - float(ref) > limit_s
    open_ = conn.execute("SELECT id FROM alarms WHERE code = 'NO_DATA' AND cleared = 0").fetchone()
    if late and not open_:
        _raise(conn, model.ALARM_BY_CODE["NO_DATA"], None, wall,
               f"no new row received for more than {limit_s:g} s", 0)
    elif open_ and not late:
        t = conn.execute("SELECT MAX(t_utc) FROM samples").fetchone()[0]
        conn.execute("UPDATE alarms SET cleared = 1, t_cleared = ? WHERE id = ?", (t, open_["id"]))
        _journal(conn, open_["id"], "NO_DATA", "CLEARED", t, wall, "rows resumed")


def acknowledge(conn, alarm_id, by, note="", wall=None):
    """Acknowledge one occurrence. Returns False if it does not exist or was acknowledged
    before (the first acknowledgement is kept)."""
    wall = time.time() if wall is None else wall
    cur = conn.execute("UPDATE alarms SET acked_wall = ?, acked_by = ?, ack_note = ? "
                       "WHERE id = ? AND acked_wall IS NULL", (wall, by, note, alarm_id))
    if cur.rowcount == 0:
        return False
    code = conn.execute("SELECT code FROM alarms WHERE id = ?", (alarm_id,)).fetchone()["code"]
    _journal(conn, alarm_id, code, "ACKED", None, wall, f"by {by}" + (f": {note}" if note else ""))
    return True


def acknowledge_all(conn, by, note="", wall=None):
    ids = [r["id"] for r in conn.execute("SELECT id FROM alarms WHERE acked_wall IS NULL")]
    return sum(acknowledge(conn, i, by, note, wall) for i in ids)


def _state(a):
    return ("ACTIVE" if not a["cleared"] else "CLEARED") + \
           ("_ACKED" if a["acked_wall"] is not None else "_UNACKED")


def list_alarms(conn, which="open", limit=200):
    """which="open": the alarm list (active or not yet acknowledged), unacknowledged first,
    then by priority, newest first. which="all": every occurrence, newest first."""
    if which == "open":
        sql = ("SELECT * FROM alarms WHERE cleared = 0 OR acked_wall IS NULL ORDER BY "
               "acked_wall IS NOT NULL, priority, id DESC LIMIT ?")
    else:
        sql = "SELECT * FROM alarms ORDER BY id DESC LIMIT ?"
    out = []
    for r in conn.execute(sql, (limit,)):
        a = dict(r)
        a["state"] = _state(a)
        a["group"] = model.ALARM_BY_CODE[a["code"]].group if a["code"] in model.ALARM_BY_CODE \
            else "system"
        out.append(a)
    return out


def journal(conn, limit=500):
    return [dict(r) for r in conn.execute(
        "SELECT j.*, a.text, a.priority FROM alarm_journal j JOIN alarms a ON a.id = j.alarm_id "
        "ORDER BY j.id DESC LIMIT ?", (limit,))]


def summary(conn):
    r = conn.execute("SELECT COALESCE(SUM(cleared = 0), 0) AS active, "
                     "COALESCE(SUM(acked_wall IS NULL), 0) AS unacked, "
                     "MIN(CASE WHEN acked_wall IS NULL OR cleared = 0 THEN priority END) AS top "
                     "FROM alarms").fetchone()
    return {"active": r["active"], "unacked": r["unacked"], "top_priority": r["top"]}
