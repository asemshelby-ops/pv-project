"""The logger's row logic (firmware/libraries/PvDaq/src/pv_log_logic.c) on the PC, with
FAKE sensor drivers that fail, time out or disappear on command
(firmware/tools/logger_sim.py). Task of 2026-11-21: a failed read never blocks or corrupts
a row, is retried at the next row, is counted and is reported once; the DS18B20 columns
follow the T_ROM table, never the bus search order.

What these tests show: the DECISIONS are right, whatever the drivers return. What they
cannot show: that the real drivers return within their time-outs on the real buses. That
is the on-board procedure (docs/paper/13-logger-robustness.md, "Needs board")."""
import ctypes
import datetime as dt
import importlib.util
import math
import pathlib
import random

import pytest

from data.schema import LOGGER_V2_COLUMNS, SENSOR_BITS, STATUS_BITS, decode_sensors

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("logger_sim", ROOT / "firmware/tools/logger_sim.py")
ls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ls)

pytestmark = pytest.mark.skipif(ls.find_gcc() is None, reason="no gcc")
ST = {name: 1 << bit for name, bit in STATUS_BITS.items()}
SF = {name: 1 << bit for name, bit in SENSOR_BITS.items()}
T_COLS = ["T1", "T2", "T3", "T4", "T5", "T6", "T7"]


def measured(row):
    """The fields that hold measurements (everything a failure may blank)."""
    return {c: row[c] for c in T_COLS + ["T_ambient", "humidity", "irradiance", "voltage",
                                         "current", "power", "pyr_mV"]}


# --- CSV row builder ---------------------------------------------------------------------------

def test_header_and_row_have_41_fields_and_only_append_to_the_old_format():
    sim = ls.Sim()
    header = sim.header().split(",")
    assert len(header) == 41 and header[21:] == LOGGER_V2_COLUMNS
    assert header[20] == "status" and header[0] == "date"
    sim.run(3)
    for line in sim.files["/20261125.csv"][2:]:
        assert line.count(",") == 40
    assert sim.files["/20261125.csv"][:2] == ["# nasim-logger-csv v2", sim.header()]


def test_exact_text_of_a_row():
    """Every field, in the format the logger has always used for the first 21."""
    so, L = ls.lib(), ls.Sim().L
    r = ls.Row()
    r.p, r.uptime_ms, r.unix_s, r.have_time = 0.123456789, 123456, ls.T0_UNIX + 3723, 1
    r.t[:] = [45.06251, 46.125, 47.5, 48.0, 49.9999, 30.25, 31.5]
    r.ta, r.rh, r.g, r.v, r.i, r.p_el = 30.126, 20.004, 800.4, 18.1236, 5.00004, 90.6172
    r.pyr_mv, r.d, r.alarm, r.sweep_id, r.status, r.sens_fail = 799.6, math.nan, 5, 0, 32, 0
    out = ctypes.create_string_buffer(400)
    n = so.pvlog_format_row(L, ctypes.byref(r), 7, 2, out, 400)
    assert out.value.decode() == (
        "2026-11-25,07:02:03,45.0625,46.1250,47.5000,48.0000,49.9999,30.13,20.00,800,"
        "18.1236,5.0000,90.6172,NaN,0.123457,5,NaN,none,0,123456,32,"
        "30.2500,31.5000,800,0,0,0,0,0,0,0,0,0,0,0,0,0,0,7,2,0")
    assert n == len(out.value)
    r.d, r.load_mode, r.sweep_id = 0.4612, b"hold", ls.T0_UNIX       # with the load fitted
    so.pvlog_format_row(L, ctypes.byref(r), 0, 0, out, 400)
    assert ",5,0.4612,hold,1795586400,123456,32," in out.value.decode()


def test_a_row_with_nothing_measured_keeps_every_comma():
    so, L = ls.lib(), ls.Sim().L
    r = ls.Row()
    r.p = r.ta = r.rh = r.g = r.v = r.i = r.p_el = r.pyr_mv = r.d = math.nan
    r.t[:] = [math.nan] * 7
    r.uptime_ms, r.status = 5000, 31
    out = ctypes.create_string_buffer(400)
    so.pvlog_format_row(L, ctypes.byref(r), 0, 0, out, 400)
    assert out.value.decode() == (",,,,,,,,,,,,,NaN,,0,NaN,none,0,5000,31,"
                                  ",,,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0")
    assert out.value.count(b",") == 40
    assert so.pvlog_format_row(L, ctypes.byref(r), 0, 0, out, 40) == 0   # too small: refused


@pytest.mark.parametrize("when", ["2025-01-01 00:00:00", "2026-11-25 06:00:00",
                                  "2027-12-31 23:59:59", "2028-02-29 12:34:56",
                                  "2028-03-01 00:00:00", "2099-12-31 23:59:59"])
def test_utc_date_text_matches_python(when):
    t = dt.datetime.fromisoformat(when).replace(tzinfo=dt.timezone.utc)
    out = ctypes.create_string_buffer(24)
    ls.lib().pvlog_utc_text(int(t.timestamp()), out, 24)
    assert out.value.decode() == when.replace(" ", ",")


def test_utc_date_text_over_many_days():
    so, out, rng = ls.lib(), ctypes.create_string_buffer(24), random.Random(1)
    for _ in range(3000):
        s = rng.randrange(1735689600, 4102444800)
        so.pvlog_utc_text(s, out, 24)
        t = dt.datetime.fromtimestamp(s, dt.timezone.utc)
        assert out.value.decode() == f"{t:%Y-%m-%d,%H:%M:%S}"


# --- DS18B20 columns by ROM ---------------------------------------------------------------------

def test_columns_follow_t_rom_whatever_the_bus_search_order():
    ref = ls.Sim().run(3)
    for seed in range(5):
        rig = ls.Rig()
        random.Random(seed).shuffle(rig.scan_order)
        rows = ls.Sim(rig).run(3)
        assert [[r[c] for c in T_COLS] for r in rows] == [[r[c] for c in T_COLS] for r in ref]
    assert [ref[0][c] for c in T_COLS] == [f"{40 + k:.4f}" for k in range(7)]


def test_t_rom_decides_the_column_not_the_position_on_the_bus():
    order = [3, 0, 6, 1, 5, 2, 4]                       # T1 <- probe 3, T2 <- probe 0, ...
    rows = ls.Sim(t_rom=[ls.rom(k) for k in order]).run(2)
    assert [rows[1][c] for c in T_COLS] == [f"{40 + k:.4f}" for k in order]


@pytest.mark.parametrize("missing", range(7))
def test_a_missing_probe_empties_only_its_own_column(missing):
    rig = ls.Rig()
    rig.probes[ls.rom(missing)]["present"] = False
    row = ls.Sim(rig).run(2)[1]
    for k, c in enumerate(T_COLS):
        assert row[c] == ("" if k == missing else f"{40 + k:.4f}")
    assert int(row["sens_fail"]) == 1 << missing and row[f"f_T{missing + 1}"] == "2"
    assert int(row["status"]) == (ST["TEMP_FAIL"] if missing < 5 else 0)   # T6, T7: no detector


def test_a_probe_with_a_bad_reading_is_empty_too():
    rig = ls.Rig()
    rig.probes[ls.rom(4)]["fail"] = True                 # answers, but CRC wrong / 85.0
    row = ls.Sim(rig).run(1)[0]
    assert row["T5"] == "" and row["T4"] == "43.0000" and row["T6"] == "45.0000"


def test_a_t_rom_row_left_at_zero_is_an_empty_column_and_is_not_counted():
    t_rom = [ls.rom(0), ls.rom(1), None, ls.rom(3), ls.rom(4), None, ls.rom(6)]
    sim = ls.Sim(t_rom=t_rom)
    row = sim.run(4)[3]
    assert [row[c] for c in T_COLS] == ["40.0000", "41.0000", "", "43.0000", "44.0000", "",
                                        "46.0000"]
    assert int(row["status"]) == ST["TROM_UNMAPPED"] | ST["TEMP_FAIL"]   # T3 has no ROM
    assert row["sens_fail"] == "0" and row["f_T3"] == row["f_T6"] == "0"   # not "failing"
    # probes 2 and 5 are on the bus but in no row: each is reported once
    unknown = [d for e, d in sim.events() if e == "unknown_rom"]
    assert len(unknown) == 2 and ls.rom_text(ls.rom(2)) in unknown[0]


def test_with_t_rom_empty_no_column_is_filled_from_the_search_order():
    sim = ls.Sim(t_rom=[None] * 7)
    row = sim.run(2)[1]
    assert all(row[c] == "" for c in T_COLS)
    assert int(row["status"]) & ST["TROM_UNMAPPED"]
    assert len([e for e, _ in sim.events() if e == "unknown_rom"]) == 7


def test_an_unknown_rom_is_reported_once_however_often_it_is_seen():
    rig = ls.Rig()
    stranger = bytes([0x28, 0x01, 0x02, 0x03, 0x04, 0x00, 0x00, 0x99])
    rig.probes[stranger] = {"present": True, "t": 20.0, "fail": False}
    rig.scan_order.insert(2, stranger)
    sim = ls.Sim(rig, scan_every=1)                      # a bus scan at every row
    rows = sim.run(20)
    assert sim.events() == [("unknown_rom", "28-01-02-03-04-00-00-99 is on the bus but in no "
                                            "T_ROM row: not logged")]
    assert [rows[5][c] for c in T_COLS] == [f"{40 + k:.4f}" for k in range(7)]   # nothing moved
    # a second stranger later on: one more line, not a repeat of the first
    other = bytes([0x28, 0xEE, 0x02, 0x03, 0x04, 0x00, 0x00, 0x98])
    rig.probes[other] = {"present": True, "t": 21.0, "fail": False}
    rig.scan_order.append(other)
    sim.run(10)
    assert [d[:23] for _, d in sim.events()] == [ls.rom_text(stranger), ls.rom_text(other)]


def test_a_probe_listed_as_known_is_not_reported():
    rig = ls.Rig()
    heatsink = bytes([0x28, 0x55, 0x02, 0x03, 0x04, 0x00, 0x00, 0x77])
    rig.probes[heatsink] = {"present": True, "t": 60.0, "fail": False}
    rig.scan_order.append(heatsink)
    sim = ls.Sim(rig, known=[heatsink], scan_every=1)
    sim.run(5)
    assert sim.events() == []


def test_more_unknown_roms_than_the_list_holds_are_summarised_once():
    rig = ls.Rig()
    for k in range(12):
        r = bytes([0x28, k, 0xAA, 0x03, 0x04, 0x00, 0x00, k])
        rig.probes[r] = {"present": True, "t": 20.0, "fail": False}
        rig.scan_order.append(r)
    rig.scan_order = rig.scan_order[7:] + rig.scan_order[:7]    # the strangers come first
    sim = ls.Sim(rig, scan_every=1)
    sim.run(8)
    events = [e for e in sim.events() if e[0] == "unknown_rom"]
    assert len(events) == 9 and events[-1][1].startswith("more unknown ROMs")


def test_the_same_rom_in_two_t_rom_rows_is_reported_at_boot():
    sim = ls.Sim(t_rom=[ls.rom(0), ls.rom(1), ls.rom(1), ls.rom(3), ls.rom(4), None, None])
    sim.run(1)
    assert ("config", "T_ROM rows T2 and T3 hold the same ROM") in sim.events()


# --- retry and fail counters -------------------------------------------------------------------

DEVICE = {  # fake device -> (sensor name, status bit, fields it owns)
    "rtc": ("rtc", "RTC_INVALID", ["date", "time"]),
    "env": ("env", "AMBIENT_FAIL", ["T_ambient", "humidity"]),
    "pyr": ("pyr", "IRRAD_FAIL", ["irradiance"]),
    "ina": ("ina", "PANEL_FAIL", ["voltage", "current", "power"]),
    "hub": ("hub", "PANEL_FAIL", ["voltage", "current", "power"]),
}


@pytest.mark.parametrize("name", DEVICE)
@pytest.mark.parametrize("how", ["present", "fail"])
def test_one_failing_device_empties_only_its_own_fields(name, how):
    sensor, bit, fields = DEVICE[name]
    ref = ls.Sim().run(3)[2]
    rig = ls.Rig()
    if how == "present":
        getattr(rig, name).present = False               # unplugged
    else:
        getattr(rig, name).fail = True                   # answers with a read error
    row = ls.Sim(rig).run(3)[2]
    for col in ref:
        if col in fields:
            assert row[col] == "", col
        elif col in ("status", "sens_fail", f"f_{sensor}", "cycle_ms", "_path"):
            continue
        else:
            assert row[col] == ref[col], col             # nothing else moved or changed
    assert int(row["status"]) == ST[bit]
    assert decode_sensors(int(row["sens_fail"])) == [sensor] and row[f"f_{sensor}"] == "3"


def test_a_failed_sensor_is_started_and_read_again_at_the_very_next_row():
    rig = ls.Rig()
    sim = ls.Sim(rig)
    sim.run(2)
    assert (rig.env.begins, rig.env.reads) == (1, 2)     # started once, then only read
    rig.env.fail = True
    row = sim.cycle()                                    # row 2: the read fails
    assert row["T_ambient"] == "" and (rig.env.begins, rig.env.reads) == (1, 3)
    rig.env.fail = False
    row = sim.cycle()                                    # row 3: begin() again, then read
    assert row["T_ambient"] == "30.00" and (rig.env.begins, rig.env.reads) == (2, 4)
    assert row["f_env"] == "1" and row["sens_fail"] == "0"   # one bad read cost one row


def test_an_absent_device_is_tried_once_per_row_never_in_a_loop():
    rig = ls.Rig()
    for d in (rig.rtc, rig.env, rig.hub, rig.pyr):
        d.present = False
    sim = ls.Sim(rig)
    sim.run(10)
    for d in (rig.rtc, rig.env, rig.hub, rig.pyr):
        assert (d.begins, d.reads) == (10, 0)            # one begin() per row, no read
    assert (rig.ina.begins, rig.ina.reads) == (0, 0)     # behind the absent PaHub: not tried


def test_counters_and_the_one_report():
    """6 failed rows: the counter counts each, the event is written once (at the 3rd) and
    once more when the sensor is back."""
    sim = ls.scenario_probe_missing()
    assert [r["f_T3"] for r in sim.rows] == ["0", "0", "0", "1", "2", "3", "4", "5", "6",
                                             "6", "6", "6"]
    assert [r["sens_fail"] for r in sim.rows] == ["0"] * 3 + ["4"] * 6 + ["0"] * 3
    assert [r["T3"] for r in sim.rows] == ["42.0000"] * 3 + [""] * 6 + ["42.0000"] * 3
    assert sim.events() == [
        ("sensor_fail", "T3 failed 3 rows in a row: no valid reading from its ROM"),
        ("sensor_ok", "T3 back after 6 failed rows")]
    assert sim.files["/events.csv"][1].startswith("2026-11-25,06:00:5")   # at the 3rd bad row
    for r in sim.rows:                                   # the other six columns never moved
        assert [r[c] for c in T_COLS if c != "T3"] == ["40.0000", "41.0000", "43.0000",
                                                       "44.0000", "45.0000", "46.0000"]
    assert sum(1 for l in sim.rig.serial if "sensor_fail" in l) == 1


def test_a_failure_shorter_than_n_rows_is_counted_but_not_reported():
    sim = ls.Sim()
    sim.run(8, lambda k, rig: setattr(rig.pyr, "fail", k in (3, 4)))
    assert sim.rows[-1]["f_pyr"] == "2" and sim.events() == []


def test_a_sensor_that_comes_and_goes_is_reported_once_per_outage():
    sim = ls.Sim()
    sim.run(30, lambda k, rig: setattr(rig.env, "present", not (3 <= k < 8 or 15 <= k < 19)))
    assert [e for e, _ in sim.events()] == ["sensor_fail", "sensor_ok", "sensor_fail",
                                            "sensor_ok"]
    assert sim.rows[-1]["f_env"] == "9"
    assert sim.get("run", "env") == 0 and sim.get("reported", "env") == 0


def test_the_report_threshold_is_the_configured_n():
    sim = ls.Sim(fail_report_after=5)
    sim.run(10, lambda k, rig: setattr(rig.env, "present", k < 2))
    assert sim.events() == [("sensor_fail", "env failed 5 rows in a row: no answer")]
    assert sim.files["/events.csv"][1].startswith("2026-11-25,06:01:0")   # row 6 = 5th failure


def test_pahub_absent_the_ina226_is_not_counted_and_is_started_again_afterwards():
    sim = ls.scenario_pahub_absent()
    rows = sim.rows
    assert [r["voltage"] for r in rows] == ["18.0000"] * 3 + [""] * 6 + ["18.0000"] * 3
    assert [r["status"] for r in rows] == ["0"] * 3 + ["16"] * 6 + ["0"] * 3     # PANEL_FAIL
    assert [r["sens_fail"] for r in rows] == ["0"] * 3 + ["512"] * 6 + ["0"] * 3  # hub only
    assert rows[-1]["f_hub"] == "6" and rows[-1]["f_ina"] == "0"
    for r in rows:                                       # the main-bus sensors are untouched
        assert r["T_ambient"] == "30.00" and r["time"] != "" and r["T3"] == "42.0000"
    assert sim.events() == [("sensor_fail", "hub failed 3 rows in a row: no answer"),
                            ("sensor_ok", "hub back after 6 failed rows")]
    # the INA226 lost power with the PaHub: begin() (calibration) again before its next read
    assert sim.rig.ina.begins == 2 and sim.rig.ina.reads == 6


def test_every_sensor_gone_still_gives_a_complete_row_every_10_s():
    rig = ls.Rig()
    for d in rig.dev.values():
        d.present = False
    for p in rig.probes.values():
        p["present"] = False
    sim = ls.Sim(rig)
    rows = sim.run(12)
    assert len(rows) == 12
    for k, r in enumerate(rows):
        assert all(v == "" for v in list(measured(r).values())[:-1])      # all but pyr_mV (ADC)
        assert r["date"] == r["time"] == "" and r["uptime_ms"] == str(5000 + 10000 * k)
        assert int(r["status"]) == (ST["RTC_INVALID"] | ST["TEMP_FAIL"] | ST["AMBIENT_FAIL"]
                                    | ST["IRRAD_FAIL"] | ST["PANEL_FAIL"])
        assert r["_path"] == "/nodate.csv"
    assert rows[-1]["sens_fail"] == str(sum(SF[n] for n in SENSOR_BITS if n not in ("ina", "sd")))
    assert sorted(d.split()[0] for e, d in sim.events() if e == "sensor_fail") == sorted(
        n for n in SENSOR_BITS if n not in ("ina", "sd"))                 # each reported once


def test_the_event_queue_holds_every_sensor_failing_in_the_same_row():
    rig = ls.Rig()
    for d in rig.dev.values():
        d.present = False
    for p in rig.probes.values():
        p["present"] = False
    sim = ls.Sim(rig, fail_report_after=1)               # 11 events in the first row
    sim.run(2)
    assert [e for e, _ in sim.events()] == ["sensor_fail"] * 11


def test_a_full_event_queue_counts_what_it_cannot_keep_and_never_blocks():
    sim = ls.Sim()
    for k in range(21):                                  # the queue holds 16
        sim.so.pvlog_event(sim.L, b"note", f"number {k}".encode())
    sim.run(1)
    events = sim.events()
    assert [d for e, d in events if e == "note"] == [f"number {k}" for k in range(16)]
    assert events[-1] == ("events_missed", "5 events not listed (queue full)")


# --- bounded time ---------------------------------------------------------------------------------

def test_slowest_possible_row_is_the_sum_of_the_driver_limits_and_below_the_period():
    """Every device at its worst at once, row after row: begin() answers as slowly as the
    driver allows and the read then runs into its time-out; the 1-Wire line is held low.
    The logic calls each driver at most once per row (one begin, one read), so the row can
    not take longer than the sum of the limits. The limits are [EST] from the drivers'
    constants (firmware/tools/logger_sim.py Rig; table in docs/paper/13 section 2): this
    proves the row is BOUNDED and what bounds it, not how long a real row takes."""
    rig = ls.Rig()
    for d in rig.dev.values():
        d.slow = True
    rig.bus_shorted = True
    rig.ds_read_ms, rig.scan_ms = 30, 85                 # 7 reads; a scan of ~0.6 s
    sim = ls.Sim(rig, scan_every=1)
    sim.run(8)
    limit = (sum(d.begin_max_ms + d.read_max_ms for d in rig.dev.values())
             + 750 + 7 * 30 + 7 * 85 + 4 + 30)           # 1-Wire, ADC, one SD write
    assert 6000 < limit < 7000
    assert 4000 < max(sim.cycle_ms) <= limit < 10000     # below the period ...
    assert limit < 30000 / 4                             # ... and far below the watchdog
    for d in rig.dev.values():
        assert d.begins <= 8 and d.reads <= 8            # never twice in one row
    assert [int(r["uptime_ms"]) for r in sim.rows] == [5000 + 10000 * k for k in range(8)]
    assert [int(r["cycle_ms"]) for r in sim.rows[1:]] == sim.cycle_ms[:-1]   # logged per row
    assert all(measured(r)["voltage"] == "" and r["T1"] == "" for r in sim.rows)


def test_cycle_ms_reports_the_row_before():
    sim = ls.Sim()
    rows = sim.run(4)
    assert rows[0]["cycle_ms"] == "0" and int(rows[1]["cycle_ms"]) == sim.cycle_ms[0]
    assert 800 < int(rows[2]["cycle_ms"]) < 1200         # 750 ms conversion + the rest [EST]


# --- time ------------------------------------------------------------------------------------------

def test_rtc_not_set_gives_rows_without_time_in_nodate_csv():
    rig = ls.Rig()
    rig.rtc_valid = False
    sim = ls.Sim(rig)
    rows = sim.run(4)
    assert all(r["date"] == "" and int(r["status"]) == ST["RTC_INVALID"] for r in rows)
    assert all(r["_path"] == "/nodate.csv" for r in rows) and rows[-1]["f_rtc"] == "4"
    assert ("sensor_fail", "rtc failed 3 rows in a row: time not set (run firmware/tools/"
                           "set_rtc.py)") in sim.events()


def test_a_row_without_time_goes_to_the_file_of_the_last_row_that_had_one():
    sim = ls.Sim()
    rows = sim.run(6, lambda k, rig: setattr(rig.rtc, "present", k not in (2, 3)))
    assert [r["_path"] for r in rows] == ["/20261125.csv"] * 6
    assert [r["time"] for r in rows] == ["06:00:00", "06:00:10", "", "", "06:00:40", "06:00:50"]


def test_rows_after_utc_midnight_go_to_the_next_day_file():
    rig = ls.Rig()
    rig.rtc_offset_s = 18 * 3600 - 20                    # 23:59:40 UTC at row 0
    sim = ls.Sim(rig)
    rows = sim.run(4)
    assert [r["_path"] for r in rows] == ["/20261125.csv"] * 2 + ["/20261126.csv"] * 2
    assert [(r["date"], r["time"]) for r in rows[1:3]] == [("2026-11-25", "23:59:50"),
                                                           ("2026-11-26", "00:00:00")]
    assert sim.files["/20261126.csv"][:2] == ["# nasim-logger-csv v2", sim.header()]


def test_one_wrong_rtc_read_is_refused_and_does_not_create_a_stray_day_file():
    sim = ls.Sim()

    def plan(k, rig):
        rig.rtc_offset_s = 400 * 86400 if k == 3 else 0  # one read 400 days off
    rows = sim.run(6, plan)
    assert [r["time"] for r in rows] == ["06:00:00", "06:00:10", "06:00:20", "", "06:00:40",
                                         "06:00:50"]
    assert int(rows[3]["status"]) == ST["RTC_INVALID"] and rows[3]["sens_fail"] == str(SF["rtc"])
    assert set(sim.files) == {"/20261125.csv"}           # no file for the wrong date


def test_a_real_change_of_the_rtc_time_is_accepted_at_the_second_read():
    sim = ls.Sim()

    def plan(k, rig):
        rig.rtc_offset_s = 3600 if k >= 3 else 0         # the clock really moved by 1 h
    rows = sim.run(6, plan)
    assert [r["time"] for r in rows] == ["06:00:00", "06:00:10", "06:00:20", "", "07:00:40",
                                         "07:00:50"]
    assert ("rtc_jump", "RTC time moved by 3600 s and was confirmed by a second read") \
        in sim.events()


def test_an_rtc_time_out_of_range_is_not_used():
    rig = ls.Rig()
    rig.rtc_offset_s = -(ls.T0_UNIX - 946684800)         # 2000-01-01: a clock never set
    rows = ls.Sim(rig).run(2)
    assert rows[1]["date"] == "" and rows[1]["_path"] == "/nodate.csv"


def test_millis_wrapping_after_49_7_days_changes_nothing():
    rig = ls.Rig()
    start = (1 << 32) - 25000                            # the counter wraps before row 3
    rig.ms = start
    sim = ls.Sim(rig)
    sim.n = (start - ls.BOOT_MS) // ls.PERIOD_MS
    rig.ms = ls.BOOT_MS + sim.n * ls.PERIOD_MS
    rows = sim.run(6)
    up = [int(r["uptime_ms"]) for r in rows]
    wrap = next(k for k in range(1, 6) if up[k] < up[k - 1])
    assert up[wrap - 1] > 4.29e9 and up[wrap] < 10000    # the counter started again at 0
    assert all(r["time"] != "" and r["status"] == "0" for r in rows)   # no time jump seen
    assert all(0 < int(r["cycle_ms"]) < 2000 for r in rows[1:])
    sim.run(1, lambda k, rig: setattr(rig.env, "fail", True))
    line = ctypes.create_string_buffer(400)
    sim.so.pvlog_event(sim.L, b"test", b"after the wrap")
    assert sim.so.pvlog_next_event(sim.L, rig.ms & 0xFFFFFFFF, line, 400)
    assert line.value.decode().startswith(rows[-1]["date"])            # events keep their time


def test_event_text_cannot_break_the_csv():
    so, sim = ls.lib(), ls.Sim()
    so.pvlog_event(sim.L, b"note", b"a,b\nc\td" + b"x" * 300)
    line = ctypes.create_string_buffer(400)
    assert so.pvlog_next_event(sim.L, 1234, line, 400) == 1
    fields = line.value.decode().split(",")
    assert fields[:4] == ["", "", "1234", "note"] and len(fields) == 5
    assert fields[4].startswith("a;b c d") and len(fields[4]) < 128
    assert so.pvlog_next_event(sim.L, 1234, line, 400) == 0


# --- the files as the analysis reads them -----------------------------------------------------------

def test_the_simulated_card_loads_with_the_analysis_loader(tmp_path):
    from analysis import ingest
    sim = ls.scenario_probe_missing()
    for path, lines in sim.files.items():
        (tmp_path / path.lstrip("/")).write_text("\r\n".join(lines) + "\r\n", newline="")
    df = ingest.read_logger(tmp_path)
    assert len(df) == 12 and (df["csv_version"] == 2).all() and df.attrs["rows_malformed"] == 0
    assert df["T3"].isna().sum() == 6 and df["T2"].notna().all()
    assert list(df["f_T3"]) == [0, 0, 0, 1, 2, 3, 4, 5, 6, 6, 6, 6]
    assert decode_sensors(df["sens_fail"].iloc[4]) == ["T3"]
