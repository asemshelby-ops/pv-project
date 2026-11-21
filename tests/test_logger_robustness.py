"""The logger's row logic (firmware/libraries/PvDaq/src/pv_log_logic.c) and its storage
logic (pv_store_logic.c) on the PC, with FAKE sensor drivers that fail, time out or
disappear on command, a FAKE SD card that can be pulled and a FAKE internal flash
(firmware/tools/logger_sim.py). Task of 2026-11-21: a failed read never blocks or corrupts
a row, is retried at the next row, is counted and is reported once; the DS18B20 columns
follow the T_ROM table, never the bus search order; without the card the rows wait in a
LittleFS ring and reach the card later, in order, each exactly once.

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
    sensors = (sum(d.begin_max_ms + d.read_max_ms for d in rig.dev.values())
               + 750 + 7 * 30 + 7 * 85 + 4)              # 1-Wire and the ADC
    assert 6000 < sensors < 7000
    limit = sensors + 30 + 13 * (30 + 5)                 # the row, and one event per sensor
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


# =================================================================================================
# Task 2: the card is missing or fails -> LittleFS ring -> back to the card, in order, once
# =================================================================================================

def uptimes(sim, path="/20261125.csv"):
    return [int(r["uptime_ms"]) for r in sim.data_rows(path)]


def grid(n, first=0):
    return [5000 + 10000 * k for k in range(first, first + n)]


def test_sd_missing_for_12_rows_then_back_nothing_lost_nothing_twice():
    sim = ls.scenario_sd_missing()                       # out before row 3, back before row 15
    assert [r["_where"] for r in sim.rows] == ["sd"] * 3 + ["ring"] * 12 + ["sd"] * 5
    assert uptimes(sim) == grid(20)                      # all 20 on the card, in order, once
    card = sim.data_rows("/20261125.csv")
    assert [int(r["status"]) for r in card] == [0] * 3 + [ST["SD_RETRY"] | ST["SD_BUFFERED"]] * 12 \
        + [0] * 5
    assert [r["sens_fail"] for r in card] == ["0"] * 3 + [str(SF["sd"])] * 12 + ["0"] * 5
    assert [r["f_sd"] for r in card][2:16] == [str(k) for k in range(0, 13)] + ["12"]
    assert card[-1]["ring_rows"] == "0" and card[-1]["ring_dropped"] == "0"
    assert sim.rig.flash == {} and sim.store("waiting") == 0     # the ring is empty again
    assert [e for e, _ in sim.events()] == ["sd_lost", "sensor_fail", "sensor_ok", "sd_back",
                                            "ring_flushed"]
    assert sim.events()[-1][1] == "14 lines copied from the buffer to the card in order; 0 given up"
    # the rows on the card are, text for text, the rows the logger made (and printed)
    made = [",".join(r[c] for c in sim.header().split(",")) for r in sim.rows]
    assert [l for l in sim.files["/20261125.csv"] if l[:2] == "20"] == made
    assert sim.files["/20261125.csv"].count(sim.header()) == 1   # one boot, one header block


def test_the_card_is_tried_once_per_row_while_it_is_away():
    sim = ls.scenario_sd_missing()
    assert sim.rig.mounts == 1 + 12 + 1                  # boot, one per missing row, the return
    out = sim.cycle_ms[4:14]                             # rows made while the card was out
    assert max(out) < 912 + sim.rig.sd_mount_fail_ms + 200       # a row + ONE failed mount [EST]


def test_no_card_at_boot_then_inserted():
    rig = ls.Rig()
    rig.card_in = False
    sim = ls.Sim(rig)
    sim.run(8, lambda k, r: setattr(r, "card_in", k >= 5))
    assert [r["_where"] for r in sim.rows] == ["ring"] * 5 + ["sd"] * 3
    assert uptimes(sim) == grid(8)
    assert sim.files["/20261125.csv"][:2] == ["# nasim-logger-csv v2", sim.header()]
    assert sim.events()[0] == ("sd_lost", "no card at boot: lines go to the LittleFS buffer")


def test_one_failed_write_gets_one_remount_and_a_second_attempt():
    sim = ls.Sim()
    sim.run(6, lambda k, r: setattr(r, "card_fail_appends", 1 if k == 3 else 0))
    assert [r["_where"] for r in sim.rows] == ["sd"] * 3 + ["sd_retry"] + ["sd"] * 2
    card = sim.data_rows("/20261125.csv")
    assert uptimes(sim) == grid(6)
    assert [int(r["status"]) for r in card] == [0, 0, 0, ST["SD_RETRY"], 0, 0]
    assert card[3]["sens_fail"] == str(SF["sd"]) and card[5]["f_sd"] == "1"
    assert sim.rig.flash == {}                           # the ring was not needed


def test_a_write_that_reports_failure_but_reached_the_card_is_not_written_twice():
    """The worst case for "exactly once": the bytes are on the card, the card says error."""
    sim = ls.Sim()
    sim.run(6, lambda k, r: setattr(r, "card_ghost_appends", 1 if k == 3 else 0))
    assert uptimes(sim) == grid(6)                       # row 3 once, not twice
    assert sim.store("n_dedup") == 1 and sim.rows[3]["_where"] == "sd_retry"
    # ... and the same when the second attempt cannot follow at once: the card is gone
    # right after that write and comes back five rows later
    sim = ls.Sim()
    for k in range(12):
        if k == 3:
            sim.rig.card_ghost_appends = 1               # row 3 lands, the card reports failure
            sim.rig.sd_mount_ok = False
        sim.rig.card_in = not (3 < k < 9)
        if k == 3:                                       # the re-mount of this row fails too
            real, sim.rig.sd_mount = sim.rig.sd_mount, lambda ctx: 0
            sim.sio = sim.rig.store_io()
            sim.cycle()
            sim.rig.sd_mount = real
            sim.sio = sim.rig.store_io()
        else:
            sim.cycle()
    assert sim.rows[3]["_where"] == "ring" and sim.store("n_dedup") == 1
    assert uptimes(sim) == grid(12)                      # row 3 is on the card once


def test_a_half_written_line_cannot_join_the_next_row():
    sim = ls.Sim()
    sim.run(6, lambda k, r: setattr(r, "card_torn_appends", 1 if k == 3 else 0))
    lines = sim.files["/20261125.csv"]
    whole = [l for l in lines if l.count(",") == 40 and l[:2] == "20"]
    assert [int(l.split(",")[19]) for l in whole] == grid(6)     # every row whole, once
    torn = [l for l in lines if l[:2] == "20" and l.count(",") != 40]
    assert len(torn) == 1 and whole[3].startswith(torn[0])       # the fragment stands alone


def test_new_rows_wait_behind_the_backlog_so_the_file_stays_in_order():
    rig = ls.Rig()
    sim = ls.Sim(rig)
    sim.run(200, lambda k, r: setattr(r, "card_in", not 5 <= k < 160))   # 155 rows buffered
    assert uptimes(sim) == grid(200)                     # strictly in order, none missing
    back = [r["_where"] for r in sim.rows[160:]]
    assert back.count("ring") >= 3 and back[-1] == "sd"  # rows made during the flush waited
    assert "sd" not in back[:back.index("sd")] and set(back[back.index("sd"):]) == {"sd"}


def test_the_flush_is_bounded_per_row():
    rig = ls.Rig()
    sim = ls.Sim(rig)
    sim.run(160, lambda k, r: setattr(r, "card_in", not 5 <= k < 160))
    rig.card_in = True
    before = len(rig.card_lines("/20261125.csv"))
    sim.cycle()
    copied = len(rig.card_lines("/20261125.csv")) - before
    assert 10 <= copied <= 64                            # FLUSH_MAX, and the 1 s budget
    assert sim.cycle_ms[-1] < 912 + 1000 + 64 * 5 + 200  # row + budget + the last write [EST]
    assert sim.store("waiting") > 60                     # the rest goes with the next rows


def test_ring_full_the_oldest_are_given_up_and_counted():
    rig = ls.Rig()
    rig.convert_ms = 0                                   # (only to make 4300 rows run fast)
    sim = ls.Sim(rig, scan_every=10 ** 6)
    n_out = 4300                                         # the ring holds 128 x 32 = 4096 lines
    sim.run(3 + n_out, lambda k, r: setattr(r, "card_in", k < 3))
    assert sim.store("segments") == 128 and sim.store("waiting") <= 4096
    dropped = sim.store("dropped")
    assert dropped > 0 and dropped % 32 == 0             # whole segments, oldest first
    assert int(sim.rows[-1]["ring_dropped"]) == dropped  # in the row itself
    assert len(rig.flash) == 128
    rig.card_in = True
    sim.run(200)
    assert sim.store("waiting") == 0 and rig.flash == {}
    up = uptimes(sim)
    assert up == sorted(up) and len(set(up)) == len(up)  # in order, nothing twice
    lost = sorted(set(grid(3 + n_out + 200)) - set(up))
    assert lost == grid(len(lost), first=3)              # the lost rows are the OLDEST of the outage
    events = sim.events()
    assert [e for e, _ in events].count("ring_full") == 1
    flushed = [d for e, d in events if e == "ring_flushed"][0]
    assert flushed.endswith(f"; {dropped} given up")
    assert len(lost) <= dropped                          # the count also covers event lines


def test_flash_full_before_the_ring_is_gives_up_the_oldest_too():
    rig = ls.Rig()
    rig.flash_capacity = 40 * 260                        # room for about 40 lines only
    sim = ls.Sim(rig)
    sim.run(120, lambda k, r: setattr(r, "card_in", k < 3 or k >= 100))
    up = uptimes(sim)
    assert up == sorted(up) and len(set(up)) == len(up)
    assert up[:3] == grid(3) and up[-20:] == grid(20, first=100)
    assert grid(1, first=99)[0] in up                    # the newest of the outage were kept
    lost = set(grid(120)) - set(up)
    assert 0 < len(lost) < 97 and max(lost) < min(up[3:])        # the oldest were given up
    assert sum("ring_full" in l for l in rig.serial) == 1        # said once (on Serial; its
    flushed = [d for e, d in sim.events() if e == "ring_flushed"][0]   # own line was given up)
    assert int(flushed.split("; ")[1].split()[0]) >= len(lost)


def test_flash_unusable_rows_are_counted_as_lost_and_logging_goes_on():
    rig = ls.Rig()
    rig.flash_ok = False
    sim = ls.Sim(rig)
    sim.run(10, lambda k, r: setattr(r, "card_in", not 3 <= k < 6))
    assert [r["_where"] for r in sim.rows] == ["sd"] * 3 + ["LOST"] * 3 + ["sd"] * 4
    card = sim.data_rows("/20261125.csv")
    assert uptimes(sim) == grid(3) + grid(4, first=6)
    assert int(card[3]["status"]) == ST["SD_RETRY"]      # "the row(s) before did not reach the card"
    assert card[3]["f_sd"] == "3" and int(card[3]["ring_dropped"]) >= 3
    events = [e for e, _ in sim.events()]
    assert "ring_error" in events


def test_a_restart_while_rows_wait_in_the_ring_loses_nothing():
    rig = ls.Rig()
    sim = ls.Sim(rig)
    sim.run(8, lambda k, r: setattr(r, "card_in", k < 3))        # 5 rows in the ring
    assert sim.store("waiting") >= 5
    first = uptimes(sim) if "/20261125.csv" in rig.card else []
    rig.reboot()                                         # watchdog or power: RAM is gone
    sim2 = ls.Sim(rig)
    assert sim2.store("waiting") == sim.store("waiting")         # found on the flash
    sim2.run(3)                                          # still no card
    rig.card_in = True
    sim2.run(3)
    rows = sim2.data_rows("/20261125.csv")
    up = [int(r["uptime_ms"]) for r in rows]
    assert up == grid(8) + grid(6)                       # boot 1 complete, then boot 2: in order
    times = [r["time"] for r in rows]
    assert times == sorted(times) and len(set(times)) == 14
    assert first == grid(3)
    assert ("ring_found", f"{sim.store('waiting')} lines from before the restart wait in the "
                          f"LittleFS buffer") in sim2.events()
    assert rig.flash == {}


def test_a_power_cut_between_the_card_write_and_the_saved_place_gives_no_duplicate():
    """Power goes after a waiting line reached the card and before its new place was saved
    on the flash: after the restart the saved place still points at that line. The end
    of the file is read first, the line is found there and is not written again."""
    rig = ls.Rig()
    sim = ls.Sim(rig)
    sim.run(10, lambda k, r: setattr(r, "card_in", k < 3))       # 7 rows (+ events) wait
    rig.card_in = True
    real_save, saves = rig.fs_save, []

    def save_then_cut(ctx, text):                        # the 4th save is the one that is lost:
        saves.append(text)
        if len(saves) == 4:
            rig.card_in = False                          # ... power goes: nothing after it
            return 0
        return real_save(ctx, text)
    rig.fs_save = save_then_cut
    sim.sio = rig.store_io()
    sim.so.pvstore_service(sim.S, ctypes.byref(sim.sio))
    written = len(rig.card_lines("/20261125.csv")) + len(rig.card_lines("/events.csv"))
    rig.fs_save = real_save
    assert saves[3] != rig.flash_state                   # the flash holds the place before it
    rig.reboot()
    rig.card_in = True
    sim2 = ls.Sim(rig)
    sim2.run(4)
    assert sim2.store("n_dedup") == 1                    # found at the end of its file
    up = uptimes(sim2)
    assert up == grid(10) + grid(4)                      # every row once, in order
    events = rig.card_lines("/events.csv")
    assert len(events) == len(set(events)) and written > 4


def test_markers_and_events_wait_in_the_ring_too_and_keep_their_order():
    sim = ls.Sim()
    sim.run(3)
    sim.marker("2026-11-25,06:00:21.500,26500")
    sim.rig.card_in = False
    sim.run(2)
    sim.marker("2026-11-25,06:00:41.250,46250")          # pressed while the card is out
    sim.run(2)
    sim.marker("2026-11-25,06:01:01.000,66000")
    sim.rig.card_in = True
    sim.run(2)
    assert sim.files["/markers.csv"] == ["date,time,millis", "2026-11-25,06:00:21.500,26500",
                                         "2026-11-25,06:00:41.250,46250",
                                         "2026-11-25,06:01:01.000,66000"]
    assert uptimes(sim) == grid(9)


def test_an_outage_over_utc_midnight_fills_both_day_files():
    rig = ls.Rig()
    rig.rtc_offset_s = 18 * 3600 - 40                    # 23:59:20 UTC at row 0
    sim = ls.Sim(rig)
    sim.run(12, lambda k, r: setattr(r, "card_in", not 2 <= k < 9))
    assert uptimes(sim, "/20261125.csv") == grid(4)
    assert uptimes(sim, "/20261126.csv") == grid(8, first=4)
    for path in ("/20261125.csv", "/20261126.csv"):
        assert sim.files[path][:2] == ["# nasim-logger-csv v2", sim.header()]


def test_a_day_file_begun_by_older_firmware_gets_a_new_block_not_mixed_rows(tmp_path):
    from analysis import ingest
    rig = ls.Rig()
    old_header = ",".join(ls.Sim().header().split(",")[:21])
    old_row = "2026-11-25,05:59:50,39.0,39.0,39.0,39.0,39.0,29.0,20.0,700,17.9,4.9,87.7,NaN,,0,NaN,none,0,4000,32"
    rig.card["/20261125.csv"] = bytearray((old_header + "\r\n" + old_row + "\r\n").encode())
    sim = ls.Sim(rig)
    sim.run(3)
    lines = sim.files["/20261125.csv"]
    assert lines[:4] == [old_header, old_row, "# nasim-logger-csv v2", sim.header()]
    (tmp_path / "20261125.csv").write_bytes(bytes(rig.card["/20261125.csv"]))
    df = ingest.read_logger(tmp_path)
    assert list(df["csv_version"]) == [1, 2, 2, 2] and df.attrs["rows_malformed"] == 0


def test_garbage_in_a_ring_segment_is_skipped_and_counted():
    rig = ls.Rig()
    sim = ls.Sim(rig)
    sim.run(6, lambda k, r: setattr(r, "card_in", k < 2))
    seq = min(rig.flash)
    good = rig.flash[seq].decode().splitlines(keepends=True)
    rig.flash[seq] = bytearray((good[0] + "not a record at all\n" + "".join(good[2:])).encode())
    rig.card_in = True
    sim.run(3)
    up = uptimes(sim)
    assert up == sorted(up) and len(set(up)) == len(up) and rig.flash == {}
    assert sim.store("n_dropped") == 1


def test_the_card_as_the_analysis_reads_it_after_an_outage(tmp_path):
    from analysis import ingest
    from data.schema import has_sensor_fail, has_status
    sim = ls.scenario_sd_missing()
    for path, data in sim.rig.card.items():
        (tmp_path / path.lstrip("/")).write_bytes(bytes(data))
    df = ingest.read_logger(tmp_path)
    assert len(df) == 20 and df.attrs["rows_malformed"] == 0
    assert (df["t_utc"].diff().dropna().dt.total_seconds() == 10).all()   # every 10 s, in order
    assert has_status(df["status"], "SD_BUFFERED").sum() == 12
    assert has_sensor_fail(df["sens_fail"], "sd").sum() == 12


def test_the_logger_never_formats_the_card():
    """The store's driver table has no format function, and the logger's sources do not
    call the one that exists for the bench test sketch (tests/sdcard)."""
    assert not any("format" in name for name, _ in ls.StoreIo._fields_)
    for f in ("firmware/logger/logger.ino", "firmware/libraries/PvDaq/src/pv_store_logic.c",
              "firmware/libraries/PvDaq/src/pv_log_logic.c"):
        text = (ROOT / f).read_text(encoding="utf-8")
        assert "formatIfNoFilesystem" not in text and "mkfs" not in text, f
    sd = (ROOT / "firmware/libraries/PvDaq/src/pv_sdcard.cpp").read_text(encoding="utf-8")
    assert "bool PvSdCard::begin() { return mount(false); }" in sd


# --- the sketch: watchdog, first action, what goes through the store -----------------------------
# Text checks of logger.ino: what cannot run on the PC is at least pinned where it stands.

SKETCH = (ROOT / "firmware/logger/logger.ino").read_text(encoding="utf-8")


def test_the_watchdog_is_on_in_every_build_and_well_above_one_row():
    import re
    m = re.search(r"LOOP_WDT_MS = HAS_LOAD \? (\d+) : (\d+);", SKETCH)
    with_load, without = int(m.group(1)), int(m.group(2))
    assert without >= 3 * 10000 and with_load > 10000    # 10 s = one row (SAMPLE_PERIOD_MS)
    setup = SKETCH.split("void setup() {")[1]
    on = setup.index("enableLoopWDT();")
    assert on < setup.index("d->begin()")                # before the first device is touched
    assert "if (HAS_LOAD)" not in setup[:on]             # not only with the load fitted
    assert setup.count("feedLoopWDT();") >= 3            # fed between the slow steps of setup


def test_the_first_statement_of_setup_drives_the_pump_gate_low():
    setup = SKETCH.split("void setup() {")[1]
    assert setup.lstrip().startswith("pvOutputsOff();")
    daq = (ROOT / "firmware/libraries/PvDaq/src/PvDaq.h").read_text(encoding="utf-8")
    body = daq.split("inline void pvOutputsOff() {")[1].split("\n}")[0]
    assert "for (int pin : {pins::PUMP_PWM," in body     # the pump first
    level, mode = body.index("gpio_set_level((gpio_num_t)pin, 0);"), body.index("pinMode(pin, OUTPUT);")
    assert level < mode                                  # LOW is written before the pin drives
    config = (ROOT / "firmware/libraries/PvDaq/src/pv_config.h").read_text(encoding="utf-8")
    assert "constexpr int PUMP_PWM = 32;" in config and "#define ENABLE_PUMP 0" in config


def test_the_boot_writes_the_reset_reason_as_an_event():
    assert 'pvlog_event(&plog, "boot", text);' in SKETCH and "esp_reset_reason()" in SKETCH
    setup = SKETCH.split("void setup() {")[1]
    assert setup.index("bootEvent();") < setup.index("pvstore_begin(&store, &SIO);")
    for reason in ("TASK_WDT", "PANIC", "BROWNOUT", "POWERON", "SOFTWARE"):
        assert f'"{reason}"' in SKETCH


def test_rows_events_and_markers_all_go_through_the_store():
    assert SKETCH.count("pvlog_commit(&plog, &store, &SIO, &row, line, sizeof(line));") == 1
    assert "sd.appendLine(path" not in SKETCH            # no row is written past the store
    assert "int sioMount(void *) { return sd.begin(); }" in SKETCH     # mounts, never formats


def test_a_restart_with_a_full_ring_finds_every_line_and_copies_them_in_order():
    """The longest boot there can be: 128 segments on the flash. Each is counted in one
    pass (the sketch feeds the watchdog per segment), and the copy to the card then starts
    with the oldest line, bounded per row as always."""
    rig = ls.Rig()
    rig.convert_ms = 0
    sim = ls.Sim(rig, scan_every=10 ** 6)
    sim.run(4200, lambda k, r: setattr(r, "card_in", k < 3))
    waiting, dropped = sim.store("waiting"), sim.store("dropped")
    assert len(rig.flash) == 128 and waiting > 4000
    rig.reboot()
    sim2 = ls.Sim(rig, scan_every=10 ** 6)
    assert sim2.store("waiting") == waiting and sim2.store("dropped") == dropped
    assert sim2.store("segments") == 128
    rig.card_in = True
    sim2.run(100)
    assert 0 < sim2.store("waiting") < waiting           # about 20 lines per row [EST]: not yet
    sim2.run(200)
    assert sim2.store("waiting") == 0 and rig.flash == {}
    rows = sim2.data_rows("/20261125.csv") + sim2.data_rows("/20261126.csv")
    times = [(r["date"], r["time"]) for r in rows]
    assert times == sorted(times) and len(set(times)) == len(times)   # in order, none twice
    # the ring was exactly full at the restart, so the first new row cost one more segment
    # (the oldest, 32 lines at the most): given up AND counted, as the rule says
    given_up = int([d for e, d in sim2.events() if e == "ring_flushed"][0].split("; ")[1].split()[0])
    assert dropped <= given_up <= dropped + 32
    made = 4200 + 300                                    # rows of both boots
    assert made - given_up <= len(rows) < made           # every missing row is a counted one
    assert max(sim2.cycle_ms) < 912 + 1000 + 400         # every row stayed bounded


# =================================================================================================
# Follow-up 2026-11-21: the copy back goes in chunks, one per row, each within its budget
# =================================================================================================

BUDGET_MS = 1000           # PVSTORE_FLUSH_BUDGET_MS (pv_store_logic.h)
FLUSH_MAX = 64             # PVSTORE_FLUSH_MAX
LOOP_WDT_MS = 30000        # logger.ino, HAS_LOAD 0 (15000 with the load)


def full_ring():
    """A night without the card: the ring at its 128 segments (4096 lines), some given up."""
    rig = ls.Rig()
    rig.convert_ms = 0                                   # (only to make 4200 rows run fast)
    sim = ls.Sim(rig, scan_every=10 ** 6)
    sim.run(4200, lambda k, r: setattr(r, "card_in", k < 3))
    assert sim.store("segments") == 128 and sim.store("waiting") > 4000
    return sim


def drain(sim, max_rows=400):
    """Rows until the ring is empty; per row: (records copied, ms of the chunk)."""
    chunks = []
    while sim.store("waiting") and len(chunks) < max_rows:
        sim.cycle()
        chunks.append((sim.store("chunk_n"), sim.store("chunk_ms")))
    return chunks


def test_the_numbers_of_the_chunk_rule_match_the_header():
    text = (ROOT / "firmware/libraries/PvDaq/src/pv_store_logic.h").read_text()
    assert f"#define PVSTORE_FLUSH_BUDGET_MS {BUDGET_MS}" in text
    assert f"#define PVSTORE_FLUSH_MAX {FLUSH_MAX} " in text
    assert f"LOOP_WDT_MS = HAS_LOAD ? 15000 : {LOOP_WDT_MS}" in SKETCH


def test_a_full_ring_goes_back_to_the_card_in_chunks_each_within_its_budget():
    """4096 lines waiting, card back: every row copies one chunk, no chunk passes the 1 s
    budget, no row comes near the watchdog, and the card ends complete, in order, once."""
    sim = full_ring()
    rows_before = sim.n
    sim.rig.card_in = True
    chunks = drain(sim)
    assert sim.store("waiting") == 0 and sim.rig.flash == {}
    assert len(chunks) > 4096 // FLUSH_MAX               # many rows: it went in chunks
    assert all(1 <= n <= FLUSH_MAX for n, _ in chunks)   # every row moved the copy on
    assert max(ms for _, ms in chunks) <= BUDGET_MS      # same-speed card: never over
    assert sim.store("chunk_ms_max") <= BUDGET_MS
    during = sim.cycle_ms[rows_before:]
    assert max(during) <= 912 + BUDGET_MS                # the row itself + one chunk [EST]
    assert max(during) < LOOP_WDT_MS / 10
    rows = sim.data_rows("/20261125.csv") + sim.data_rows("/20261126.csv")
    times = [(r["date"], r["time"]) for r in rows]
    assert times == sorted(times) and len(set(times)) == len(times)


def test_a_record_that_might_not_fit_is_not_started():
    """A card that takes 300 ms per write: the old rule (stop only once 1 s had passed)
    started a 4th record at ~940 ms and ended at ~1250 ms. Now a record starts only if
    the slowest one of the chunk still fits."""
    sim = full_ring()
    sim.rig.card_in = True
    sim.rig.sd_write_ms = 300
    chunks = drain(sim, max_rows=30)
    assert all(ms <= BUDGET_MS for _, ms in chunks)
    assert all(n >= 2 for n, _ in chunks[1:])            # ... and still > 1 new line per row
    assert sim.store("waiting") < 4096                   # so the backlog shrinks


def test_a_card_that_stalls_overshoots_by_that_one_write_only_and_ends_the_chunk():
    sim = full_ring()
    sim.rig.card_in = True
    sim.rig.sd_write_extra = [0] * 10 + [2000]           # the 11th write hangs 2 s
    chunks = drain(sim)
    n0, ms0 = chunks[0]
    assert n0 == 11                                      # the stall was the last of its chunk
    assert BUDGET_MS < ms0 <= BUDGET_MS + 2000           # over by the stall, not more
    assert max(ms for _, ms in chunks[1:]) <= BUDGET_MS  # the next chunks are normal again
    assert sim.store("waiting") == 0


def test_full_ring_and_every_sensor_at_its_time_out_stays_far_below_the_watchdog():
    """The slowest pass the logic allows: every device at its limit (as in the slowest-row
    test) WHILE a full ring is copied back to a slow card."""
    sim = full_ring()
    rig = sim.rig
    for d in rig.dev.values():
        d.slow = True
    rig.bus_shorted = True
    rig.convert_ms, rig.ds_read_ms = 750, 30
    rig.card_in = True
    rig.sd_write_ms = 250
    start = sim.n
    sim.run(20)
    worst = max(sim.cycle_ms[start:])
    assert worst < 7000 + BUDGET_MS + 1000               # sensors [EST] + chunk + events
    assert worst < 15000 / 1.5                           # below the load build's 15 s too
    assert sim.store("chunks") == 20                     # every row moved the copy on
