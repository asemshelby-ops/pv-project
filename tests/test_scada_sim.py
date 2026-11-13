"""The rooftop twin as a SIMULATION source of the SCADA: its own file, its own alarms, its
own page -- and nothing of it in the historian of the real module."""
import json
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest

from scada import alarms, historian, model, run, server, sim_source
from tests.test_twin_controllers import Stub, lull_day, obs
from tests.test_twin_rooftop import weather
from twin import SIM_NOTICE, params

P = params.load(None, None)


def feeder(tmp_path, controller="none", w=None, p=None):
    return sim_source.Feeder(tmp_path / "simulation.sqlite", controller, p=p,
                             weathers=[w if w is not None else weather(n=40)],
                             weather_note="constant test weather")


def rec(n=6, t_max=40.0, alarm=(), bypassed=(), t_tank=30.0):
    """A plant record with just the fields the alarm conditions read."""
    return {"alarm": np.isin(np.arange(n), alarm), "t_string_max": np.full(n, t_max),
            "bypassed": np.isin(np.arange(n), bypassed).astype(int), "t_tank": t_tank,
            "t_zone": np.full(n, t_max - 1), "g": 800.0}


def test_simulation_file_is_its_own_and_other_historians_are_refused(tmp_path):
    live = tmp_path / "historian.sqlite"
    run.open_historian(live, "live")
    with pytest.raises(SystemExit, match="not a simulation file"):
        sim_source.open_db(live)
    conn = historian.connect(live)
    assert historian.get_meta(conn, "mode") == "live"            # untouched
    conn.close()
    sim = tmp_path / "simulation.sqlite"
    sim_source.open_db(sim).close()
    conn = historian.connect(sim)
    assert historian.get_meta(conn, "mode") == "simulation"
    conn.close()
    with pytest.raises(SystemExit):                              # and the other way round
        run.open_historian(sim, "live")


def test_feeder_stores_simulated_steps_and_never_a_sample_of_the_real_module(tmp_path):
    f = feeder(tmp_path)
    for _ in range(10):
        assert f.step(wall=1000.0)
    conn = f.conn
    assert conn.execute("SELECT COUNT(*) FROM sim_plant").fetchone()[0] == 10
    assert conn.execute("SELECT COUNT(*) FROM sim_strings").fetchone()[0] == 60
    assert conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0
    rows = conn.execute("SELECT * FROM sim_plant ORDER BY t_utc").fetchall()
    # simulated time: 2026-12-01 10:00:00 Makkah = 07:00:00 UTC, 10 s apart
    assert rows[0]["t_utc"] == 1796108400 and rows[1]["t_utc"] - rows[0]["t_utc"] == 10
    # no cooling: the plant IS the no-cooling reference, and nothing is pumped
    assert rows[-1]["p_dc"] == pytest.approx(rows[-1]["p_ref"])
    assert rows[-1]["e_net_wh"] == pytest.approx(rows[-1]["e_ref_wh"]) and rows[-1]["pump_s"] == 0
    assert rows[-1]["e_net_wh"] == pytest.approx(sum(r["p_dc"] for r in rows) * 10 / 3600)
    f.close()


def test_feeder_ends_after_the_last_day_and_says_so(tmp_path):
    f = feeder(tmp_path, w=weather(n=3))
    assert [f.step(), f.step(), f.step(), f.step()] == [True, True, True, False]
    conn = historian.connect(f.db)
    assert json.loads(historian.get_meta(conn, "sim_progress"))["finished"] is True
    conn.close()
    f.close()


def test_state_and_series_carry_the_simulation_label(tmp_path):
    f = feeder(tmp_path, "continuous")
    for _ in range(40):
        f.step(wall=1000.0)
    st = sim_source.state(f.conn, {"speed": 120})
    assert st["simulation"] is True and st["label"] == SIM_NOTICE and st["available"]
    assert st["controller"] == "Continuous" and st["n_strings"] == 6
    assert len(st["strings"]) == 6 and np.shape(st["modules"]) == (6, 20)
    assert all(s["valve"] == 1 for s in st["strings"])
    assert st["latest"]["p_net"] == pytest.approx(
        st["latest"]["p_dc"] - st["latest"]["p_pump"] - st["latest"]["p_valves"])
    assert st["latest"]["p_dc"] > st["latest"]["p_ref"]           # cooled strings give more
    assert st["parameter_tags"]["[LIT-PLACEHOLDER]"] >= 25
    assert st["weather"] == "constant test weather"
    raw = sim_source.series(f.conn, 600)
    assert raw["simulation"] is True and raw["label"] == SIM_NOTICE
    assert len(raw["t"]) == 40 and raw["bucket_s"] == 10 and raw["valve"][-1] == [1] * 6
    assert raw["t_min"][-1] <= raw["t_mean"][-1] <= raw["t_max"][-1]
    coarse = sim_source.series(f.conn, 600, points=10)            # 60 s buckets
    assert coarse["bucket_s"] == 60 and len(coarse["t"]) <= 8
    assert coarse["valve"][-1] == [1] * 6                         # flags are OR-ed
    f.close()
    empty = sim_source.series(sim_source.open_db(tmp_path / "e.sqlite"), 600)
    assert empty["t"] == [] and empty["newest"] is None


def test_alarm_codes_and_texts_cannot_be_mistaken_for_the_real_module():
    defs = sim_source.alarm_defs(6)
    assert len(defs) == 19 and len({d.code for d in defs}) == 19
    assert all(d.code.startswith("SIM_") and d.text.startswith("SIMULATION:") for d in defs)
    assert not {d.code for d in defs} & set(model.ALARM_BY_CODE)
    c = sim_source.conditions(rec(alarm=[2], t_max=56.0, bypassed=[5], t_tank=39.0), 6)
    assert c["SIM_SPIKE_Z3"] and not c["SIM_SPIKE_Z1"]
    assert c["SIM_HOT_Z1"] and c["SIM_BYPASS_Z6"] and not c["SIM_BYPASS_Z1"]
    assert c["SIM_TANK_WARM"]
    assert not any(sim_source.conditions(rec(), 6).values())


def test_alarm_life_cycle_raised_cleared_acknowledged(tmp_path):
    conn = sim_source.open_db(tmp_path / "s.sqlite")
    eng = sim_source.AlarmEngine(6)
    t = 1000
    eng.evaluate(conn, t, rec(alarm=[0], t_max=56.0), wall=1.0)   # spike in zone 1, all hot
    listed = alarms.list_alarms(conn, "open")
    assert {a["code"] for a in listed} == {"SIM_SPIKE_Z1"} | {f"SIM_HOT_Z{z}" for z in range(1, 7)}
    assert all(a["text"].startswith("SIMULATION:") and "simulated" in a["detail"] for a in listed)
    eng.evaluate(conn, t + 10, rec(t_max=56.0), wall=2.0)         # the spike alarm lasts one step
    state = {a["code"]: a for a in alarms.list_alarms(conn, "open")}
    assert state["SIM_SPIKE_Z1"]["cleared"] == 1 and state["SIM_HOT_Z2"]["cleared"] == 0
    assert state["SIM_HOT_Z2"]["n_rows"] == 2
    for k in range(5):                                            # hot clears after 6 cool steps
        eng.evaluate(conn, t + 20 + 10 * k, rec(), wall=3.0)
    assert {a["code"]: a for a in alarms.list_alarms(conn, "open")}["SIM_HOT_Z2"]["cleared"] == 0
    eng.evaluate(conn, t + 70, rec(), wall=3.0)
    assert all(a["cleared"] for a in alarms.list_alarms(conn, "open"))
    assert alarms.acknowledge_all(conn, "tester") == 7
    assert alarms.list_alarms(conn, "open") == []                 # cleared and acknowledged
    events = [j["event"] for j in alarms.journal(conn)]
    assert events.count("RAISED") == 7 and events.count("CLEARED") == 7 and events.count("ACKED") == 7


def test_zone_monitor_watches_but_not_its_own_spray():
    stubs = []

    def make():
        stubs.append(Stub({3}))
        return stubs[-1]
    mon = sim_source.ZoneMonitor(P, 2, make)
    closed, open1 = np.array([False, False]), np.array([True, False])
    o = obs([50.0, 50.0], n_sensors=5)
    assert not mon.step(o, closed).any() and not mon.step(o, closed).any()
    assert list(mon.step(o, closed)) == [True, True]              # third update of each stub
    for _ in range(4):
        mon.step(o, open1)                                        # zone 1 sprayed: not watched
    assert (stubs[0].fed, stubs[1].fed) == (3, 7)
    blank = int(P.spike_blank_s / P.dt_s)
    for _ in range(blank - 1):
        mon.step(o, closed)
    assert stubs[0].fed == 3 and stubs[0].resets == 1             # still blanked
    mon.step(o, closed)
    assert stubs[0].fed == 4 and stubs[0].resets == 2             # restarted, then fed again


def test_feeder_shows_detector_alarms_whatever_controller_runs(tmp_path, monkeypatch):
    """With a controller that has no detector, a watching detector supplies the alarms."""
    from twin import controllers
    monkeypatch.setattr(sim_source, "monitor_detector",
                        lambda: (controllers.threshold_factory(2.5), "threshold rule (test)"))
    f = feeder(tmp_path, "none", w=lull_day())
    while f.step(wall=1.0):
        pass
    conn = historian.connect(f.db)
    assert json.loads(historian.get_meta(conn, "sim_info"))["alarm_detector"] == "threshold rule (test)"
    codes = [a["code"] for a in alarms.list_alarms(conn, "all")]
    assert sum(c.startswith("SIM_SPIKE_Z") for c in codes) >= 10      # warm-up and the lull
    lull_start = 1796104800 + 540 * 10                                # 09:00 local + 90 min
    after = conn.execute("SELECT COUNT(*) FROM alarms WHERE code LIKE 'SIM_SPIKE%' AND "
                         "t_raised BETWEEN ? AND ?", (lull_start, lull_start + 1500)).fetchone()[0]
    assert after >= 4
    assert conn.execute("SELECT SUM(valve) FROM sim_strings").fetchone()[0] == 0   # it only watched
    conn.close()
    f.close()


def test_controller_names_and_unknown_controller(tmp_path):
    assert sim_source.make_controller("predictive", P).name.startswith("Predictive")
    assert sim_source.make_controller("none", P).name == "No cooling"
    assert "threshold" in sim_source.make_controller("threshold", P).name
    with pytest.raises(SystemExit, match="unknown controller"):
        sim_source.make_controller("magic", P)
    with pytest.raises(FileNotFoundError):                        # nasim needs the pipeline's run
        from twin import controllers
        controllers.nasim_factory(tmp_path)


def test_http_rooftop_page_and_api_are_separate_from_the_real_module(tmp_path):
    live = tmp_path / "historian.sqlite"
    run.open_historian(live, "live")
    f = feeder(tmp_path, "continuous", p=P)
    for _ in range(5):
        f.step(wall=1.0)
    sim_source.AlarmEngine(6).evaluate(f.conn, 999, rec(t_max=60.0), wall=1.0)
    f.conn.commit()
    httpd = server.make_server(live, {"mode": "live"}, port=0, sim_db_path=f.db,
                               sim_info={"speed": 120})
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(path):
        with urllib.request.urlopen(base + path) as r:
            return r.read().decode("utf-8")

    def post(path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    try:
        page = get("/rooftop")
        assert "SIMULATION" in page and "only real data source" in page
        assert "/api/sim/" in get("/rooftop.js") and "/api/state" not in get("/rooftop.js")
        assert "Rooftop twin (SIMULATION)" in get("/")            # the link on the real page
        st = json.loads(get("/api/sim/state"))
        assert st["simulation"] is True and st["label"] == SIM_NOTICE and st["speed"] == 120
        assert len(json.loads(get("/api/sim/series?span=600"))["t"]) == 5
        sim_alarms = json.loads(get("/api/sim/alarms"))
        assert len(sim_alarms) == 6 and all(a["code"].startswith("SIM_") for a in sim_alarms)
        # the real module's API knows nothing of the simulation
        real = json.loads(get("/api/state"))
        assert real["latest"] is None and real["counts"]["samples"] == 0
        assert real["alarms"] == {"active": 0, "unacked": 0, "top_priority": None}
        assert json.loads(get("/api/alarms")) == []
        # acknowledging on the simulation page touches the simulation file only
        assert post("/api/sim/ack", {"id": "all", "by": "tester"}) == {"acknowledged": 6}
        assert post("/api/ack", {"id": "all", "by": "tester"}) == {"acknowledged": 0}
        with pytest.raises(urllib.error.HTTPError):
            get("/api/sim/nothing")
    finally:
        httpd.shutdown()
        httpd.server_close()
        f.close()
    conn = historian.connect(live)
    assert conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM alarms").fetchone()[0] == 0
    assert conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'sim_%'").fetchall() == []
    conn.close()


def test_without_a_simulation_the_page_says_how_to_start_it(tmp_path):
    live = tmp_path / "historian.sqlite"
    run.open_historian(live, "live")
    httpd = server.make_server(live, {"mode": "live"}, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/api/sim/state") as r:
            st = json.loads(r.read())
        assert st == {**server.SIM_OFF} and "--sim" in st["hint"] and st["available"] is False
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/api/sim/series")
        req = urllib.request.Request(base + "/api/sim/ack", data=b'{"id": "all", "by": "x"}',
                                     headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(req)
    finally:
        httpd.shutdown()
        httpd.server_close()
