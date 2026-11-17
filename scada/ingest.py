"""Ingest: logger rows -> historian. Two inputs, one parser.

  FolderIngest   polls a folder that holds the logger's files (a copy of the SD card, or
                 the folder the replay writes into): /YYYYMMDD.csv, /nodate.csv,
                 /markers.csv. Each file is read from where the last poll stopped, and only
                 up to its last complete line, so a file that is still being written is safe.
  SerialIngest   the logger's serial output (it prints every row it writes). Read-only:
                 nothing is ever sent to the board.

Formats: a row is read by the column names of its file's header, so the new (21 columns),
old (16) and bare-schema (14) formats all load; a column a format lacks is stored as NULL.
Without a header (serial stream joined after boot) the format is taken from the number of
fields. A row whose field count differs from its header (an old file continued by new
firmware) is read by its own count.

Rows without RTC time (date and time empty) are never dropped:
  * same boot has a timed row -> time = that row's time - its uptime_ms + this uptime_ms
    (time_source "uptime"), the rule of analysis/ingest.py;
  * serial -> the PC's clock at reception (time_source "pc");
  * otherwise -> kept in `unplaced_rows` until a timed row of the same boot arrives, and
    reported as an RTC alarm. Rows of /nodate.csv usually stay there.
Sweep files (/SW*.csv, /SS*.csv), /load_log.csv and /rom_scan.txt are not ingested yet.
"""
import calendar
import json
import pathlib
import re
import time

from data.schema import MARKER_CODES
from scada import alarms, historian, model

DAY_FILE = re.compile(r"^\d{8}\.csv$")
SERIAL_MARKER = re.compile(r"^# marker(?: \((.+?)\))?: (.*)$")
POLL_S = 1.0


def _number(text, integer=False):
    text = text.strip()
    if text == "" or text.lower() == "nan":
        return None
    return int(float(text)) if integer else float(text)


def _utc_seconds(date, clock):
    """'2026-11-15', '03:00:00' -> Unix seconds. ValueError if malformed."""
    y, m, d = (int(x) for x in date.split("-"))
    hh, mm, ss = (int(x) for x in clock.split(":"))
    if not (1 <= m <= 12 and 1 <= d <= 31 and hh < 24 and mm < 60 and ss < 60):
        raise ValueError("date or time out of range")
    return calendar.timegm((y, m, d, hh, mm, ss))


class RowParser:
    """Turns the lines of ONE source (a file or a serial port) into historian rows, and
    keeps what is needed between lines: the header, the boot counter and the time anchor
    (UTC ms at uptime 0 of the current boot)."""

    def __init__(self, source, state=None):
        s = state or {}
        self.source = source
        self.header = s.get("header")
        self.boot_no = s.get("boot_no", 0)
        self.anchor_ms = s.get("anchor_ms")
        self.last_uptime = s.get("last_uptime")
        self.line_no = s.get("line_no", 0)
        self.bad_lines = s.get("bad_lines", 0)

    def state(self):
        return {"header": self.header, "boot_no": self.boot_no, "anchor_ms": self.anchor_ms,
                "last_uptime": self.last_uptime, "line_no": self.line_no,
                "bad_lines": self.bad_lines}

    def parse(self, line, wall, pc_time=None):
        """One line -> (kind, row, new_anchor).
        kind: "row" (goes to samples), "unplaced" (no time yet), "skip" (header, comment,
        blank) or "bad" (not a row: counted). new_anchor is True when this row is the first
        timed row of its boot: rows of that boot waiting in unplaced_rows can now be placed."""
        self.line_no += 1
        line = line.strip()
        if not line or line.startswith("#"):
            return "skip", None, False
        fields = line.split(",")
        if fields[0] == "date":
            self.header = fields
            return "skip", None, False
        names = self.header if self.header and len(fields) == len(self.header) \
            else model.FORMATS.get(len(fields)) or model.PHASE_B_FORMATS.get(len(fields))
        if names is None:
            self.bad_lines += 1
            return "bad", None, False
        raw = dict(zip(names, fields))
        row = dict.fromkeys(historian.SAMPLE_COLUMNS)
        try:
            for c in model.REAL_COLUMNS:
                if c in raw:
                    row[c] = _number(raw[c])
            for c in model.INT_COLUMNS:
                if c in raw:
                    row[c] = _number(raw[c], integer=True)
            t_rtc = (_utc_seconds(raw["date"], raw["time"])
                     if raw.get("date") and raw.get("time") else None)
        except ValueError:
            self.bad_lines += 1
            return "bad", None, False
        if "load_mode" in raw:
            row["load_mode"] = raw["load_mode"].strip() or None
        row["source"], row["ingested_wall"] = self.source, wall

        up, new_anchor = row["uptime_ms"], False
        if up is not None and self.last_uptime is not None and up < self.last_uptime:
            self.boot_no += 1                 # uptime fell: the board restarted
            self.anchor_ms = None
        if up is not None:
            self.last_uptime = up
        if t_rtc is not None:
            row["t_utc"], row["time_source"] = t_rtc, "rtc"
            if up is not None:
                new_anchor = self.anchor_ms is None
                self.anchor_ms = t_rtc * 1000 - up
        elif up is not None and self.anchor_ms is not None:
            row["t_utc"], row["time_source"] = round((self.anchor_ms + up) / 1000), "uptime"
        elif pc_time is not None:
            row["t_utc"], row["time_source"] = int(pc_time), "pc"
        else:
            row["boot_no"], row["line_no"] = self.boot_no, self.line_no
            return "unplaced", row, False
        return "row", row, new_anchor


def _store_unplaced(conn, row):
    keep = {k: row[k] for k in historian.SAMPLE_COLUMNS}
    conn.execute("INSERT OR IGNORE INTO unplaced_rows (source, boot_no, line_no, uptime_ms, "
                 "row_json, ingested_wall) VALUES (?, ?, ?, ?, ?, ?)",
                 (row["source"], row["boot_no"], row["line_no"], row["uptime_ms"],
                  json.dumps(keep), row["ingested_wall"]))


def _place_waiting(conn, source, boot_no, anchor_ms):
    """The boot now has a time anchor: move its waiting rows into samples."""
    waiting = conn.execute("SELECT id, uptime_ms, row_json FROM unplaced_rows WHERE source = ? "
                           "AND boot_no = ? AND uptime_ms IS NOT NULL", (source, boot_no)).fetchall()
    rows = []
    for w in waiting:
        row = json.loads(w["row_json"])
        row["t_utc"], row["time_source"] = round((anchor_ms + w["uptime_ms"]) / 1000), "uptime"
        rows.append(row)
    historian.insert_samples(conn, rows)
    conn.executemany("DELETE FROM unplaced_rows WHERE id = ?", [(w["id"],) for w in waiting])


def _marker_time_ms(date, clock):
    """'2026-11-15', '03:10:00.137' -> Unix ms, or None if either is empty."""
    if not date or not clock:
        return None
    whole, _, frac = clock.partition(".")
    return _utc_seconds(date, whole) * 1000 + int((frac + "000")[:3])


def _store_marker(conn, line, source, note=None):
    """One /markers.csv line (date,time,millis). Returns True if it was a marker.
    A logger built with MARKER_CODES_ENABLED 1 writes a fourth field, the row's code
    (data/schema.py, MARKER_CODES): its name becomes the note if the line brings none."""
    f = [x.strip() for x in line.split(",")]
    try:
        if len(f) not in (3, 4):
            return False
        t_ms, millis = _marker_time_ms(f[0], f[1]), int(f[2])
        if len(f) == 4 and int(f[3]) and note is None:
            note = MARKER_CODES.get(int(f[3]), f"code {int(f[3])}")
    except ValueError:
        return False
    conn.execute("INSERT OR IGNORE INTO markers (t_utc_ms, millis, note, source) "
                 "VALUES (?, ?, ?, ?)", (t_ms, millis, note, source))
    return True


def _report_unplaced(conn, wall):
    """Rows that still have no time: raise (or keep) the RTC alarm when their number grew."""
    n = conn.execute("SELECT COUNT(*) FROM unplaced_rows").fetchone()[0]
    if n > int(historian.get_meta(conn, "unplaced_reported", 0)):
        alarms.raise_untimed(conn, "RTC_INVALID", wall,
                             f"{n} rows without any usable time (kept in unplaced_rows)")
    historian.set_meta(conn, "unplaced_reported", n)


class FolderIngest:
    def __init__(self, conn, folder):
        self.conn, self.folder = conn, pathlib.Path(folder)

    def files(self):
        """Day files in date order, then /nodate.csv."""
        if not self.folder.is_dir():
            return []
        days = sorted(p for p in self.folder.iterdir() if DAY_FILE.match(p.name))
        nodate = self.folder / "nodate.csv"
        return days + ([nodate] if nodate.exists() else [])

    def _new_lines(self, path):
        """(complete lines added since the last poll, saved parser state, new offset, key
        of the file in ingest_files), or None if there is nothing new."""
        key = str(path.resolve())
        saved = self.conn.execute("SELECT byte_offset, state_json FROM ingest_files WHERE "
                                  "path = ?", (key,)).fetchone()
        offset, state = (saved["byte_offset"], json.loads(saved["state_json"])) if saved \
            else (0, None)
        size = path.stat().st_size
        if size < offset:                     # the file was replaced by a shorter one
            offset, state = 0, None
        if size == offset:
            return None
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
        end = data.rfind(b"\n")
        if end < 0:                           # no complete line yet
            return None
        lines = data[:end + 1].decode("utf-8", errors="replace").splitlines()
        return lines, state, offset + end + 1, key

    def _save(self, key, offset, state, wall):
        self.conn.execute(
            "INSERT INTO ingest_files (path, byte_offset, state_json, updated_wall) VALUES "
            "(?, ?, ?, ?) ON CONFLICT (path) DO UPDATE SET byte_offset = excluded.byte_offset, "
            "state_json = excluded.state_json, updated_wall = excluded.updated_wall",
            (key, offset, json.dumps(state), wall))

    def _tail_rows(self, path, wall):
        got = self._new_lines(path)
        if got is None:
            return 0
        lines, state, offset, key = got
        parser, rows = RowParser(path.name, state), []
        for line in lines:
            kind, row, new_anchor = parser.parse(line, wall)
            if kind == "unplaced":
                _store_unplaced(self.conn, row)
            elif kind == "row":
                if new_anchor:
                    _place_waiting(self.conn, parser.source, parser.boot_no, parser.anchor_ms)
                rows.append(row)
        new = historian.insert_samples(self.conn, rows)
        self._save(key, offset, parser.state(), wall)
        return new

    def _tail_markers(self, wall):
        path = self.folder / "markers.csv"
        got = self._new_lines(path) if path.exists() else None
        if got is None:
            return 0
        lines, _, offset, key = got
        n = sum(_store_marker(self.conn, line, path.name) for line in lines)
        self._save(key, offset, {}, wall)
        return n

    def poll(self, wall=None):
        """Read everything new in the folder, evaluate the alarms, commit.
        Returns {"rows": new samples, "markers": marker lines read}."""
        wall = time.time() if wall is None else wall
        rows = sum(self._tail_rows(p, wall) for p in self.files())
        markers = self._tail_markers(wall)
        _report_unplaced(self.conn, wall)
        alarms.evaluate(self.conn, wall)
        if rows:
            historian.set_meta(self.conn, "last_row_wall", wall)
        alarms.check_no_data(self.conn, wall)
        self.conn.commit()
        return {"rows": rows, "markers": markers}


class SerialIngest:
    """The logger's serial lines. Kept apart from the port itself so it can be tested
    without hardware: handle_line() takes text."""

    def __init__(self, conn, port):
        self.conn, self.source = conn, f"serial:{port}"
        self.parser = RowParser(self.source)

    def handle_line(self, line, wall=None):
        wall = time.time() if wall is None else wall
        m = SERIAL_MARKER.match(line.strip())
        if m:
            _store_marker(self.conn, m.group(2), self.source, note=m.group(1))
            self.conn.commit()
            return "marker"
        kind, row, _ = self.parser.parse(line, wall, pc_time=wall)
        if kind == "row" and historian.insert_samples(self.conn, [row]):
            alarms.evaluate(self.conn, wall)
            historian.set_meta(self.conn, "last_row_wall", wall)
        self.conn.commit()
        return kind


    def check_no_data(self):
        alarms.check_no_data(self.conn)
        self.conn.commit()


def run_folder(db_path, folder, stop, poll_s=POLL_S):
    """Thread body: poll the folder until `stop` is set."""
    ingest = FolderIngest(historian.connect(db_path), folder)
    while not stop.is_set():
        ingest.poll()
        stop.wait(poll_s)
    ingest.conn.close()


def run_serial(db_path, port, baud, stop):
    """Thread body: read the logger's serial port until `stop` is set. NOT tested on the
    board yet. DTR and RTS are held low before the port opens, because on most ESP32
    dev boards raising them resets the chip [to check on our board]."""
    import serial                             # pyserial; only needed with --serial
    ingest = SerialIngest(historian.connect(db_path), port)
    while not stop.is_set():
        try:
            ser = serial.Serial()
            ser.port, ser.baudrate, ser.timeout = port, baud, 1.0
            ser.dtr = ser.rts = False
            ser.open()
            with ser:
                buf = b""
                while not stop.is_set():
                    ingest.check_no_data()
                    buf += ser.read(ser.in_waiting or 1)   # waits up to the 1 s timeout
                    *lines, buf = buf.split(b"\n")         # the unfinished line stays in buf
                    for line in lines:
                        ingest.handle_line(line.decode("utf-8", errors="replace"))
        except serial.SerialException as e:   # unplugged, port busy: try again
            print(f"serial {port}: {e}; retrying in 5 s", flush=True)
            ingest.check_no_data()
            stop.wait(5.0)
    ingest.conn.close()
