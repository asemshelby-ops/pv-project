"""One server, both sources: `scada.run --replay ... --sim` (what start-scada.bat runs) serves
the real module's replay on / and the rooftop SIMULATION on /rooftop, each from its own file,
with a navigation bar on both pages."""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import time
import urllib.request

import pytest

from tests.test_scada_rooftop3d import _browser_ready
from tests.test_scada_stack import days, source   # noqa: F401  (fixtures)

ROOT = pathlib.Path(__file__).resolve().parents[1]
NAV_CHECK = pathlib.Path(__file__).resolve().parent / "browser" / "nav_check.cjs"


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.read().decode("utf-8")


def _tables(path):
    conn = sqlite3.connect(path)
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        samples = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] if "samples" in names else 0
        codes = [r[0] for r in conn.execute("SELECT code FROM alarms")] if "alarms" in names else []
    finally:
        conn.close()
    return names, samples, codes


def test_one_server_runs_the_replay_and_the_simulation_side_by_side(source, tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "scada.run", "--replay", str(source), "--speed", "3000",
         "--sim", "none", "--sim-days", "1", "--sim-speed", "3000", "--port", "0"],
        cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        first, second = proc.stdout.readline(), proc.stdout.readline()
        assert first.startswith("SCADA (replay) on http://127.0.0.1:"), first + second
        assert "Rooftop SIMULATION" in second and "/rooftop" in second
        base = first.split()[3]
        deadline = time.time() + 90
        while time.time() < deadline:
            real, sim = json.loads(_get(base + "/api/state")), json.loads(_get(base + "/api/sim/state"))
            if real["counts"]["samples"] > 50 and sim["latest"] is not None:
                break
            time.sleep(0.5)
        # both sources are served by the one process
        assert real["mode"] == "replay" and real["counts"]["samples"] > 50
        assert sim["available"] and sim["simulation"] is True and sim["latest"] is not None
        # the navigation bar on both pages, the current page marked
        for path, current in (("/", 'href="/" aria-current="page"'),
                              ("/rooftop", 'href="/rooftop" aria-current="page"')):
            page = _get(base + path)
            assert 'class="pages"' in page and 'href="/"' in page and 'href="/rooftop"' in page
            assert current in page and page.count('aria-current="page"') == 1
        if _browser_ready():
            res = subprocess.run([_browser_ready(), str(NAV_CHECK), base], capture_output=True,
                                 text=True, timeout=120)
            out = json.loads(res.stdout.strip().splitlines()[-1])
            assert out["errors"] == [], out["errors"]
            dash, roof, back = out["steps"]
            assert dash["path"] == "/" and dash["current"] == "Real module · dashboard"
            assert dash["mode"] == "REPLAY"
            assert roof["path"] == "/rooftop" and roof["current"].startswith("Rooftop twin")
            assert roof["badge"] == "SIMULATION"
            assert back["path"] == "/" and back["title"] == "PV rig SCADA"
    finally:
        proc.terminate()
        proc.wait(timeout=20)
    # the two sources stay in their own files
    names, samples, codes = _tables(tmp_path / "out/scada/replay.sqlite")
    assert samples > 50 and not any(n.startswith("sim_") for n in names)
    assert not any(c.startswith("SIM_") for c in codes)
    names, samples, codes = _tables(tmp_path / "out/scada/simulation.sqlite")
    assert {"sim_plant", "sim_strings"} <= names and samples == 0
    assert all(c.startswith("SIM_") for c in codes)
    assert not (tmp_path / "out/scada/historian.sqlite").exists()   # the live file is untouched


def test_launcher_runs_both_sources_and_old_launchers_are_gone():
    bat = (ROOT / "start-scada.bat").read_bytes()
    assert b"\r\n" in bat and b"\n" not in bat.replace(b"\r\n", b"")      # CRLF only
    text = bat.decode("ascii")
    assert '-m scada.run %REAL_ARGS% --sim' in text
    assert 'set "REAL_ARGS=--replay --speed 600"' in text and "scada-live.cfg" in text
    assert 'set "URL=http://127.0.0.1:8050"' in text and "CLOSE THIS WINDOW TO STOP IT." in text
    assert "git checkout" not in text and "twin-sim" not in text          # uses the checked-out branch
    assert not (ROOT / "start-rooftop.bat").exists() and not (ROOT / "start-scada-demo.bat").exists()
    assert "start-scada.bat" in (ROOT / "make-desktop-shortcuts.bat").read_text()
