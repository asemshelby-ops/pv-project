"""SCADA: the tag model against the firmware and the schema, the replay, the demo fault
scenario, the HMI's API and the separation of live and replay historians.
All data is synthetic."""
import json
import pathlib
import re
import threading
import urllib.error
import urllib.request

import pytest

from analysis import fake_logger
from data import synth
from data.schema import ALARM_BITS, LOGGER_EXTRA_COLUMNS, STATUS_BITS
from scada import demo_data, historian, model, replay, run, server
from scada.ingest import FolderIngest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BIT = {name: 1 << bit for name, bit in STATUS_BITS.items()}


@pytest.fixture(scope="module")
def days():
    return synth.generate(seed=3, n_days=2, hard=False)[0]


@pytest.fixture
def source(days, tmp_path):
    src = tmp_path / "src"
    fake_logger.write_logger_files(days, src)
    dates = sorted(days["date"].unique())
    fake_logger.write_markers({d: [("06:10:00", "cleaning start", ""),
                                   ("06:20:00", "cleaning end", "")] for d in dates}, src)
    return src


class FakeClock:
    """Each call moves the time on by `step` seconds: a replay without waiting."""
    def __init__(self, step):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


# --- tag model ---------------------------------------------------------------------------------

def test_new_format_is_the_header_the_logger_writes():
    ino = (ROOT / "firmware/logger/logger.ino").read_text(encoding="utf-8")
    m = re.search(r'const char \*HEADER =\s*((?:"[^"]*"\s*)+);', ino)
    header = "".join(re.findall(r'"([^"]*)"', m.group(1))).split(",")
    assert header == model.FORMAT_NEW
    assert model.FORMAT_NEW[-5:] == list(LOGGER_EXTRA_COLUMNS)
    assert sorted(model.FORMATS) == [14, 16, 21]


def test_every_logged_column_and_every_bit_has_a_tag():
    names = {t.name for t in model.TAGS}
    assert set(model.FORMAT_NEW) <= names
    assert {f"ALM.{n}" for n in ALARM_BITS} | {f"ST.{n}" for n in STATUS_BITS} <= names
    assert set(model.STATUS_TEXT) == set(STATUS_BITS)             # every bit is explained
    assert len(names) == len(model.TAGS)                          # no tag twice
    stored = set(historian.SAMPLE_COLUMNS)
    assert set(model.FORMAT_NEW) - {"date", "time"} <= stored     # ... and has a column


def test_tag_list_document_is_up_to_date():
    assert model.TAG_LIST_PATH.read_text(encoding="utf-8") == model.tag_list_markdown(), \
        "run: python -m scada.model"


def test_health_of_a_row():
    row = dict.fromkeys(historian.SAMPLE_COLUMNS)
    row.update({"T1": 40.0, "T2": 40.0, "T3": None, "T4": 40.0, "T5": 40.0, "T_ambient": 30.0,
                "irradiance": 800.0, "voltage": 18.0, "time_source": "rtc",
                "status": BIT["TEMP_FAIL"] | BIT["TROM_UNMAPPED"] | BIT["DET_SKIPPED"]})
    h = {x["key"]: x["state"] for x in model.health(row)}
    assert h["T3"] == "fault" and h["T1"] == "ok" and h["rtc"] == "ok"
    assert h["mapping"] == "warn" and h["detectors"] == "warn" and h["sd"] == "ok"
    row["status"] = None                                          # old file: status not recorded
    h = {x["key"]: x["state"] for x in model.health(row)}
    assert h["T3"] == "fault" and h["sd"] == "unknown" and h["ambient"] == "unknown"
    assert model.t_module(row) == 40.0                            # mean of the four present
    assert model.health(None) == []


# --- replay ------------------------------------------------------------------------------------

def test_replay_writes_the_source_files_line_for_line(source, tmp_path):
    dest = replay.prepare_dest(tmp_path / "live")
    n = replay.feed(source, dest, speed=1.0, tick_s=0, clock=FakeClock(900.0))
    day_files = sorted(p.name for p in source.glob("2*.csv"))
    for name in day_files + ["markers.csv"]:
        assert (dest / name).read_text() == (source / name).read_text(), name
    assert n == sum(len((source / f).read_text().splitlines()) - 1
                    for f in day_files + ["markers.csv"])


def test_replay_is_progressive_and_the_ingest_follows_it(source, tmp_path):
    dest = replay.prepare_dest(tmp_path / "live")
    conn = historian.connect(":memory:")
    ingest, seen = FolderIngest(conn, dest), []
    clock = FakeClock(600.0)                                      # 10 min of data per step

    def tick():                                                   # the ingest polls between steps
        seen.append(ingest.poll()["rows"])
        return clock()

    replay.feed(source, dest, speed=1.0, tick_s=0, clock=tick)
    ingest.poll()
    total = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
    assert total == sum(len(f.read_text().splitlines()) - 1 for f in source.glob("2*.csv"))
    assert max(seen) <= 61 and sum(1 for n in seen if n) > 100    # in small steps, not at once
    assert conn.execute("SELECT COUNT(*) FROM markers").fetchone()[0] == 4
    conn.close()


def test_replay_shortens_the_night_between_two_days(source, tmp_path):
    dest = replay.prepare_dest(tmp_path / "live")
    clock = FakeClock(600.0)
    replay.feed(source, dest, speed=1.0, tick_s=0, clock=clock, max_gap_s=600.0)
    assert clock.t < 26 * 3600            # 2 x 12 h of data + a 10 min night, not 36 h


def test_replay_never_empties_a_folder_it_did_not_make(tmp_path):
    card = tmp_path / "sd_copy"
    card.mkdir()
    (card / "20261115.csv").write_text("date,time\n")
    with pytest.raises(SystemExit, match="not touched"):
        replay.prepare_dest(card)
    assert (card / "20261115.csv").exists()
    mine = replay.prepare_dest(tmp_path / "live", "a note")
    (mine / "20261115.csv").write_text("x\n")
    replay.prepare_dest(mine, "second run")                       # its own folder: emptied
    assert [p.name for p in mine.iterdir()] == [replay.SENTINEL]
    assert replay.source_note(mine) == "second run"
    assert replay.source_note(card) == f"files of {card}"


def test_replay_needs_day_files(tmp_path):
    with pytest.raises(FileNotFoundError):
        replay.load(tmp_path)


# --- demo fault scenario -------------------------------------------------------------------------

def test_injected_faults_set_the_bits_the_logger_would_set(days, tmp_path):
    fake_logger.write_logger_files(days, tmp_path)
    f = sorted(tmp_path.glob("2*.csv"))[0]
    date = f"{f.stem[:4]}-{f.stem[4:6]}-{f.stem[6:]}"
    before = f.read_text().splitlines()
    after = demo_data.inject_faults(before, date)
    col = {name: k for k, name in enumerate(before[0].split(","))}
    changed = [a.split(",") for a, b in zip(after, before) if a != b]
    assert len(changed) == sum(n for _, n, _, _ in demo_data.FAULTS) == 30
    t3 = [r for r in changed if int(r[col["status"]]) & BIT["TEMP_FAIL"]]
    assert all(r[col["T3"]] == "" and r[col["T1"]] != "" for r in t3)
    assert all(int(r[col["status"]]) & BIT["DET_SKIPPED"] for r in t3)
    assert [bool(int(r[col["status"]]) & BIT["DET_RESET"]) for r in t3] == \
        [False] * 4 + [True] + [False] * 4                        # at the 5th skipped row
    rtc = [r for r in changed if int(r[col["status"]]) & BIT["RTC_INVALID"]]
    assert len(rtc) == 6 and all(r[col["date"]] == r[col["time"]] == "" for r in rtc)
    panel = [r for r in changed if int(r[col["status"]]) & BIT["PANEL_FAIL"]]
    assert all(int(r[col["status"]]) == BIT["PANEL_FAIL"] for r in panel)   # detector goes on
    # the faulted file still loads, and every row finds its place in time
    f.write_text("\n".join(after) + "\n")
    conn = historian.connect(":memory:")
    assert FolderIngest(conn, tmp_path).poll()["rows"] == len(days)
    codes = {r[0] for r in conn.execute("SELECT code FROM alarms")}
    assert codes == {"TEMP_FAIL", "IRRAD_FAIL", "RTC_INVALID", "SD_ERROR", "PANEL_FAIL", "DET_RESET"}
    conn.close()


# --- API ---------------------------------------------------------------------------------------

def _row(t, **cells):
    row = dict.fromkeys(historian.SAMPLE_COLUMNS)
    row.update({"t_utc": t, "time_source": "rtc", "T1": 40.0, "T2": 42.0, "T_ambient": 30.0,
                "irradiance": 800.0, "voltage": 18.0, "power": 60.0, "alarm": 0, "status": 0,
                "load_mode": "hold", "source": "test", "ingested_wall": 0.0})
    row.update(cells)
    return row


def test_series_raw_and_bucketed_never_lose_an_alarm_or_a_fault():
    conn = historian.connect(":memory:")
    t0 = 1_800_000_000
    rows = [_row(t0 + 10 * k) for k in range(360)]                # one hour
    rows[100].update(alarm=4, status=BIT["SD_RETRY"])
    rows[101].update(alarm=1, load_mode="trip")
    rows[200].update(T1=None, T2=None, status=None, load_mode=None)
    historian.insert_samples(conn, rows)
    raw = server.series(conn, span=3600)
    assert raw["bucket_s"] == 10 and len(raw["t"]) == 360 and raw["end"] == t0 + 3590
    assert raw["T_module"][0] == 41.0 and raw["T_module"][200] is None
    assert (raw["alarm"][100], raw["alarm"][101], raw["status"][200]) == (4, 1, None)
    coarse = server.series(conn, span=3600, points=36)            # 100 s buckets
    assert coarse["bucket_s"] == 100 and len(coarse["t"]) <= 37
    k = coarse["t"].index((t0 + 1000) // 100 * 100)
    assert coarse["alarm"][k] == 5                                # 4 | 1
    assert coarse["status"][k] == BIT["SD_RETRY"] and coarse["load_mode"][k] == "trip"
    assert coarse["power"][k] == 60.0
    part = server.series(conn, span=600, end=t0 + 1200)
    assert part["t"][0] == t0 + 610 and part["t"][-1] == t0 + 1200
    assert server.series(historian.connect(":memory:"), span=3600)["t"] == []
    conn.close()


def test_http_api_state_series_alarms_and_acknowledge(tmp_path):
    db = tmp_path / "h.sqlite"
    conn = historian.connect(db)
    t0 = 1_800_000_000
    historian.insert_samples(conn, [_row(t0), _row(t0 + 10, alarm=2, p=0.4), _row(t0 + 20)])
    from scada import alarms
    alarms.evaluate(conn, 5.0)
    conn.commit()
    httpd = server.make_server(db, {"mode": "replay", "speed": 120, "replay_note": "test"}, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(path):
        with urllib.request.urlopen(base + path) as r:
            return json.loads(r.read())

    def post(body, ctype="application/json"):
        req = urllib.request.Request(base + "/api/ack", json.dumps(body).encode(),
                                     {"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        st = get("/api/state")
        assert st["mode"] == "replay" and st["latest"]["t_utc"] == t0 + 20
        assert st["latest"]["T_module"] == 41.0 and st["counts"]["samples"] == 3
        assert st["alarms"] == {"active": 0, "unacked": 1, "top_priority": 2}
        assert [h["state"] for h in st["health"]].count("fault") == 3      # T3..T5 not in _row
        assert get("/api/series?span=3600")["alarm"] == [0, 2, 0]
        assert {d["code"] for d in get("/api/model")["alarm_defs"]} == set(model.ALARM_BY_CODE)
        (a,) = get("/api/alarms")
        assert a["code"] == "DET_THRESHOLD" and a["state"] == "CLEARED_UNACKED"
        assert post({"id": a["id"], "by": ""})[0] == 400                    # a name is required
        assert post({"id": a["id"], "by": "asim"}, "text/plain")[0] == 404  # not a form post
        assert post({"id": a["id"], "by": "asim", "note": "ok"}) == (200, {"acknowledged": 1})
        assert post({"id": "all", "by": "asim"}) == (200, {"acknowledged": 0})
        assert get("/api/alarms") == []
        assert get("/api/alarms?which=all")[0]["acked_by"] == "asim"
        assert [j["event"] for j in get("/api/journal")] == ["ACKED", "CLEARED", "RAISED"]
        with urllib.request.urlopen(base + "/") as r:
            assert b"PV rig SCADA" in r.read()
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/../config.py")
    finally:
        httpd.shutdown()
        httpd.server_close()
        conn.close()


# --- live and replay historians never mix --------------------------------------------------------

def test_a_replay_empties_only_a_replay_historian(tmp_path):
    live, rep = tmp_path / "live.sqlite", tmp_path / "replay.sqlite"
    run.open_historian(live, "live")
    conn = historian.connect(live)
    historian.insert_samples(conn, [_row(1_800_000_000)])
    conn.commit()
    conn.close()
    with pytest.raises(SystemExit, match="not touched"):
        run.open_historian(live, "replay")                        # refuses the live file
    run.open_historian(live, "live")                              # reopening keeps the data
    conn = historian.connect(live)
    assert conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1
    conn.close()

    run.open_historian(rep, "replay")
    conn = historian.connect(rep)
    historian.insert_samples(conn, [_row(1_800_000_000)])
    conn.commit()
    conn.close()
    run.open_historian(rep, "replay")                             # a new replay starts empty
    conn = historian.connect(rep)
    assert conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0
    conn.close()
    with pytest.raises(SystemExit, match="not touched"):
        run.open_historian(rep, "live")


# --- no data arriving, in a replay ----------------------------------------------------------------

def test_no_data_alarm_in_a_replay_that_stops_and_resumes(source, tmp_path):
    """The folder ingest polls once per fake second while the replay writes. The replay
    ends after the first day; 61 s of silence raise the alarm; the second day clears it."""
    from scada import alarms
    dest = replay.prepare_dest(tmp_path / "live")
    conn = historian.connect(":memory:")
    ingest, wall = FolderIngest(conn, dest), [0.0]

    def poll_for(seconds):
        for _ in range(seconds):
            wall[0] += 1.0
            ingest.poll(wall=wall[0])

    def state():
        return [a["state"] for a in alarms.list_alarms(conn, "all") if a["code"] == "NO_DATA"]

    first, second = sorted(source.glob("2*.csv"))
    part = tmp_path / "part"
    part.mkdir()
    (part / first.name).write_text(first.read_text())
    clock = FakeClock(600.0)                                      # 10 min of data per step
    replay.feed(part, dest, speed=1.0, tick_s=0, clock=lambda: (poll_for(1), clock())[1])
    poll_for(1)
    assert state() == []                                          # rows kept arriving: no alarm
    poll_for(59)
    assert state() == []
    poll_for(2)                                                   # more than 60 s of silence
    assert state() == ["ACTIVE_UNACKED"]
    (dest / second.name).write_text(second.read_text())           # the replay goes on
    poll_for(1)
    assert state() == ["CLEARED_UNACKED"]
    conn.close()
