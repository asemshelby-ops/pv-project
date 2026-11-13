"""SCADA alarm engine: the alarm bits (1 = NASIM, 2 = threshold, 4 = CUSUM), the status
bits, the raise / clear / acknowledge life cycle and the journal. Rows are typed here as
the logger writes them; nothing is measured."""
import pytest

from data.schema import ALARM_BITS, STATUS_BITS
from scada import alarms, historian, model
from scada.ingest import FolderIngest

BIT = {name: 1 << bit for name, bit in STATUS_BITS.items()}
T0 = 1_800_000_000                                   # any UTC second


@pytest.fixture
def conn():
    c = historian.connect(":memory:")
    yield c
    c.close()


def feed(conn, rows, start=None, wall=100.0):
    """rows: one dict per 10 s row, e.g. {"alarm": 1} or {"status": 2, "T3": None}."""
    start = start if start is not None else \
        (conn.execute("SELECT MAX(t_utc) FROM samples").fetchone()[0] or T0 - 10) + 10
    full = []
    for k, r in enumerate(rows):
        row = dict.fromkeys(historian.SAMPLE_COLUMNS)
        row.update({"T1": 40.0, "T2": 40.0, "T3": 40.0, "T4": 40.0, "T5": 40.0,
                    "T_ambient": 30.0, "irradiance": 900.0, "alarm": 0, "status": 0,
                    "load_mode": "none", "time_source": "rtc", "source": "test",
                    "ingested_wall": wall, "t_utc": start + 10 * k})
        row.update(r)
        full.append(row)
    historian.insert_samples(conn, full)
    alarms.evaluate(conn, wall)
    return start


def codes(conn, which="open"):
    return sorted(a["code"] for a in alarms.list_alarms(conn, which))


def one(conn, code):
    (a,) = [a for a in alarms.list_alarms(conn, "all") if a["code"] == code]
    return a


# --- the bits ---------------------------------------------------------------------------------

def test_alarm_bits_are_1_nasim_2_threshold_4_cusum():
    assert {n: 1 << b for n, b in ALARM_BITS.items()} == {"NASIM": 1, "THRESHOLD": 2, "CUSUM": 4}
    assert model.alarm_names(0) == [] and model.alarm_names(None) == []
    assert model.alarm_names(1) == ["NASIM"]
    assert model.alarm_names(6) == ["THRESHOLD", "CUSUM"]
    assert model.alarm_names(7) == ["NASIM", "THRESHOLD", "CUSUM"]


def test_status_decoding():
    assert model.status_names(33) == ["RTC_INVALID", "TROM_UNMAPPED"]
    assert model.status_names(None) == []
    assert model.status_names(1 << 14) == ["BIT14"]              # unknown bit: shown, not hidden


@pytest.mark.parametrize("value, expected", [
    (1, ["DET_NASIM"]), (2, ["DET_THRESHOLD"]), (4, ["DET_CUSUM"]),
    (6, ["DET_CUSUM", "DET_THRESHOLD"]), (7, ["DET_CUSUM", "DET_NASIM", "DET_THRESHOLD"])])
def test_each_detector_bit_raises_its_own_alarm(conn, value, expected):
    feed(conn, [{"alarm": 0}, {"alarm": value, "p": 0.99}, {"alarm": 0}])
    assert codes(conn) == expected


@pytest.mark.parametrize("bit, code", [
    ("RTC_INVALID", "RTC_INVALID"), ("TEMP_FAIL", "TEMP_FAIL"), ("AMBIENT_FAIL", "AMBIENT_FAIL"),
    ("IRRAD_FAIL", "IRRAD_FAIL"), ("PANEL_FAIL", "PANEL_FAIL"),
    ("TROM_UNMAPPED", "TROM_UNMAPPED"), ("DET_RESET", "DET_RESET"), ("SD_RETRY", "SD_ERROR")])
def test_each_status_bit_raises_its_own_alarm(conn, bit, code):
    feed(conn, [{"status": BIT[bit]}])
    assert codes(conn) == [code]


def test_det_skipped_alone_is_not_an_alarm(conn):
    feed(conn, [{"status": BIT["DET_SKIPPED"]}])
    assert codes(conn, "all") == []


def test_every_alarm_definition_names_a_real_bit_or_load_mode():
    for d in model.ALARM_DEFS:
        table = {"alarm": ALARM_BITS, "status": STATUS_BITS, "load_mode": model.LOAD_MODES}
        assert d.key in table[d.field], d.code
    assert len(model.ALARM_BY_CODE) == len(model.ALARM_DEFS)


def test_load_trip_from_the_load_mode_column(conn):
    feed(conn, [{"load_mode": "hold"}, {"load_mode": "trip"}, {"load_mode": "trip"}])
    assert codes(conn) == ["LOAD_TRIP"]
    feed(conn, [{"load_mode": "off"}])
    assert one(conn, "LOAD_TRIP")["state"] == "CLEARED_UNACKED"


# --- life cycle -------------------------------------------------------------------------------

def test_detector_alarm_raise_clear_acknowledge(conn):
    t = feed(conn, [{}, {"alarm": 1, "p": 0.991}, {}, {}])
    a = one(conn, "DET_NASIM")
    assert a["state"] == "CLEARED_UNACKED"                       # still in the alarm list
    assert (a["t_raised"], a["t_cleared"], a["n_rows"]) == (t + 10, t + 20, 1)
    assert "p 0.991" in a["detail"] and "T_module 40.00" in a["detail"]
    assert alarms.acknowledge(conn, a["id"], "asim", "seen", wall=500.0)
    a = one(conn, "DET_NASIM")
    assert (a["state"], a["acked_by"], a["ack_note"], a["acked_wall"]) == \
        ("CLEARED_ACKED", "asim", "seen", 500.0)
    assert codes(conn) == []                                     # left the list ...
    assert codes(conn, "all") == ["DET_NASIM"]                   # ... stays in the history
    events = [(j["event"], j["t_data"]) for j in reversed(alarms.journal(conn))]
    assert events == [("RAISED", t + 10), ("CLEARED", t + 20), ("ACKED", None)]


def test_acknowledging_an_active_alarm_keeps_it_listed_until_it_clears(conn):
    feed(conn, [{"status": BIT["IRRAD_FAIL"], "irradiance": None}])
    a = one(conn, "IRRAD_FAIL")
    assert a["state"] == "ACTIVE_UNACKED"
    assert alarms.acknowledge(conn, a["id"], "asim")
    assert one(conn, "IRRAD_FAIL")["state"] == "ACTIVE_ACKED" and codes(conn) == ["IRRAD_FAIL"]
    feed(conn, [{}, {}, {}])
    assert one(conn, "IRRAD_FAIL")["state"] == "CLEARED_ACKED" and codes(conn) == []


def test_a_second_acknowledgement_changes_nothing(conn):
    feed(conn, [{"alarm": 2}])
    a = one(conn, "DET_THRESHOLD")
    assert alarms.acknowledge(conn, a["id"], "asim", wall=1.0)
    assert not alarms.acknowledge(conn, a["id"], "someone else", wall=2.0)
    assert not alarms.acknowledge(conn, 9999, "asim")
    assert one(conn, "DET_THRESHOLD")["acked_by"] == "asim"
    assert [j["event"] for j in alarms.journal(conn)].count("ACKED") == 1


def test_a_fault_clears_only_after_three_good_rows_so_a_flickering_sensor_is_one_alarm(conn):
    bad = {"status": BIT["TEMP_FAIL"], "T3": None}
    feed(conn, [bad, {}, bad, {}, {}, bad, {}, {}])
    a = one(conn, "TEMP_FAIL")                                   # one occurrence, not three
    assert (a["state"], a["n_rows"]) == ("ACTIVE_UNACKED", 3)
    assert a["detail"] == "no value: T3"
    t = feed(conn, [{}])                                         # third good row in a row
    a = one(conn, "TEMP_FAIL")
    assert (a["state"], a["t_cleared"]) == ("CLEARED_UNACKED", t)


def test_the_clear_counter_survives_a_batch_boundary_and_a_restart(tmp_path):
    db = tmp_path / "h.sqlite"
    c = historian.connect(db)
    feed(c, [{"status": BIT["PANEL_FAIL"]}, {}, {}])
    c.commit()
    c.close()
    c = historian.connect(db)
    assert one(c, "PANEL_FAIL")["state"] == "ACTIVE_UNACKED"
    feed(c, [{}])
    assert one(c, "PANEL_FAIL")["state"] == "CLEARED_UNACKED"
    c.close()


def test_a_condition_that_returns_after_clearing_is_a_new_occurrence(conn):
    feed(conn, [{"alarm": 4}, {}, {"alarm": 4}, {}])
    assert codes(conn, "all") == ["DET_CUSUM", "DET_CUSUM"]
    assert alarms.acknowledge_all(conn, "asim") == 2
    assert codes(conn) == []
    assert alarms.summary(conn) == {"active": 0, "unacked": 0, "top_priority": None}


def test_summary_counts_and_list_order(conn):
    feed(conn, [{"alarm": 1, "status": BIT["TROM_UNMAPPED"] | BIT["SD_RETRY"]}])
    assert alarms.summary(conn) == {"active": 3, "unacked": 3, "top_priority": 1}
    listed = alarms.list_alarms(conn)
    assert [a["priority"] for a in listed] == [1, 2, 3]          # high priority first
    alarms.acknowledge(conn, listed[0]["id"], "asim")
    assert [a["code"] for a in alarms.list_alarms(conn)][-1] == "SD_ERROR"   # acked go last


# --- rows that cannot tell, rows without time, order ------------------------------------------

def test_rows_of_an_old_file_neither_raise_nor_clear_status_alarms(conn):
    feed(conn, [{"status": BIT["AMBIENT_FAIL"], "T_ambient": None}])
    feed(conn, [{"status": None, "load_mode": None}] * 5)        # status not recorded
    assert one(conn, "AMBIENT_FAIL")["state"] == "ACTIVE_UNACKED"
    feed(conn, [{"status": None, "load_mode": None, "alarm": 1}])
    assert codes(conn) == ["AMBIENT_FAIL", "DET_NASIM"]          # `alarm` is in old files too


def test_rows_are_evaluated_once_and_older_rows_that_arrive_later_raise_nothing(conn):
    feed(conn, [{"alarm": 1}, {}], start=T0)
    assert alarms.evaluate(conn) == 0                            # nothing new: nothing read
    feed(conn, [{"alarm": 2}], start=T0 - 500)                   # backfill behind the cursor
    assert codes(conn, "all") == ["DET_NASIM"]


def test_rows_without_any_time_raise_the_rtc_alarm_and_a_timed_good_row_clears_it(tmp_path, conn):
    blank = "," * 15 + f"0,NaN,none,0,{{}},{BIT['RTC_INVALID']}"
    header = ",".join(model.FORMAT_NEW)
    (tmp_path / "nodate.csv").write_text(
        "\n".join([header, blank.format(5000), blank.format(15000)]) + "\n")
    ingest = FolderIngest(conn, tmp_path)
    ingest.poll(wall=50.0)
    a = one(conn, "RTC_INVALID")
    assert (a["state"], a["t_raised"], a["raised_wall"]) == ("ACTIVE_UNACKED", None, 50.0)
    assert a["detail"].startswith("2 rows without any usable time")
    ingest.poll(wall=51.0)
    assert len(alarms.list_alarms(conn, "all")) == 1             # not raised again
    good = "2026-11-15,03:00:{:02d},20,21,22,23,24,25,40,800,17.9,5.1,91.3,NaN,,0,NaN,none,0,{},0"
    (tmp_path / "20261115.csv").write_text(
        "\n".join([header] + [good.format(10 * k, 900000 + 10000 * k) for k in range(3)]) + "\n")
    ingest.poll(wall=60.0)
    assert one(conn, "RTC_INVALID")["state"] == "CLEARED_UNACKED"


def test_rtc_alarm_from_rows_timed_by_uptime_spans_exactly_those_rows(tmp_path, conn):
    row = "{},20,21,22,23,24,25,40,800,17.9,5.1,91.3,NaN,,0,NaN,none,0,{},{}"
    lines = [",".join(model.FORMAT_NEW)]
    for k in range(10):
        lost = 3 <= k < 6
        lines.append(row.format(",", 5000 + 10000 * k, BIT["RTC_INVALID"]) if lost else
                     row.format(f"2026-11-15,03:00:{10 * k:02d}" if k < 6 else
                                f"2026-11-15,03:01:{10 * (k - 6):02d}", 5000 + 10000 * k, 0))
    (tmp_path / "20261115.csv").write_text("\n".join(lines) + "\n")
    FolderIngest(conn, tmp_path).poll()
    a = one(conn, "RTC_INVALID")
    t0 = conn.execute("SELECT MIN(t_utc) FROM samples").fetchone()[0]
    assert (a["t_raised"], a["t_last"], a["n_rows"], a["t_cleared"]) == \
        (t0 + 30, t0 + 50, 3, t0 + 80)
    assert a["detail"] == "time of the row taken from: uptime"
