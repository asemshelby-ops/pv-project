"""wxref/service.py in the SCADA: own tables and files only, the panel only when switched
on, the heartbeat line. No live calls (tests/fixtures/openmeteo/ via a fake transport)."""
import argparse
import hashlib
import json
import threading
import urllib.error
import urllib.request

import pandas as pd
import pytest

import config
from analysis import fake_logger
from data import synth
from scada import historian, notify, run, server
from scada.ingest import FolderIngest
from tests.wx_fixtures import SITE, FakeTransport
from wxref import service, store
from wxref.openmeteo import Client

NOW = pd.Timestamp("2026-11-18 09:00", tz="Asia/Riyadh").timestamp()


@pytest.fixture(scope="module")
def days():
    return synth.generate(seed=3, n_days=3, hard=False)[0]          # 2026-11-15..17 local


@pytest.fixture
def live(days, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WX_DIR", str(tmp_path / "wxref"))
    folder = tmp_path / "sd"
    fake_logger.write_logger_files(days, folder)
    db = tmp_path / "historian.sqlite"
    conn = historian.connect(db)
    historian.set_meta(conn, "mode", "live")
    FolderIngest(conn, folder).poll(wall=NOW - 600)
    conn.close()
    return db, folder


def _samples_hash(db):
    conn = historian.connect(db)
    rows = conn.execute("SELECT * FROM samples ORDER BY t_utc").fetchall()
    conn.close()
    return hashlib.sha256(repr([tuple(r) for r in rows]).encode()).hexdigest()


def _folder_hash(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir())}


def test_service_writes_its_own_tables_and_csv_only(live, tmp_path, monkeypatch):
    db, folder = live
    before, files = _samples_hash(db), _folder_hash(folder)
    fake = FakeTransport()
    svc = service.WeatherService(db, site=SITE, clock=lambda: NOW, log=lambda m: None,
                                 client=Client(cache_dir=tmp_path / "wxref" / "cache",
                                               clock=lambda: NOW, transport=fake,
                                               log=lambda m: None))
    svc.tick(NOW)
    assert svc.status()["state"] == "ok" and fake.urls
    assert _samples_hash(db) == before and _folder_hash(folder) == files   # logger data untouched
    conn = historian.connect(db)
    assert store.has_tables(conn)
    model = store.read_model(conn)
    assert set(model["wx_source"]) == {"satellite", "forecast"} and len(model) > 48
    checks = store.read_checks(conn)
    assert list(checks["date"]) == ["2026-11-16", "2026-11-17"]          # complete days only
    assert (tmp_path / "wxref" / "wx_model.csv").exists()
    assert (tmp_path / "wxref" / "wx_daily_check.csv").exists()
    assert not any(p.name.startswith("wx_") for p in folder.iterdir())
    # a second tick inside the refresh time asks nothing new
    n = len(fake.urls)
    svc.tick(NOW + 120)
    assert len(fake.urls) == n

    body = service.api_payload(conn, svc.status(), NOW)
    assert body["available"] and body["label"].startswith("MODEL data")
    assert body["attribution"]["url"] == "https://open-meteo.com/"
    assert body["latest_check"]["date"] == "2026-11-17"
    assert body["today"]["date"] == "2026-11-18" and len(body["today"]["t"]) == 24
    json.dumps(body)                                        # plain JSON, no NaN objects

    line = service.heartbeat_line(conn, "2026-11-17")
    assert line.startswith("PYR20/model check 2026-11-17 (Open-Meteo MODEL reference)")
    monkeypatch.setattr(config, "WX_ENABLED", True)
    on = notify.heartbeat_text(conn, NOW, "PV rig")
    monkeypatch.setattr(config, "WX_ENABLED", False)
    off = notify.heartbeat_text(conn, NOW, "PV rig")
    assert line in on.splitlines() and line not in off and len(on.splitlines()) == len(
        off.splitlines()) + 1
    conn.close()


def _get(base, path):
    try:
        with urllib.request.urlopen(base + path) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_page_gets_the_panel_only_when_switched_on(live):
    db, _ = live
    page = (server.WEB / "index.html").read_bytes()
    for wx in (None, lambda conn: {"enabled": True, "available": False, "hint": "test"}):
        httpd = server.make_server(db, {"mode": "live"}, port=0, wx=wx)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            code, body = _get(base, "/")
            js, wx_api = _get(base, "/wxref.js"), _get(base, "/api/wx")
        finally:
            httpd.shutdown()
            httpd.server_close()
        if wx is None:
            assert body == page and js[0] == 404 and wx_api[0] == 404
        else:
            assert body == page.replace(b"</body>", server.WX_SCRIPT, 1)
            assert js[0] == 200 and b"Open-Meteo" in js[1]
            assert json.loads(wx_api[1])["hint"] == "test"


def test_scada_run_starts_the_weather_reference_only_when_on_and_live(live, monkeypatch):
    db, folder = live
    ns = argparse.Namespace(replay=None, folder=str(folder), serial=None)
    stop, threads = threading.Event(), []
    assert run.start_weather(ns, db, stop, threads) is None and not threads       # switch off
    monkeypatch.setattr(config, "WX_ENABLED", True)
    replay = argparse.Namespace(replay="demo", folder=None, serial=None)
    assert run.start_weather(replay, db, stop, threads)(None)["available"] is False
    assert not threads                                       # replay: a note, no thread
    sim_only = argparse.Namespace(replay=None, folder=None, serial=None)
    assert run.start_weather(sim_only, db, stop, threads) is None
    api = run.start_weather(ns, db, stop, threads)           # live: its own thread
    assert [t.name for t in threads] == ["weather"] and callable(api)


def test_a_weather_reference_that_cannot_start_does_not_stop_the_scada(live, monkeypatch):
    db, folder = live
    monkeypatch.setattr(config, "WX_ENABLED", True)

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(service, "WeatherService", broken)
    threads = []
    api = run.start_weather(argparse.Namespace(replay=None, folder=str(folder), serial=None),
                            db, threading.Event(), threads)
    assert not threads and "disk full" in api(None)["hint"]
