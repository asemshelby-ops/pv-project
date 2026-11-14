"""The 3D view of the rooftop SIMULATION page: served from this computer (offline), the
vendored Three.js unchanged, and -- in a headless browser -- the page loads (camera intro),
follows the simulation, sprays the open zones, places the sun for the simulated time,
switches between the heat map and the realistic look, answers clicks and camera presets,
adapts its quality to the frame time, and shows our real module apart, in the same colour
scale, as the only measured one."""
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import threading
import urllib.request

import pandas as pd
import pvlib
import pytest

from scada import historian, run, server
from tests.test_scada_sim import feeder
from tests.test_scada_stack import _row
from tests.test_twin_rooftop import weather

WEB = pathlib.Path(server.__file__).resolve().parent / "web"
VENDOR = WEB / "vendor" / "three-0.160.0"
CHECK = pathlib.Path(__file__).resolve().parent / "browser" / "rooftop3d_check.cjs"


def _serve(tmp_path, controller="continuous", steps=0, real_rows=()):
    live = tmp_path / "historian.sqlite"
    run.open_historian(live, "live")
    if real_rows:
        conn = historian.connect(live)
        historian.insert_samples(conn, list(real_rows))
        conn.commit()
        conn.close()
    f = feeder(tmp_path, controller, w=weather(n=600))
    for _ in range(steps):
        f.step(wall=1.0)
    f.close()                       # SQLite: the thread that runs the feeder opens its own
    httpd = server.make_server(live, {"mode": "live"}, port=0, sim_db_path=f.db,
                               sim_info={"speed": 100})
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f, f"http://127.0.0.1:{httpd.server_address[1]}"


def test_three_is_vendored_unchanged_and_served_without_internet(tmp_path):
    readme = (VENDOR / "README.md").read_text(encoding="utf-8")
    for name in ("three.module.min.js", "OrbitControls.js", "LICENSE.txt"):
        digest = hashlib.sha256((VENDOR / name).read_bytes()).hexdigest()
        assert digest in readme, f"{name} differs from the file recorded in the README"
    page = (WEB / "rooftop.html").read_text(encoding="utf-8")
    js = (WEB / "rooftop3d.js").read_text(encoding="utf-8")
    # no CDN: nothing on the page or in the 3D code is fetched from another host
    assert not re.search(r"""(src|href)=["']https?://""", page)
    assert not re.search(r"""(import|from)\s*["']https?://""", js)
    imap = json.loads(re.search(r'<script type="importmap">(.*?)</script>', page, re.S).group(1))
    assert imap["imports"]["three"] == "/vendor/three-0.160.0/three.module.min.js"
    assert '<script type="module" src="rooftop3d.js">' in page
    # the simulation's own script still reads only /api/sim/; the 3D view reads /api/state
    # for one thing only, our real module
    assert "/api/state" not in (WEB / "rooftop.js").read_text(encoding="utf-8")
    assert js.count('"/api/state"') == 1 and "the only measured one" in js
    httpd, f, base = _serve(tmp_path)
    try:
        for path in ("/rooftop3d.js", "/vendor/three-0.160.0/three.module.min.js",
                     "/vendor/three-0.160.0/OrbitControls.js"):
            with urllib.request.urlopen(base + path) as r:
                assert r.status == 200 and r.headers["Content-Type"].startswith("text/javascript")
                assert len(r.read()) > 1000
        with urllib.request.urlopen(base + "/rooftop") as r:
            assert b'id="view3d"' in r.read()
    finally:
        httpd.shutdown()
        httpd.server_close()
        f.close()


def _browser_ready():
    node = shutil.which("node")
    if node is None:
        return None
    ok = subprocess.run([node, "-e", "require('playwright')"], capture_output=True)
    return node if ok.returncode == 0 else None


@pytest.mark.skipif(_browser_ready() is None, reason="needs Node.js with Playwright and Chromium")
def test_page_loads_follows_the_simulation_and_answers_clicks(tmp_path):
    t0 = 1_800_000_000
    httpd, f, base = _serve(tmp_path, "continuous", steps=3,
                            real_rows=[_row(t0), _row(t0 + 10)])     # T_module 41.0 degC
    stop = threading.Event()
    sim = threading.Thread(target=f.run, args=(stop, 50.0), daemon=True)  # a step every 0.2 s
    sim.start()
    try:
        res = subprocess.run([_browser_ready(), str(CHECK), base + "/rooftop"],
                             capture_output=True, text=True, timeout=420)
    finally:
        stop.set()
        sim.join(timeout=10)
        httpd.shutdown()
        httpd.server_close()
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["errors"] == [] and out["external"] == []
    first, later, check = out["first"], out["later"], out["check"]
    # loads, with the camera intro played and the permanent badge
    assert first["ready"] and first["error"] is None and first["dims"] == "6x20"
    assert first["introPlayed"] and first["view"] == "overview"
    assert out["badge"] == "SIMULATION"
    # updates: a newer simulated step is drawn, every module in the shared colour scale
    assert later["tUtc"] > first["tUtc"] and later["updates"] > first["updates"]
    assert check["wrongColours"] == [] and check["faces"] == {"heat": True, "realistic": False}
    # the continuous controller keeps every valve open: every zone sprays, valves show it
    assert check["valves"] == [1] * 6 and check["spraying"] == [True] * 6
    assert all(w > 0 for w in check["wet"])
    assert check["valveColour"] == check["series1"]
    # the drawn sun stands where the sun stood over Makkah at the simulated time
    sp = pvlib.solarposition.get_solarposition(pd.Timestamp(check["sunTUtc"], unit="s", tz="UTC"),
                                               21.4225, 39.8262)
    assert abs(check["sun"]["el"] - float(sp["apparent_elevation"].iloc[0])) < 1.0
    assert abs(check["sun"]["az"] - float(sp["azimuth"].iloc[0])) < 1.5
    # our real module: coloured by its measured temperature in the same scale
    assert later["realT"] == 41.0 and check["realColour"] == check["realExpected"]
    # clicks
    m, s, r = out["clickModule"], out["clickString"], out["clickReal"]
    assert m["sel"] == {"kind": "module", "s": 1, "m": 4}
    assert "SIMULATED" in m["text"] and "String 2, module 5" in m["text"] and "degC" in m["text"]
    assert s["sel"] == {"kind": "string", "s": 2}
    assert "String 3 · zone 3" in s["text"] and "OPEN" in s["text"]
    assert r["sel"] == {"kind": "real"}
    assert "MEASURED" in r["text"] and "only measured module" in r["text"]
    assert "41.0 degC" in r["text"] and "SIMULATED" not in r["text"]
    # module look: realistic hides the heat map, which still follows the data
    real = out["realistic"]
    assert real["info"]["mode"] == "realistic" and real["note"] is True
    assert real["check"]["faces"] == {"heat": False, "realistic": True}
    assert real["check"]["wrongColours"] == []
    assert out["heatAgain"] == {"heat": True, "realistic": False}
    # camera presets
    assert out["presentation"] == "presentation" and out["overview"] == "overview"
    # frame-time rule: down when slow, up only once and only when nearly all frames are fast
    assert out["rule"] == {"slowHigh": "medium", "steady30": "medium", "slowCpu": "low", "slowLow": "low",
                           "fastFirst": "high", "fastLater": "medium", "mixed": "medium"}
    auto = out["autoInfo"]
    assert auto["choice"] == "auto" and auto["quality"] in ("low", "medium", "high")
    assert auto["frameMs"] is not None and auto["frameMs"] > 0
    # light: animated while spraying on screen, nothing drawn off screen
    assert out["rendersOnScreen"] >= 3
    assert out["rendersOffScreen"] == 0
    # forced tiers: shadows only above low
    hi, lo = out["forced_high"], out["forced_low"]
    assert hi["quality"] == "high" and hi["shadows"] is True and not hi["introPlayed"]
    assert lo["quality"] == "low" and lo["shadows"] is False and lo["pixelRatio"] == 1
