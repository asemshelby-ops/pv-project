"""The logger row format: the five columns appended on 2026-11-12 (D, load_mode, sweep_id,
uptime_ms, status), CSV v2 of 2026-11-21 (version line, 20 more columns, blocks), files
written before either, rows without RTC time, and the status and sensor bits. All data is
synthetic (analysis/fake_logger.py)."""
import pathlib
import re

import pandas as pd
import pytest

import config
from analysis import fake_logger, ingest
from data import synth
from data.schema import (FAIL_COUNTER_COLUMNS, LOGGER_EXTRA_COLUMNS, LOGGER_V2_COLUMNS,
                         SENSOR_BITS, STATUS_BITS, decode_sensors, decode_status,
                         has_sensor_fail, has_status)

ROOT = pathlib.Path(__file__).resolve().parent.parent
BIT = {name: 1 << bit for name, bit in STATUS_BITS.items()}
CLEAN = {"rows_without_time": 0, "rows_time_rebuilt": 0, "rows_malformed": 0}
LOGIC_C = ROOT / "firmware/libraries/PvDaq/src/pv_log_logic.c"


def _header_at(lines):
    """Index of the first header line (after the version line of a CSV v2 file)."""
    return next(k for k, line in enumerate(lines) if line.startswith("date,"))


def firmware_header():
    """The header line the logger writes: the HEADER string of pv_log_logic.c."""
    literal = re.search(r"static const char HEADER\[\] =((?:\s*\"[^\"]*\")+);",
                        LOGIC_C.read_text(encoding="utf-8")).group(1)
    return "".join(re.findall(r"\"([^\"]*)\"", literal)).split(",")


@pytest.fixture(scope="module")
def days():
    return synth.generate(seed=3, n_days=2, hard=False)[0]


def _edit(folder, row, **cells):
    """Overwrite cells of data row `row` (0 = first row) in the first day file."""
    f = sorted(folder.glob("2*.csv"))[0]
    lines = f.read_text().splitlines()
    h = _header_at(lines)
    header = lines[h].split(",")
    values = lines[h + 1 + row].split(",")
    for name, value in cells.items():
        values[header.index(name)] = str(value)
    lines[h + 1 + row] = ",".join(values)
    f.write_text("\n".join(lines) + "\n")


# --- loader: old and new formats ---------------------------------------------------------

def test_new_format_loads_with_typed_extra_columns(days, tmp_path):
    fake_logger.write_logger_files(days, tmp_path)
    df = ingest.read_logger(tmp_path)
    assert len(df) == len(days)
    assert df["D"].isna().all()                                  # "NaN": no load fitted
    assert (df["load_mode"] == "none").all()
    assert (df["sweep_id"] == 0).all() and (df["status"] == 0).all()
    assert str(df["uptime_ms"].dtype) == "Int64" and df["uptime_ms"].iloc[0] == 5000
    assert df["uptime_ms"].iloc[1] - df["uptime_ms"].iloc[0] == 10000
    assert (df["time_source"] == "rtc").all()
    assert df.attrs == CLEAN
    assert (df["csv_version"] == 2).all()                        # the version line was read
    assert df["T6"].isna().all() and (df["pyr_mV"] == df["irradiance"].round()).all()
    for c in ["sens_fail", *FAIL_COUNTER_COLUMNS, "ring_rows", "ring_dropped"]:
        assert str(df[c].dtype) == "Int64" and (df[c] == 0).all(), c
    assert (df["cycle_ms"] == 912).all()


def test_old_format_still_loads_and_gives_the_same_data(days, tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    fake_logger.write_logger_files(days, old, extra_columns=False)
    fake_logger.write_logger_files(days, new)
    assert "status" not in sorted(old.glob("*.csv"))[0].read_text().splitlines()[0]
    a, b = ingest.read_logger(old), ingest.read_logger(new)
    for c in LOGGER_EXTRA_COLUMNS + LOGGER_V2_COLUMNS:           # present, but "not recorded"
        assert a[c].isna().all(), c
    assert not has_status(a["status"], "RTC_INVALID").any()
    assert not has_sensor_fail(a["sens_fail"], "T3").any()
    assert (a["csv_version"] == 0).all() and (b["csv_version"] == 2).all()
    shared = [c for c in a.columns
              if c not in LOGGER_EXTRA_COLUMNS + LOGGER_V2_COLUMNS + ["csv_version"]]
    pd.testing.assert_frame_equal(a[shared], b[shared])
    assert (a["segment"] == b["segment"]).all()


def test_old_and_new_files_in_one_folder(days, tmp_path):
    fake_logger.write_logger_files(days, tmp_path)
    first = sorted(tmp_path.glob("2*.csv"))[0]                   # make day 1 an old file
    rows = [",".join(line.split(",")[:16]) for line in first.read_text().splitlines()[1:]]
    first.write_text("\n".join(rows) + "\n")
    df = ingest.read_logger(tmp_path)
    assert len(df) == len(days)
    day1 = df["date_utc"] == df["date_utc"].iloc[0]
    assert (df.loc[day1, "csv_version"] == 0).all() and (df.loc[~day1, "csv_version"] == 2).all()
    assert df.loc[day1, "status"].isna().all() and (df.loc[~day1, "status"] == 0).all()
    assert df.loc[day1, "load_mode"].isna().all() and (df.loc[~day1, "load_mode"] == "none").all()


# --- loader: rows the logger wrote without RTC time ----------------------------------------

def test_rows_without_rtc_time_are_kept_and_timed_from_uptime(days, tmp_path):
    fake_logger.write_logger_files(days, tmp_path)
    ref = ingest.read_logger(tmp_path)
    for row in (100, 101, 102):
        _edit(tmp_path, row, date="", time="", status=BIT["RTC_INVALID"] | BIT["DET_SKIPPED"])
    df = ingest.read_logger(tmp_path)
    assert len(df) == len(ref)                                   # nothing dropped
    assert (df["t_utc"] == ref["t_utc"]).all()                   # and put back in place
    assert list(df["time_source"].iloc[99:104]) == ["rtc", "uptime", "uptime", "uptime", "rtc"]
    assert df["time"].iloc[101] == ref["time"].iloc[101]         # local time filled in
    assert has_status(df["status"], "RTC_INVALID").sum() == 3
    assert df.attrs == {**CLEAN, "rows_time_rebuilt": 3}


def test_untimed_row_right_after_a_reboot_uses_the_next_timed_row(days, tmp_path):
    fake_logger.write_logger_files(days, tmp_path)
    ref = ingest.read_logger(tmp_path)
    for row in range(200, 230):                                  # the board restarts at row 200
        _edit(tmp_path, row, uptime_ms=3000 + 10000 * (row - 200))
    _edit(tmp_path, 200, date="", time="", status=BIT["RTC_INVALID"])
    df = ingest.read_logger(tmp_path)
    assert df["time_source"].iloc[200] == "uptime"
    assert df["t_utc"].iloc[200] == ref["t_utc"].iloc[200]       # not taken from the old boot


def test_rows_whose_time_cannot_be_rebuilt_are_counted_not_hidden(days, tmp_path):
    fake_logger.write_logger_files(days, tmp_path, extra_columns=False)
    _edit(tmp_path, 50, date="", time="")                        # old file: no uptime_ms
    header = ",".join(fake_logger.HEADER + fake_logger.EXTRA)    # a 21-column /nodate.csv
    blank = "," * 15 + f"0,NaN,none,0,{{}},{BIT['RTC_INVALID']}"  # date ... p empty, alarm 0
    (tmp_path / "nodate.csv").write_text(
        "\n".join([header, blank.format(5000), blank.format(15000)]) + "\n")
    with pytest.warns(UserWarning, match="3 logger rows have no usable time"):
        df = ingest.read_logger(tmp_path)
    assert len(df) == len(days) - 1
    assert df.attrs == {**CLEAN, "rows_without_time": 3}


# --- status bits ------------------------------------------------------------------------------

def test_status_bits_match_the_firmware_header():
    text = (ROOT / "firmware/libraries/PvDaq/src/pv_status.h").read_text()
    defined = {name: int(bit)
               for name, bit in re.findall(r"^#define ST_(\w+)\s+\(1u << (\d+)\)", text, re.M)}
    assert defined == STATUS_BITS
    assert len(set(defined.values())) == len(defined)            # no bit used twice
    assert text.count("#define ST_") == len(defined)             # every define was parsed


def test_sensor_numbers_match_the_firmware_header():
    text = (ROOT / "firmware/libraries/PvDaq/src/pv_status.h").read_text()
    defined = {name: int(n) for name, n in
               re.findall(r"^#define PV_SENSOR_(\w+)\s+(\d+)\b", text, re.M)}
    assert defined.pop("COUNT") == len(SENSOR_BITS)
    assert {k.lower(): v for k, v in defined.items()} == {k.lower(): v
                                                          for k, v in SENSOR_BITS.items()}
    names = re.search(r"SENSOR_NAME\[PV_SENSOR_COUNT\] = \{(.*?)\};", LOGIC_C.read_text(), re.S)
    assert re.findall(r"\"(\w+)\"", names.group(1)) == list(SENSOR_BITS)   # the f_* order
    assert FAIL_COUNTER_COLUMNS == [f"f_{n}" for n in SENSOR_BITS]


def test_decode_sensors():
    assert decode_sensors(0) == [] and decode_sensors(pd.NA) == []
    assert decode_sensors(4) == ["T3"] and decode_sensors(512) == ["hub"]
    assert decode_sensors((1 << 2) | (1 << 12)) == ["T3", "sd"]
    s = pd.Series(pd.array([0, 4, pd.NA, 4100], dtype="Int64"))
    assert list(has_sensor_fail(s, "T3")) == [False, True, False, True]
    assert list(has_sensor_fail(s, "sd")) == [False, False, False, True]


def test_decode_status():
    assert decode_status(0) == []
    assert decode_status(BIT["RTC_INVALID"]) == ["RTC_INVALID"]
    assert decode_status(33) == ["RTC_INVALID", "TROM_UNMAPPED"]           # 1 + 32
    assert decode_status(sum(BIT.values())) == sorted(STATUS_BITS, key=STATUS_BITS.get)
    assert decode_status(1 << 14) == ["BIT14"]                             # unknown bit: shown
    assert decode_status(pd.NA) == [] and decode_status(float("nan")) == []
    assert decode_status("96") == ["TROM_UNMAPPED", "DET_SKIPPED"]         # text from a CSV


def test_has_status_on_a_column_with_missing_values():
    s = pd.array([0, 32, 33, pd.NA, 128], dtype="Int64")
    s = pd.Series(s)
    assert list(has_status(s, "TROM_UNMAPPED")) == [False, True, True, False, False]
    assert list(has_status(s, "RTC_INVALID")) == [False, False, True, False, False]
    assert list(has_status(s, "DET_RESET")) == [False, False, False, False, True]


def test_firmware_header_line_equals_the_format_the_tests_use():
    header = firmware_header()
    assert header == fake_logger.HEADER + fake_logger.EXTRA + fake_logger.V2
    assert header[16:21] == LOGGER_EXTRA_COLUMNS and header[21:] == LOGGER_V2_COLUMNS
    assert len(header) == 41 and len(set(header)) == 41
    # CSV v2 only APPENDS: the first 21 names are the format written since 2026-11-12
    assert header[:21] == ("date,time,T1,T2,T3,T4,T5,T_ambient,humidity,irradiance,voltage,"
                           "current,power,wind,p,alarm,D,load_mode,sweep_id,uptime_ms,"
                           "status").split(",")
    version = re.search(r'#define PVLOG_VERSION_LINE "(.*)"',
                        (LOGIC_C.with_suffix(".h")).read_text()).group(1)
    assert version == fake_logger.VERSION_LINE


# --- CSV v2: blocks, a format change inside one file, cut-off lines ----------------------------

def test_a_file_that_changes_format_in_the_middle_loads_block_by_block(days, tmp_path):
    """A firmware update at midday: the day file starts with 21-column rows and goes on,
    after the new firmware's version line and header, with 41-column rows."""
    fake_logger.write_logger_files(days, tmp_path)
    ref = ingest.read_logger(tmp_path)
    first = sorted(tmp_path.glob("2*.csv"))[0]
    lines = first.read_text().splitlines()
    version, header, rows = lines[0], lines[1], lines[2:]
    old = [",".join(r.split(",")[:21]) for r in rows[:1000]]
    first.write_text("\n".join([",".join(header.split(",")[:21]), *old, version, header,
                                *rows[1000:]]) + "\n")
    df = ingest.read_logger(tmp_path)
    assert len(df) == len(ref) and df.attrs == CLEAN
    assert list(df["csv_version"].iloc[[0, 999, 1000]]) == [1, 1, 2]
    assert df["cycle_ms"].iloc[:1000].isna().all() and (df["cycle_ms"].iloc[1000:] == 912).all()
    assert (df["status"] == 0).all()                             # in both blocks
    pd.testing.assert_series_equal(df["T1"], ref["T1"])


def test_a_header_block_repeated_at_every_boot_is_not_data(days, tmp_path):
    fake_logger.write_logger_files(days, tmp_path)
    ref = ingest.read_logger(tmp_path)
    first = sorted(tmp_path.glob("2*.csv"))[0]
    lines = first.read_text().splitlines()
    first.write_text("\n".join(lines[:500] + lines[:2] + lines[500:]) + "\n")   # a reboot
    df = ingest.read_logger(tmp_path)
    assert len(df) == len(ref) and df.attrs == CLEAN and (df["csv_version"] == 2).all()


def test_cut_off_lines_are_left_out_and_counted(days, tmp_path):
    fake_logger.write_logger_files(days, tmp_path)
    ref = ingest.read_logger(tmp_path)
    first = sorted(tmp_path.glob("2*.csv"))[0]
    lines = first.read_text().splitlines()
    torn = lines[300][:57]                                       # a write cut by a power loss
    first.write_text("\n".join(lines[:300] + [torn] + lines[300:] + ["2026-11-"]) + "\n")
    with pytest.warns(UserWarning, match="2 lines in the logger files are not complete rows"):
        df = ingest.read_logger(tmp_path)
    assert len(df) == len(ref)                                   # the full row 300 is kept
    assert df.attrs == {**CLEAN, "rows_malformed": 2}
    pd.testing.assert_series_equal(df["irradiance"], ref["irradiance"])


# --- detector segments: the board's rule (config.DET_RESET_AFTER_FAILS) -------------------------

def _drop(folder, rows):
    """Remove data rows (0 = first row) from the first day file: rows that never reached the card."""
    f = sorted(folder.glob("2*.csv"))[0]
    lines = f.read_text().splitlines()
    h = _header_at(lines)
    f.write_text("\n".join(l for k, l in enumerate(lines) if k - h - 1 not in set(rows)) + "\n")


def _segments(folder):
    df = ingest.read_logger(folder)
    return df, ingest.detector_frame(df)["segment"].nunique()


def test_four_unusable_rows_are_bridged_and_five_start_a_new_segment(days, tmp_path):
    assert config.DET_RESET_AFTER_FAILS == 5
    fake_logger.write_logger_files(days, tmp_path)
    _, n0 = _segments(tmp_path)                                  # one segment per day
    for row in range(300, 304):
        _edit(tmp_path, row, irradiance="")
    df, n = _segments(tmp_path)
    assert n == n0 and (df["segment"].iloc[300:304] == -1).all()  # skipped, the run goes on
    assert df["segment"].iloc[299] == df["segment"].iloc[304]
    _edit(tmp_path, 304, irradiance="")                          # the fifth in a row
    df, n = _segments(tmp_path)
    assert n == n0 + 1 and df["segment"].iloc[305] == df["segment"].iloc[299] + 1


def test_absent_rows_count_like_unusable_ones(days, tmp_path):
    fake_logger.write_logger_files(days, tmp_path)
    _, n0 = _segments(tmp_path)
    _drop(tmp_path, range(300, 304))                             # 4 rows never written: bridged
    assert _segments(tmp_path)[1] == n0
    _edit(tmp_path, 600, T3="")
    _edit(tmp_path, 601, T_ambient="")
    _drop(tmp_path, [602, 603])                                  # 2 unusable + 2 absent = 4
    assert _segments(tmp_path)[1] == n0
    _drop(tmp_path, range(900, 905))                             # 5 absent: a new segment
    assert _segments(tmp_path)[1] == n0 + 1
    _edit(tmp_path, 1200, irradiance="")
    _drop(tmp_path, range(1201, 1205))                           # 1 unusable + 4 absent = 5
    assert _segments(tmp_path)[1] == n0 + 2


def test_a_restart_of_the_board_and_a_row_without_rtc_time(days, tmp_path):
    fake_logger.write_logger_files(days, tmp_path)
    _, n0 = _segments(tmp_path)
    _edit(tmp_path, 100, date="", time="", status=BIT["RTC_INVALID"])
    df, n = _segments(tmp_path)                                  # the board skipped that row
    assert n == n0 and df["segment"].iloc[100] == -1 and df["time_source"].iloc[100] == "uptime"
    for row in range(400, 420):                                  # uptime_ms falls: a restart
        _edit(tmp_path, row, uptime_ms=4000 + 10000 * (row - 400))
    df, n = _segments(tmp_path)
    assert n == n0 + 1 and df["segment"].iloc[400] == df["segment"].iloc[399] + 1


def test_board_and_analysis_share_one_reset_constant():
    from export import run as ex
    header = (ROOT / "export/c/generated/nasim_config.h").read_text()
    assert header == ex.config_header()                          # the committed file is current
    assert f"#define NASIM_DET_RESET_AFTER_FAILS {config.DET_RESET_AFTER_FAILS}\n" in header
    ino = (ROOT / "firmware/logger/logger.ino").read_text()
    assert "DET_RESET_AFTER_FAILS = NASIM_DET_RESET_AFTER_FAILS;" in ino
    assert "GAP_S" not in (ROOT / "analysis/ingest.py").read_text()
