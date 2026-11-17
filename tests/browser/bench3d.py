"""Frame-time benchmark of the 3D view of /rooftop (SIMULATION page), per quality tier.

    python tests/browser/bench3d.py                 software WebGL (SwiftShader): repeatable
    python tests/browser/bench3d.py --gpu           the computer's own GPU (your laptop)
    python tests/browser/bench3d.py --story         also time the story mode

It starts the SCADA server on a free port with the rooftop twin under the continuous
controller in constant weather (every zone sprays, so the view animates all the time),
opens the page in headless Chromium once per tier and module look, and prints a table:
milliseconds per drawn frame (from the view's own frame counter) and the median interval
between animation frames (the number the page shows as "ms/frame").

The numbers are frame times of a DRAWING; nothing here is a result of the twin.
Needs Node.js with Playwright and Chromium (as tests/test_scada_rooftop3d.py does).
"""
import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scada import run, server, sim_source          # noqa: E402
from tests.test_twin_rooftop import weather        # noqa: E402

SCRIPT = pathlib.Path(__file__).resolve().parent / "rooftop3d_bench.cjs"


def serve(tmp, warm_steps=90):
    live = tmp / "historian.sqlite"
    run.open_historian(live, "live")
    f = sim_source.Feeder(tmp / "simulation.sqlite", "continuous",
                          weathers=[weather(n=6000)], weather_note="constant bench weather")
    for _ in range(warm_steps):                     # 15 simulated minutes: warm, all valves open
        f.step(wall=1.0)
    f.close()
    httpd = server.make_server(live, {"mode": "live"}, port=0, sim_db_path=f.db,
                               sim_info={"speed": 10})
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    stop = threading.Event()
    threading.Thread(target=f.run, args=(stop, 10.0), daemon=True).start()   # a step a second
    return httpd, stop, f"http://127.0.0.1:{httpd.server_address[1]}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gpu", action="store_true", help="use the computer's GPU, not SwiftShader")
    ap.add_argument("--seconds", type=float, default=12.0, help="measuring time per case")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--story", action="store_true", help="also time the story mode")
    ap.add_argument("--json", help="also write the raw results to this file")
    a = ap.parse_args()
    node = shutil.which("node")
    if node is None:
        raise SystemExit("Node.js with Playwright is needed (see tests/test_scada_rooftop3d.py)")
    with tempfile.TemporaryDirectory() as tmp:
        httpd, stop, base = serve(pathlib.Path(tmp))
        try:
            res = subprocess.run(
                [node, str(SCRIPT), base + "/rooftop", str(a.seconds), str(a.repeat),
                 "gpu" if a.gpu else "swiftshader", "story" if a.story else "nostory"],
                capture_output=True, text=True, timeout=3600)
        finally:
            stop.set()
            httpd.shutdown()
            httpd.server_close()
    if res.returncode != 0:
        raise SystemExit(res.stderr or res.stdout)
    out = json.loads(res.stdout.strip().splitlines()[-1])
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(out, indent=2))
    print(f"3D view of /rooftop, {out['viewport']}, stage {out['stage']}, renderer: {out['renderer']}")
    print(f"{'case':28s} {'ms per drawn frame':>20s} {'median rAF interval, ms':>26s}")
    for c in out["cases"]:
        print(f"{c['name']:28s} {c['ms_per_frame']:20.1f} {c['raf_median_ms']:26.1f}")
    if out["errors"]:
        print("page errors:", *out["errors"], sep="\n  ")


if __name__ == "__main__":
    main()
