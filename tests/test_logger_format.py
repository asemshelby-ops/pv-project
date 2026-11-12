"""The logger row format: the five columns appended on 2026-11-12 (D, load_mode, sweep_id,
uptime_ms, status), files written before that, rows without RTC time, and the status
bits. All data is synthetic (analysis/fake_logger.py)."""
import pathlib
import re

import pandas as pd
import pytest

import config
from analysis import fake_logger, ingest
from data import synth
from data.schema import (LOGGER_EXTRA_COLUMNS, STATUS_BITS, decode_status, has_status)

ROOT = pathlib.Path(__file__).resolve().parent.parent
BIT = {name: 1 << bit for name, bit in STATUS_BITS.items()}


@pytest.fixture(scope="module")
def days():
    return synth.generate(seed=3, n_days=2, hard=False)[0]


def _edit(folder, row, **cells):
    """Overwrite cells of data row `row` (0 = first row) in the first day file."""
    f = sorted(folder.glob("2*.csv"))[0]
    lines = f.read_text().splitlines()
    header = lines[0].split(",")
    values = lines[row + 1].split(",")
    for name, value in cells.items():
        values[header.index(name)] = str(value)
    lines[row + 1] = ",".join(values)
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
    assert df.attrs == {"rows_without_time": 0, "rows_time_rebuilt": 0}


def test_old_format_still_loads_and_gives_the_same_data(days, tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    fake_logger.write_logger_files(days, old, extra_columns=False)
    fake_logger.write_logger_files(days, new)
    assert "status" not in sorted(old.glob("*.csv"))[0].read_text().splitlines()[0]
    a, b = ingest.read_logger(old), ingest.read_logger(new)
    for c in LOGGER_EXTRA_COLUMNS:                               # present, but "not recorded"
        assert a[c].isna().all(), c
    assert not has_status(a["status"], "RTC_INVALID").any()
    shared = [c for c in a.columns if c not in LOGGER_EXTRA_COLUMNS]
    pd.testing.assert_frame_equal(a[shared], b[shared])
    assert (a["segment"] == b["segment"]).all()


def test_old_and_new_files_in_one_folder(days, tmp_path):
    fake_logger.write_logger_files(days, tmp_path)
    first = sorted(tmp_path.glob("2*.csv"))[0]                   # make day 1 an old file
    rows = [",".join(line.split(",")[:-5]) for line in first.read_text().splitlines()]
    first.write_text("\n".join(rows) + "\n")
    df = ingest.read_logger(tmp_path)
    assert len(df) == len(days)
    day1 = df["date_utc"] == df["date_utc"].iloc[0]
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
    assert df.attrs == {"rows_without_time": 0, "rows_time_rebuilt": 3}


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
    header = ",".join(fake_logger.HEADER + fake_logger.EXTRA)
    blank = "," * 15 + f"0,NaN,none,0,{{}},{BIT['RTC_INVALID']}"  # date ... p empty, alarm 0
    (tmp_path / "nodate.csv").write_text(
        "\n".join([header, blank.format(5000), blank.format(15000)]) + "\n")
    with pytest.warns(UserWarning, match="3 logger rows have no usable time"):
        df = ingest.read_logger(tmp_path)
    assert len(df) == len(days) - 1
    assert df.attrs == {"rows_without_time": 3, "rows_time_rebuilt": 0}


# --- status bits ------------------------------------------------------------------------------

def test_status_bits_match_the_firmware_header():
    text = (ROOT / "firmware/libraries/PvDaq/src/pv_status.h").read_text()
    defined = {name: int(bit)
               for name, bit in re.findall(r"^#define ST_(\w+)\s+\(1u << (\d+)\)", text, re.M)}
    assert defined == STATUS_BITS
    assert len(set(defined.values())) == len(defined)            # no bit used twice
    assert text.count("#define ST_") == len(defined)             # every define was parsed


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
    ino = (ROOT / "firmware/logger/logger.ino").read_text()
    literal = re.search(r"const char \*HEADER =((?:\s*\"[^\"]*\")+);", ino).group(1)
    header = "".join(re.findall(r"\"([^\"]*)\"", literal))
    assert header.split(",") == fake_logger.HEADER + fake_logger.EXTRA
    assert header.split(",")[-5:] == LOGGER_EXTRA_COLUMNS


# --- detector segments: the board's rule (config.DET_RESET_AFTER_FAILS) -------------------------

def _drop(folder, rows):
    """Remove data rows (0 = first row) from the first day file: rows that never reached the card."""
    f = sorted(folder.glob("2*.csv"))[0]
    lines = f.read_text().splitlines()
    f.write_text("\n".join(l for k, l in enumerate(lines) if k - 1 not in set(rows)) + "\n")


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
