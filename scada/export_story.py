"""Export the story mode of the rooftop SIMULATION page for slides: an MP4 and PNG stills.

    python -m scada.export_story                       English, 1280x720, 25 frames/s
    python -m scada.export_story --lang ar             Arabic captions
    python -m scada.export_story --lang both --size 1920x1080
    python -m scada.export_story --gpu                 use this computer's GPU (faster)

It starts the SCADA server on a free port with the showcase data only (twin/showcase.py;
created first when it is missing), opens /rooftop?story=1 in headless Chromium and draws
the story frame by frame at a fixed time step, so the video is the same on every computer
however slow it draws. ffmpeg encodes the frames (H.264, plays in PowerPoint).

Files, in out/twin/showcase/ (not in git):
    story_<lang>.mp4                        the story, about 75 s
    stills/story_<lang>_NN_<chapter>.png    one picture per main chapter
    stills/compare_page.png                 the controller comparison of the page

Every frame is a picture of the page, with its label "SIMULATION - placeholder
parameters, not measured" in the corner: the label cannot be cut out of the export, and
the export stops if the page does not show it. Needs Node.js with Playwright and Chromium
(as the page tests do) and ffmpeg on the PATH.
"""
import argparse
import json
import pathlib
import shutil
import subprocess
import tempfile
import threading

from scada import run, server
from twin import VIEW_LABEL

CAPTURE = pathlib.Path(__file__).resolve().parent / "story_capture.cjs"


def export(out=server.SHOWCASE_DIR, langs=("en",), size=(1280, 720), fps=25, quality="high",
           gpu=False, log=print):
    """Render the story for each language. Returns [{lang, mp4, stills, frames, seconds}]."""
    node, ffmpeg = shutil.which("node"), shutil.which("ffmpeg")
    if node is None or ffmpeg is None:
        raise SystemExit("the export needs Node.js (with Playwright) and ffmpeg on the PATH")
    out = pathlib.Path(out)
    run.ensure_showcase(out)
    if not (out / "story.json").exists():
        raise SystemExit(f"{out} has no story.json: the simulated day has no cloud to tell a "
                         "story about (python -m twin.showcase)")
    stills = out / "stills"
    stills.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        live = pathlib.Path(tmp) / "historian.sqlite"       # an empty one: no real data shown
        run.open_historian(live, "live")
        httpd = server.make_server(live, {"mode": "live"}, port=0, showcase_dir=out)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            for n, lang in enumerate(langs):
                mp4 = out / f"story_{lang}.mp4"
                cfg = {"url": f"http://127.0.0.1:{httpd.server_address[1]}", "out_mp4": str(mp4),
                       "stills_dir": str(stills), "lang": lang, "width": size[0], "height": size[1],
                       "fps": fps, "quality": quality, "gpu": gpu, "label": VIEW_LABEL,
                       "ffmpeg": ffmpeg,
                       "compare_png": str(stills / "compare_page.png") if n == 0 else None}
                cfg_file = pathlib.Path(tmp) / f"capture_{lang}.json"
                cfg_file.write_text(json.dumps(cfg), encoding="utf-8")
                log(f"recording the story ({lang}, {size[0]}x{size[1]}, {fps} frames/s, "
                    f"quality {quality}) ...")
                res = subprocess.run([node, str(CAPTURE), str(cfg_file)], stdout=subprocess.PIPE,
                                     text=True, timeout=3 * 3600)
                lines = res.stdout.strip().splitlines()
                info = json.loads(lines[-1]) if lines else {"errors": ["no output"]}
                if res.returncode != 0 or info.get("errors"):
                    raise SystemExit("export failed: " + "; ".join(info.get("errors", [])))
                results.append({"lang": lang, "mp4": str(mp4), "stills": info["stills"],
                                "frames": info["frames"], "seconds": info["total"],
                                "label": info["label"]})
                log(f"  {mp4}  ({info['frames']} frames, {info['total']:.0f} s)")
                for s in info["stills"]:
                    log(f"  {s}")
        finally:
            httpd.shutdown()
            httpd.server_close()
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lang", choices=("en", "ar", "both"), default="en")
    ap.add_argument("--size", default="1280x720", help="WIDTHxHEIGHT (default 1280x720)")
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--quality", choices=("low", "medium", "high"), default="high")
    ap.add_argument("--gpu", action="store_true", help="draw with this computer's GPU")
    ap.add_argument("--out", default=server.SHOWCASE_DIR, help="folder of the showcase data")
    a = ap.parse_args()
    w, h = (int(v) for v in a.size.lower().split("x"))
    export(a.out, ("en", "ar") if a.lang == "both" else (a.lang,), (w, h), a.fps, a.quality, a.gpu)
    print(f"Every frame carries the label: {VIEW_LABEL}")


if __name__ == "__main__":
    main()
