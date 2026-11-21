"""The SD card's state reaches the PC (follow-up of 2026-11-21): the logger's ring columns
(ring_rows, ring_dropped, f_sd, sens_fail) are SCADA tags, its sd_lost / sd_back / ring_*
events are kept, a card that stays away raises SD_OUTAGE, given-up lines raise
RING_DROPPED, and the Telegram notifier sends the alarm well within one hour.

The rows come from the logger's own C logic (firmware/tools/logger_sim.py, fake card),
printed as the sketch prints them on Serial, through scada.ingest.SerialIngest into a LIVE
historian; the notifier runs against a fake phone on a fake clock. Nothing is measured."""
import calendar
import importlib.util
import pathlib
import sqlite3

import pytest

from scada import historian, model, notify, run
from scada.ingest import FolderIngest, SerialIngest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("logger_sim", ROOT / "firmware/tools/logger_sim.py")
ls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ls)

needs_gcc = pytest.mark.skipif(ls.find_gcc() is None, reason="no gcc")
HOUR = 3600


class Phone:
    """Stands in for the Telegram client: keeps what would have been sent."""

    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


def row_time(row):
    y, m, d = (int(x) for x in row["date"].split("-"))
    hh, mm, ss = (int(x) for x in row["time"].split(":"))
    return calendar.timegm((y, m, d, hh, mm, ss))


class Link:
    """The logger on a serial cable to a live SCADA with the phone alerts switched on."""

    def __init__(self, tmp_path):
        self.sim = ls.Sim(scan_every=10 ** 6)
        self.sim.rig.convert_ms = 0
        self.db = tmp_path / "historian.sqlite"
        run.open_historian(self.db, "live")
        self.conn = historian.connect(self.db)
        self.ingest = SerialIngest(self.conn, "COM3")
        self.phone = Phone()
        self.wall = None
        self.notifier = None
        self.sent_at = []                                 # (wall, text)

    def rows(self, n, plan=None):
        header = self.sim.header().split(",")
        for _ in range(n):
            if plan:
                plan(self.sim.n, self.sim.rig)
            k0 = len(self.sim.rig.serial)
            row = self.sim.cycle()
            self.wall = row_time(row) + 1                 # received 1 s after the row time
            for text in self.sim.rig.serial[k0:]:         # the sketch prints "# " + text
                self.ingest.handle_line("# " + text, wall=self.wall)
            self.ingest.handle_line(",".join(row[c] for c in header), wall=self.wall)
            if self.notifier is None:
                self.notifier = notify.Notifier(self.db, self.phone, clock=lambda: self.wall,
                                                log=lambda m: None)
            before = len(self.phone.sent)
            self.notifier.tick(now=self.wall)
            self.sent_at += [(self.wall, t) for t in self.phone.sent[before:]]

    def first_message(self, code, event="RAISED"):
        word = {"RAISED": "raised", "CLEARED": "cleared"}[event]
        for wall, text in self.sent_at:
            if code in text and word in text.lower():
                return wall, text
        return None, None


def out_between(first, last):
    return lambda k, rig: setattr(rig, "card_in", not first <= k < last)


# --- the tags and the alarm rule --------------------------------------------------------------

def test_the_ring_columns_are_stored_tags():
    for c in ("sens_fail", "f_sd", "ring_rows", "ring_dropped"):
        assert c in model.FORMAT_V2 and c in historian.SAMPLE_COLUMNS
        assert c in {t.name for t in model.TAGS}


@pytest.mark.parametrize("ring_rows, expected", [
    (None, None),                 # older file: the row cannot tell
    (0, False),                   # card up to date: off
    (1, None), (29, None),        # a short hiccup, or the ring draining: no change
    (model.RING_ALERT_LINES, True), (4096, True),
])
def test_sd_outage_has_hysteresis(ring_rows, expected):
    got = model.conditions({"ring_rows": ring_rows}).get("SD_OUTAGE")
    assert got is expected


@pytest.mark.parametrize("dropped, expected", [(None, None), (0, False), (1, True), (32, True)])
def test_ring_dropped_is_on_from_the_first_line_given_up(dropped, expected):
    assert model.conditions({"ring_dropped": dropped}).get("RING_DROPPED") is expected


def test_both_sd_alarms_are_sent_to_the_phone():
    for code in ("SD_OUTAGE", "RING_DROPPED"):
        assert model.ALARM_BY_CODE[code].priority in notify.NOTIFY_PRIORITIES


def test_a_historian_made_before_the_new_tags_gets_them_on_opening(tmp_path):
    new = ("sens_fail", "f_sd", "ring_rows", "ring_dropped")
    old_cols = [c for c in historian.SAMPLE_COLUMNS if c not in new]   # the schema before
    db = tmp_path / "old.sqlite"
    raw = sqlite3.connect(db)
    raw.execute(f"CREATE TABLE samples ({', '.join(old_cols)}, PRIMARY KEY (t_utc))")
    raw.execute("INSERT INTO samples (t_utc, time_source, status, source, ingested_wall) "
                "VALUES (1, 'rtc', 0, 'old', 0)")
    raw.commit()
    raw.close()
    conn = historian.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(samples)")}
    assert set(historian.SAMPLE_COLUMNS) <= cols
    old = dict(conn.execute("SELECT * FROM samples").fetchone())
    assert old["status"] == 0 and old["ring_rows"] is None    # old row: "not recorded"
    conn.close()


# --- the logger's rows through the serial ingest --------------------------------------------

@needs_gcc
def test_a_card_pulled_for_an_hour_reaches_the_phone_within_minutes(tmp_path):
    link = Link(tmp_path)
    link.rows(3)
    t_out = link.wall                                     # the last row on the card
    link.rows(400, out_between(3, 403))                   # 400 rows = 67 min without the card
    wall, text = link.first_message("SD_OUTAGE")
    assert wall is not None, [t for _, t in link.sent_at]
    assert wall - t_out <= HOUR                           # what was asked
    assert wall - t_out <= 6 * 60 + 30                    # what the rule gives [sim]
    assert "lines waiting" in text
    alarm = link.conn.execute("SELECT * FROM alarms WHERE code = 'SD_OUTAGE'").fetchone()
    assert alarm["cleared"] == 0
    assert link.first_message("RING_DROPPED")[0] is None  # 400 rows fit in the ring
    latest = historian.latest(link.conn)
    assert latest["ring_rows"] >= 400 and latest["ring_dropped"] == 0
    assert model.health(latest)[-2]["state"] == "fault"   # the HMI's SD card item
    events = [r["event"] for r in link.conn.execute("SELECT event FROM logger_events")]
    assert "sd_lost" in events

    link.rows(120, out_between(3, 403))                   # card back: the ring drains
    assert historian.latest(link.conn)["ring_rows"] == 0
    assert link.conn.execute("SELECT cleared FROM alarms WHERE code = 'SD_OUTAGE'"
                             ).fetchone()["cleared"] == 1
    assert link.first_message("SD_OUTAGE", "CLEARED")[0] is not None
    events = [r["event"] for r in link.conn.execute("SELECT event FROM logger_events")]
    assert {"sd_lost", "sd_back", "ring_flushed"} <= set(events)
    beat = notify.heartbeat_text(link.conn, link.wall)
    assert "SD card: up to date (0 lines waiting in the LittleFS ring)" in beat
    assert "sd_lost 1" in beat and "sd_back 1" in beat


@needs_gcc
def test_one_failed_write_is_not_an_outage(tmp_path):
    link = Link(tmp_path)
    link.rows(3)
    link.sim.rig.card_fail_appends = 1                    # one write fails; the re-mount works
    link.rows(60)
    codes = [r["code"] for r in link.conn.execute("SELECT code FROM alarms")]
    assert "SD_OUTAGE" not in codes and "RING_DROPPED" not in codes
    assert link.first_message("SD_OUTAGE")[0] is None


@needs_gcc
def test_a_short_outage_of_a_minute_raises_no_sd_outage(tmp_path):
    link = Link(tmp_path)
    link.rows(3)
    link.rows(20, out_between(3, 9))                      # 6 rows: < 30 lines in the ring
    codes = [r["code"] for r in link.conn.execute("SELECT code FROM alarms")]
    assert "SD_OUTAGE" not in codes
    assert "SD_ERROR" in codes                            # the per-row alarm still says it


@needs_gcc
def test_a_long_outage_that_fills_the_ring_raises_ring_dropped(tmp_path):
    link = Link(tmp_path)
    link.rows(3)
    link.rows(4200, out_between(3, 10 ** 6))              # the ring holds 4096 lines
    wall, text = link.first_message("RING_DROPPED")
    assert wall is not None and "lines given up" in text
    assert historian.latest(link.conn)["ring_dropped"] > 0
    assert "ring_full" in {r["event"] for r in link.conn.execute("SELECT event FROM logger_events")}
    assert "lines given up" in notify.heartbeat_text(link.conn, link.wall)


def test_events_from_the_card_and_from_serial_are_kept_once(tmp_path):
    conn = historian.connect(tmp_path / "h.sqlite")
    line = "2026-11-25,06:00:32,37402,sd_lost,card not answering or write failed: lines go to the LittleFS buffer"
    assert SerialIngest(conn, "COM3").handle_line("# event: " + line, wall=1.0) == "event"
    (tmp_path / "card").mkdir()
    (tmp_path / "card/events.csv").write_text("date,time,uptime_ms,event,detail\n" + line
                                              + "\n2026-11-25,06:10:02,637402,sd_back,card mounted\n")
    assert FolderIngest(conn, tmp_path / "card").poll(wall=2.0)["events"] == 1   # sd_back only
    rows = [dict(r) for r in conn.execute("SELECT * FROM logger_events ORDER BY t_utc")]
    assert [r["event"] for r in rows] == ["sd_lost", "sd_back"]
    assert rows[0]["detail"].startswith("card not answering") and rows[0]["uptime_ms"] == 37402
    assert FolderIngest(conn, tmp_path / "card").poll(wall=3.0)["events"] == 0   # nothing new


def test_the_heartbeat_without_v2_rows_says_so(tmp_path):
    conn = historian.connect(tmp_path / "h.sqlite")
    assert notify.sd_lines(conn, 1_800_000_000) == [
        "SD card: not reported (no CSV v2 row in the historian)",
        "SD events in the last 24 h: none"]
