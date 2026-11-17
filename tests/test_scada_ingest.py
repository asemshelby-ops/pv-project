"""SCADA ingest: logger files -> historian. Old and new row formats, files that grow,
restarts, rows without RTC time, markers, serial lines. All data is synthetic
(analysis/fake_logger.py writes the files exactly as the logger does)."""
import calendar

import pytest

from analysis import fake_logger
from analysis import ingest as offline
from data import synth
from data.schema import STATUS_BITS
from scada import historian, model
from scada.ingest import FolderIngest, RowParser, SerialIngest

BIT = {name: 1 << bit for name, bit in STATUS_BITS.items()}
NEW_HEADER = ",".join(model.FORMAT_NEW)


@pytest.fixture(scope="module")
def days():
    return synth.generate(seed=3, n_days=2, hard=False)[0]


@pytest.fixture
def conn():
    c = historian.connect(":memory:")
    yield c
    c.close()


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


def _count(conn, table="samples"):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _times(conn):
    return [r[0] for r in conn.execute("SELECT t_utc FROM samples ORDER BY t_utc")]


def _offline_times(folder):
    return [int(t.timestamp()) for t in offline.read_logger(folder)["t_utc"]]


# --- formats ----------------------------------------------------------------------------------

def test_new_format_gives_the_rows_of_the_offline_loader(days, tmp_path, conn):
    fake_logger.write_logger_files(days, tmp_path)
    assert FolderIngest(conn, tmp_path).poll()["rows"] == len(days)
    assert _times(conn) == _offline_times(tmp_path)
    r = dict(conn.execute("SELECT * FROM samples ORDER BY t_utc LIMIT 1").fetchone())
    first = days.iloc[0]
    assert r["T1"] == first["T1"] and r["irradiance"] == first["irradiance"]
    assert r["time_source"] == "rtc" and r["source"].endswith(".csv")
    assert r["wind"] is None and r["D"] is None and r["p"] is None   # "NaN" / empty -> NULL
    assert (r["alarm"], r["load_mode"], r["sweep_id"], r["uptime_ms"], r["status"]) == \
        (0, "none", 0, 5000, 0)


def test_old_format_loads_with_the_five_columns_not_recorded(days, tmp_path, conn):
    fake_logger.write_logger_files(days, tmp_path, extra_columns=False)
    assert FolderIngest(conn, tmp_path).poll()["rows"] == len(days)
    r = conn.execute("SELECT COUNT(status), COUNT(load_mode), COUNT(uptime_ms), COUNT(alarm), "
                     "COUNT(T1) FROM samples").fetchone()
    assert tuple(r) == (0, 0, 0, len(days), len(days))


def test_old_and_new_files_in_one_folder_give_the_same_measurements(days, tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    fake_logger.write_logger_files(days, old, extra_columns=False)
    fake_logger.write_logger_files(days, new)
    got = []
    for folder in (old, new):
        c = historian.connect(":memory:")
        FolderIngest(c, folder).poll()
        got.append(c.execute("SELECT t_utc, T1, T5, T_ambient, irradiance, power, alarm "
                             "FROM samples ORDER BY t_utc").fetchall())
    assert [tuple(r) for r in got[0]] == [tuple(r) for r in got[1]]


def test_bare_schema_file_loads(tmp_path, conn):
    (tmp_path / "20261115.csv").write_text(
        ",".join(model.FORMAT_SCHEMA) + "\n"
        "2026-11-15,03:00:00,20,21,22,23,24,25.5,40,800,17.9,5.1,91.3,1.5\n")
    assert FolderIngest(conn, tmp_path).poll()["rows"] == 1
    r = dict(conn.execute("SELECT * FROM samples").fetchone())
    assert r["wind"] == 1.5 and r["alarm"] is None and r["status"] is None


def test_new_rows_under_an_old_header_are_read_by_their_own_field_count(tmp_path, conn):
    """An old file that newer firmware goes on writing: 16-column header, 21-column rows."""
    old = "2026-11-15,03:00:00,20,21,22,23,24,25,40,800,17.9,5.1,91.3,NaN,,0"
    new = "2026-11-15,03:00:10,20,21,22,23,24,25,40,800,17.9,5.1,91.3,NaN,0.4,2,NaN,none,0,9000,32"
    (tmp_path / "20261115.csv").write_text("\n".join([",".join(model.FORMAT_OLD), old, new]) + "\n")
    assert FolderIngest(conn, tmp_path).poll()["rows"] == 2
    rows = conn.execute("SELECT status, alarm, p FROM samples ORDER BY t_utc").fetchall()
    assert [tuple(r) for r in rows] == [(None, 0, None), (32, 2, 0.4)]


def test_comments_repeated_headers_and_broken_lines_are_not_rows(tmp_path, conn):
    good = "2026-11-15,03:00:{:02d},20,21,22,23,24,25,40,800,17.9,5.1,91.3,NaN,,0,NaN,none,0,{},0"
    (tmp_path / "20261115.csv").write_text("\n".join([
        NEW_HEADER, good.format(0, 5000), "# status: RTC=ok;", NEW_HEADER, "",
        "2026-11-15,03:00:10,20,21",                              # cut short by a power loss
        good.format(20, 25000).replace("800", "8x0"),             # not a number
        good.format(30, 35000)]) + "\n")
    assert FolderIngest(conn, tmp_path).poll()["rows"] == 2
    state = conn.execute("SELECT state_json FROM ingest_files").fetchone()[0]
    assert '"bad_lines": 2' in state


def test_sweep_and_log_files_in_the_folder_are_ignored(days, tmp_path, conn):
    fake_logger.write_logger_files(days, tmp_path)
    (tmp_path / "SW20261115.csv").write_text("sweep_id,k,D,V,I\n1,0,0.02,21.4,0.5\n")
    (tmp_path / "load_log.csv").write_text("date,time,uptime_ms,event\n")
    assert FolderIngest(conn, tmp_path).poll()["rows"] == len(days)


# --- files that grow, restarts, duplicates --------------------------------------------------------

def test_a_growing_file_is_read_once_and_only_in_complete_lines(days, tmp_path, conn):
    src = tmp_path / "src"
    fake_logger.write_logger_files(days, src)
    text = sorted(src.glob("2*.csv"))[0].read_text()
    lines = text.splitlines(keepends=True)
    live = tmp_path / "live"
    live.mkdir()
    f, ingest = live / "20261115.csv", FolderIngest(conn, live)
    f.write_text("".join(lines[:11]) + lines[11][:30])          # 10 rows + half a row
    assert ingest.poll()["rows"] == 10
    assert ingest.poll()["rows"] == 0                            # nothing new
    f.write_text("".join(lines[:21]))                            # the file grew
    assert ingest.poll()["rows"] == 10
    assert _count(conn) == 20


def test_a_restart_goes_on_where_the_last_run_stopped(days, tmp_path):
    db = tmp_path / "h.sqlite"
    src = tmp_path / "src"
    fake_logger.write_logger_files(days, src)
    lines = sorted(src.glob("2*.csv"))[0].read_text().splitlines(keepends=True)
    live = tmp_path / "live"
    live.mkdir()
    (live / "20261115.csv").write_text("".join(lines[:101]))
    c = historian.connect(db)
    assert FolderIngest(c, live).poll()["rows"] == 100
    c.close()
    (live / "20261115.csv").write_text("".join(lines[:151]))
    c = historian.connect(db)                                    # the service was restarted
    assert FolderIngest(c, live).poll()["rows"] == 50            # header remembered, no re-read
    assert _count(c) == 150
    c.close()


def test_the_same_rows_from_a_second_source_are_not_stored_twice(days, tmp_path, conn):
    fake_logger.write_logger_files(days, tmp_path)
    FolderIngest(conn, tmp_path).poll()
    serial = SerialIngest(conn, "COM9")
    for line in sorted(tmp_path.glob("2*.csv"))[0].read_text().splitlines()[:50]:
        serial.handle_line(line, wall=1.0)
    assert _count(conn) == len(days)


def test_a_file_replaced_by_a_shorter_one_is_read_from_the_start(tmp_path, conn):
    row = "2026-11-{:02d},03:00:00,20,21,22,23,24,25,40,800,17.9,5.1,91.3,NaN,,0"
    f, ingest = tmp_path / "20261115.csv", FolderIngest(conn, tmp_path)
    f.write_text("\n".join([",".join(model.FORMAT_OLD), row.format(15), row.format(16)]) + "\n")
    assert ingest.poll()["rows"] == 2
    f.write_text("\n".join([",".join(model.FORMAT_OLD), row.format(17)]) + "\n")
    assert ingest.poll()["rows"] == 1


# --- rows without RTC time ------------------------------------------------------------------------

def test_rows_without_rtc_time_are_timed_from_uptime_like_the_offline_loader(days, tmp_path, conn):
    fake_logger.write_logger_files(days, tmp_path)
    reference = _offline_times(tmp_path)
    for row in (100, 101, 102):
        _edit(tmp_path, row, date="", time="", status=BIT["RTC_INVALID"] | BIT["DET_SKIPPED"])
    assert FolderIngest(conn, tmp_path).poll()["rows"] == len(days)
    assert _times(conn) == reference == _offline_times(tmp_path)
    src = [r[0] for r in conn.execute("SELECT time_source FROM samples ORDER BY t_utc "
                                      "LIMIT 5 OFFSET 99")]
    assert src == ["rtc", "uptime", "uptime", "uptime", "rtc"]
    assert _count(conn, "unplaced_rows") == 0


def test_untimed_rows_right_after_a_reboot_wait_for_the_next_timed_row(days, tmp_path, conn):
    fake_logger.write_logger_files(days, tmp_path)
    reference = _offline_times(tmp_path)
    for row in range(200, 230):                                  # the board restarts at row 200
        _edit(tmp_path, row, uptime_ms=3000 + 10000 * (row - 200))
    for row in (200, 201):                                       # ... and has no time at first
        _edit(tmp_path, row, date="", time="", status=BIT["RTC_INVALID"])
    full = sorted(tmp_path.glob("2*.csv"))[0]
    lines = full.read_text().splitlines(keepends=True)
    ingest = FolderIngest(conn, tmp_path)
    full.write_text("".join(lines[:203]))                        # up to the second untimed row
    ingest.poll()
    assert _count(conn, "unplaced_rows") == 2                    # kept, waiting
    full.write_text("".join(lines))                              # the timed rows arrive
    ingest.poll()
    assert _count(conn, "unplaced_rows") == 0
    assert _times(conn) == reference                             # not taken from the old boot
    assert conn.execute("SELECT COUNT(*) FROM samples WHERE time_source = 'uptime'"
                        ).fetchone()[0] == 2


def test_rows_that_cannot_be_timed_are_kept_and_counted_not_hidden(days, tmp_path, conn):
    fake_logger.write_logger_files(days, tmp_path, extra_columns=False)
    _edit(tmp_path, 50, date="", time="")                        # old file: no uptime_ms
    blank = "," * 15 + f"0,NaN,none,0,{{}},{BIT['RTC_INVALID']}"
    (tmp_path / "nodate.csv").write_text(
        "\n".join([NEW_HEADER, blank.format(5000), blank.format(15000)]) + "\n")
    assert FolderIngest(conn, tmp_path).poll()["rows"] == len(days) - 1
    assert _count(conn, "unplaced_rows") == 3
    with pytest.warns(UserWarning):
        assert len(offline.read_logger(tmp_path)) == _count(conn)    # same as the offline loader


# --- markers --------------------------------------------------------------------------------------

def test_markers_file_with_and_without_time(tmp_path, conn):
    f, ingest = tmp_path / "markers.csv", FolderIngest(conn, tmp_path)
    f.write_text("date,time,millis\n2026-11-15,03:10:00.137,160137\n,,220411\n")
    assert ingest.poll()["markers"] == 2
    with open(f, "a") as fh:
        fh.write("2026-11-15,07:15:30.411,280822\n")
    assert ingest.poll()["markers"] == 1
    rows = [tuple(r) for r in conn.execute("SELECT t_utc_ms, millis FROM markers ORDER BY millis")]
    day = calendar.timegm((2026, 11, 15, 0, 0, 0)) * 1000
    assert rows == [(day + (3 * 60 + 10) * 60000 + 137, 160137), (None, 220411),
                    (day + ((7 * 60 + 15) * 60 + 30) * 1000 + 411, 280822)]


# --- serial ---------------------------------------------------------------------------------------

def test_serial_lines_status_messages_rows_and_markers(conn):
    s = SerialIngest(conn, "COM9")
    row = "{},20,21,22,23,24,25,40,800,17.9,5.1,91.3,NaN,,0,NaN,none,0,{},{}"
    assert s.handle_line("# PV Phase 0 logger. NASIM-GBT ...\r\n", wall=10.0) == "skip"
    assert s.handle_line(NEW_HEADER + "\r\n", wall=10.0) == "skip"
    assert s.handle_line(row.format("2026-11-15,03:00:00", 5000, 0) + "\r\n", wall=11.0) == "row"
    assert s.handle_line("# marker (load off): 2026-11-15,03:00:05.250,10250\r\n",
                         wall=12.0) == "marker"
    assert s.handle_line("# marker: ,,20250\r\n", wall=13.0) == "marker"
    assert _count(conn) == 1 and _count(conn, "markers") == 2
    assert conn.execute("SELECT note FROM markers WHERE millis = 10250").fetchone()[0] == "load off"


def test_serial_joined_after_boot_reads_rows_without_a_header(conn):
    s = SerialIngest(conn, "COM9")
    line = "2026-11-15,03:00:00,20,21,22,23,24,25,40,800,17.9,5.1,91.3,NaN,0.7,5,0.46,hold,7,5000,32"
    assert s.handle_line(line, wall=1.0) == "row"
    r = dict(conn.execute("SELECT * FROM samples").fetchone())
    assert (r["alarm"], r["load_mode"], r["D"], r["status"], r["source"]) == \
        (5, "hold", 0.46, 32, "serial:COM9")


def test_serial_row_without_rtc_time_takes_the_pc_clock(conn):
    s = SerialIngest(conn, "COM9")
    line = f",,20,21,22,23,24,25,40,800,17.9,5.1,91.3,NaN,,0,NaN,none,0,5000,{BIT['RTC_INVALID']}"
    assert s.handle_line(line, wall=1794538800.6) == "row"
    r = conn.execute("SELECT t_utc, time_source FROM samples").fetchone()
    assert tuple(r) == (1794538800, "pc")


def test_parser_counts_boots_from_falls_of_uptime():
    p = RowParser("x")
    row = "2026-11-15,03:00:{:02d},20,21,22,23,24,25,40,800,17.9,5.1,91.3,NaN,,0,NaN,none,0,{},0"
    anchors = [p.parse(row.format(s, up), 0.0)[2]
               for s, up in ((0, 5000), (10, 15000), (20, 3000), (30, 13000))]
    assert anchors == [True, False, True, False] and p.boot_no == 1


# --- Phase B preparation: the two new file formats do not blind the SCADA -------------------

def test_rows_of_a_phase_b_logger_are_read_by_their_first_21_fields(conn):
    """A logger built with PHASE_B_ENABLED 1 appends six fields after `status`. Over the
    serial line there is no header: the row must still be read, the six left alone."""
    assert sorted(model.FORMATS) == [14, 16, 21] and sorted(model.PHASE_B_FORMATS) == [27]
    row = ("2026-12-01,06:00:00,40.0,40.1,40.2,40.3,40.4,30.0,40.0,800,17.5,5.0,87.5,NaN,0.01,1,"
           "0.4600,hold,0,20000,0,7,1,0.600,1.493,0.2489,19.900")
    assert len(row.split(",")) == 27
    serial = SerialIngest(conn, "COM9")
    assert serial.handle_line(row, wall=1000.0) == "row"
    got = conn.execute("SELECT T3, irradiance, alarm, load_mode, status FROM samples").fetchone()
    assert (got["T3"], got["irradiance"], got["alarm"], got["load_mode"], got["status"]) == (
        40.2, 800.0, 1, "hold", 0)


def test_coded_marker_rows_are_stored_with_the_name_of_their_code(conn, tmp_path):
    (tmp_path / "20261201.csv").write_text(NEW_HEADER + "\n")
    (tmp_path / "markers.csv").write_text(
        "date,time,millis,code\n2026-12-01,03:10:00.137,160137,0\n"
        "2026-12-01,07:30:00.274,7000274,30\n2026-12-01,07:42:00.411,7720411,31\n")
    assert FolderIngest(conn, tmp_path).poll(wall=1000.0)["markers"] == 3
    notes = [r["note"] for r in conn.execute("SELECT note FROM markers ORDER BY millis")]
    assert notes == [None, "load off", "load on"]
    serial = SerialIngest(conn, "COM9")                         # the logger prints the name too
    assert serial.handle_line("# marker (mesh on): 2026-12-01,08:00:00.000,9000000,10") == "marker"
    assert conn.execute("SELECT note FROM markers WHERE millis = 9000000").fetchone()["note"] == "mesh on"
