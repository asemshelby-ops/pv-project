"""firmware/tools/set_rtc.py: the drift written to the log each time the RTC is set.
No serial port is used; only the arithmetic and the log file."""
import csv
import datetime as dt
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("set_rtc", ROOT / "firmware/tools/set_rtc.py")
set_rtc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(set_rtc)
T0 = dt.datetime(2026, 11, 15, 6, 0, 0, tzinfo=dt.timezone.utc)


def test_drift_from_offset_and_elapsed_time():
    days, per_day, ppm, note = set_rtc.drift(1.4, T0, T0 + dt.timedelta(days=7))
    assert (days, note) == (7.0, "")
    assert per_day == pytest.approx(0.2) and ppm == pytest.approx(0.2 / 86400 * 1e6, abs=1e-3)
    assert set_rtc.drift(-0.7, T0, T0 + dt.timedelta(days=7))[1] == pytest.approx(-0.1)


def test_cases_where_no_drift_is_claimed():
    week = T0 + dt.timedelta(days=7)
    assert set_rtc.drift(None, T0, week)[1:3] == ("", "")              # old sketch: no NOW
    assert "first entry" in set_rtc.drift(0.3, None, week)[3]
    assert "too large" in set_rtc.drift(3 * 3600.0, T0, week)[3]       # clock was on local time
    days, per_day, _, note = set_rtc.drift(0.02, T0, T0 + dt.timedelta(hours=2))
    assert per_day == "" and "less than 12 h" in note and days == pytest.approx(2 / 24, abs=1e-4)


def test_log_file_round_trip(tmp_path):
    log = tmp_path / "rtc_set_log.csv"
    assert set_rtc.last_set_time(log) is None
    for k, offset in enumerate((0.0, 1.4)):
        t = T0 + dt.timedelta(days=7 * k)
        days, per_day, ppm, note = set_rtc.drift(offset, set_rtc.last_set_time(log), t)
        set_rtc.append_log({"set_utc": f"{t:{set_rtc.FMT}}", "port": "COM3",
                            "rtc_before_utc": "", "offset_s": offset,
                            "days_since_last_set": days, "drift_s_per_day": per_day,
                            "drift_ppm": ppm, "note": note}, log)
    rows = list(csv.DictReader(open(log, newline="")))
    assert list(rows[0]) == set_rtc.COLUMNS and len(rows) == 2
    assert rows[0]["drift_s_per_day"] == "" and float(rows[1]["drift_s_per_day"]) == 0.2
    assert set_rtc.last_set_time(log) == T0 + dt.timedelta(days=7)
