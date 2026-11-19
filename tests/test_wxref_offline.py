"""No network: the weather reference warns and everything else carries on.

urllib.request.urlopen is replaced by one that fails like a PC without internet, so the
real transport code path (wxref.openmeteo.urllib_transport) is the one that fails."""
import threading
import urllib.error
import urllib.request
import warnings

import pandas as pd
import pytest

import config
from analysis import fake_logger, powerbi
from data import synth
from scada import historian
from scada.ingest import FolderIngest
from tests.wx_fixtures import SITE
from wxref import service

NOW = pd.Timestamp("2026-11-18 09:00", tz="Asia/Riyadh").timestamp()


def _no_network(*_a, **_k):
    raise urllib.error.URLError("[Errno -3] Temporary failure in name resolution")


@pytest.fixture
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(urllib.request, "urlopen", _no_network)
    monkeypatch.setattr(config, "WX_DIR", str(tmp_path / "wxref"))
    monkeypatch.setattr(config, "WX_SITE_LAT", SITE.lat)
    monkeypatch.setattr(config, "WX_SITE_LON", SITE.lon)


@pytest.fixture(scope="module")
def days():
    return synth.generate(seed=3, n_days=2, hard=False)[0]


def test_scada_service_without_network_warns_and_ingest_goes_on(offline, days, tmp_path):
    first, second = days[days["date"] == days["date"].iloc[0]], days[days["date"] != days["date"].iloc[0]]
    folder, db = tmp_path / "sd", tmp_path / "historian.sqlite"
    fake_logger.write_logger_files(first, folder)
    conn = historian.connect(db)
    historian.set_meta(conn, "mode", "live")
    ingest = FolderIngest(conn, folder)
    n1 = ingest.poll(wall=NOW)["rows"]
    logs = []
    svc = service.WeatherService(db, site=SITE, clock=lambda: NOW, log=logs.append)
    svc.tick()                                               # must not raise
    st = svc.status()
    assert st["state"] == "offline" and "name resolution" in st["last_error"]
    warned = [m for m in logs if m.startswith("WARNING weather reference")]
    assert sorted(m.split()[4] for m in warned) == ["forecast", "satellite"]   # one each
    svc.tick(NOW + 60)                                       # inside the back-off: no new warning
    assert sum(m.startswith("WARNING") for m in logs) == 2
    fake_logger.write_logger_files(second, folder)           # the logger goes on writing
    n2 = ingest.poll(wall=NOW + 70)["rows"]
    svc.tick(NOW + 80)                                       # new rows: the check runs again
    assert n1 > 0 and n2 > 0
    assert conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == n1 + n2
    body = service.api_payload(conn, svc.status(), NOW)      # the panel still answers
    assert body["service"]["state"] == "offline" and body["latest_check"]["status"] == "NO_DATA"
    assert "no result" in service.heartbeat_line(conn, "2099-01-01")
    conn.close()
    stop = threading.Event()                                 # the thread body survives too
    t = threading.Thread(target=svc.run, args=(stop, 0.01))
    t.start()
    stop.set()
    t.join(5)
    assert not t.is_alive()


def test_thread_survives_any_error(tmp_path):
    db = tmp_path / "h.sqlite"
    historian.connect(db).close()
    logs = []
    svc = service.WeatherService(db, site=SITE, log=logs.append)

    def boom(now=None):
        raise RuntimeError("database is locked")
    svc.tick = boom
    stop = threading.Event()
    t = threading.Thread(target=svc.run, args=(stop, 0.01))
    t.start()
    while not logs:
        pass
    stop.set()
    t.join(5)
    assert "database is locked" in logs[0] and svc.status()["state"] == "error"


def test_powerbi_without_network_warns_and_writes_every_table(offline, days, tmp_path):
    raw = tmp_path / "raw"
    fake_logger.write_logger_files(days, raw)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        tables = powerbi.run(raw, tmp_path / "pbi", log=lambda *a: None, weather=True)
    assert any("weather reference" in str(x.message) for x in w)
    for name in ("daily_summary", "spikes", "sweeps", "alarms", "wx_model_hourly"):
        assert (tmp_path / "pbi" / f"{name}.csv").exists()
    ds = tables["daily_summary"]
    assert ds["wx_model_poa_kWh_m2"].isna().all() and ds["pyr20_check_status"].isna().all()
    assert len(tables["wx_model_hourly"]) == 0
