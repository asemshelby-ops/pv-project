"""Golden outputs for the weather-reference switch (config.WX_ENABLED, wxref/).

collect() runs every output path the weather reference touches -- the Power BI export, the
paper pipeline's text outputs, the twin validation, the SCADA historian, its HTTP answers and
the Telegram heartbeat -- on small SYNTHETIC inputs with fixed seeds and fixed clocks, and
returns {name: bytes}. tests/golden/wx_off.sha256.json holds the SHA-256 and size of what
collect() gave on `main` BEFORE the weather reference existed (written by
`python -m tests.golden_wx_off --write` on that code, commit 51d18aa, Python 3.13 with
requirements-lock.txt). tests/test_wxref_golden.py runs collect() with the switch off and
asserts every output is byte-identical (same hash). The outputs themselves are not stored
(~7 MB); to see a difference, run `--dump <folder>` here and on main and diff the folders.
Figures (PDF/PNG) are left out: they carry creation metadata.

Never rewrite the golden files to make the test pass: a difference means the switch-off path
changed. Regenerate only on unchanged code (e.g. after a library upgrade), and say so.
"""
import hashlib
import json
import pathlib
import threading
import urllib.error
import urllib.request

GOLDEN = pathlib.Path(__file__).resolve().parent / "golden" / "wx_off.sha256.json"
WALL = 1_795_190_000.0       # fixed PC clock for the ingest
TEXT_SUFFIXES = {".csv", ".md", ".json", ".tex", ".txt"}


def _noop(*_a, **_k):
    pass


def _synthetic_rig(root):
    """6 hard synthetic days written as rig files (as tests/test_analysis.py does) plus a
    gamma run, so the Power BI export has sweeps."""
    from analysis import fake_logger
    from data import synth
    days, drivers = synth.generate(seed=5, n_days=6, hard=True)
    raw = root / "raw"
    fake_logger.write_logger_files(days, raw)
    dates = sorted(days["date"].unique())
    schedule = {d: [("06:10:00", "cleaning start", ""), ("06:20:00", "cleaning end", "")]
                for d in dates}
    schedule[dates[0]] += [("11:05:00", "gamma shade on", ""), ("11:30:00", "gamma uncover", ""),
                           ("11:50:00", "gamma end", "")]
    fake_logger.write_markers(schedule, raw)
    fake_logger.write_gamma_runs([(dates[0], "11:30:00")], raw, seed=5)
    return raw, days, drivers


def _files(folder, prefix):
    out = {}
    for p in sorted(pathlib.Path(folder).rglob("*")):
        if p.is_file() and p.suffix in TEXT_SUFFIXES:
            out[f"{prefix}/{p.relative_to(folder).as_posix()}"] = p.read_bytes()
    return out


def _dump_historian(conn):
    """Every table, wall-clock columns and absolute paths left out, as JSON bytes."""
    skip = {"ingested_wall", "raised_wall", "acked_wall", "t_wall", "updated_wall"}
    out = {"schema": [r[0] for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name")]}
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                 "ORDER BY name").fetchall():
        rows = []
        for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid"):
            d = {k: r[k] for k in r.keys() if k not in skip}
            if table == "ingest_files":
                d["path"] = pathlib.Path(d["path"]).name
            if table == "meta" and d["key"] == "last_row_wall":
                continue
            rows.append(d)
        out[table] = rows
    return json.dumps(out, indent=1, sort_keys=True).encode("utf-8")


def _scada(root):
    """Ingest two standard synthetic days into a LIVE historian, then ask the HTTP API and
    the heartbeat what a client would see."""
    from analysis import fake_logger
    from data import synth
    from scada import historian, notify, server
    from scada.ingest import FolderIngest
    days, _ = synth.generate(seed=3, n_days=2, hard=False)
    folder = root / "sd"
    fake_logger.write_logger_files(days, folder)
    db = root / "historian.sqlite"
    conn = historian.connect(db)
    historian.set_meta(conn, "mode", "live")
    conn.commit()
    FolderIngest(conn, folder).poll(wall=WALL)
    end = conn.execute("SELECT MAX(t_utc) FROM samples").fetchone()[0]
    out = {"scada/historian.json": _dump_historian(conn),
           "scada/heartbeat.txt": notify.heartbeat_text(conn, end + 3600, "PV rig").encode()}
    conn.close()

    httpd = server.make_server(db, {"mode": "live", "folder": "sd", "serial": None}, port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(path):
        try:
            with urllib.request.urlopen(base + path) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    try:
        for name, path in (("index.html", "/"), ("app.js", "/app.js"),
                           ("style.css", "/style.css"), ("api_model.json", "/api/model"),
                           ("api_series.json", f"/api/series?span=86400&end={end}"),
                           ("api_alarms_all.json", "/api/alarms?which=all"),
                           ("get_api_wx.json", "/api/wx"), ("get_wxref_js.txt", "/wxref.js")):
            code, body = get(path)
            if name == "api_alarms_all.json":
                body = json.dumps([{k: v for k, v in a.items() if not k.endswith("_wall")}
                                   for a in json.loads(body)], sort_keys=True).encode()
            out[f"scada/http/{name}"] = f"{code}\n".encode() + body
        code, body = get("/api/state")
        st = json.loads(body)
        st.pop("server_wall")
        out["scada/http/api_state.json"] = (f"{code}\n".encode()
                                            + json.dumps(st, sort_keys=True).encode())
    finally:
        httpd.shutdown()
        httpd.server_close()
    return out


def collect(root):
    """All golden outputs, {name: bytes}. `root`: an empty scratch folder."""
    from analysis import ingest, pipeline, powerbi
    from twin import params, validate
    root = pathlib.Path(root)
    raw, days, drivers = _synthetic_rig(root)
    out = {}
    powerbi.run(raw, root / "powerbi", source="synthetic", log=_noop)
    out.update(_files(root / "powerbi", "powerbi"))
    pipeline.run(raw, root / "pipeline", drivers=drivers, seed=5, n_boot=20, synthetic=True,
                 log=_noop)
    out.update(_files(root / "pipeline", "pipeline"))
    validate.run(ingest.read_logger(raw), root / "twin", params.load(None, None),
                 "golden synthetic files", log=_noop)
    out.update(_files(root / "twin", "twin"))
    out.update(_scada(root))
    return out


def manifest(got):
    """{name: {"sha256": ..., "bytes": n}} of collected outputs."""
    return {k: {"sha256": hashlib.sha256(v).hexdigest(), "bytes": len(v)}
            for k, v in sorted(got.items())}


def differences(got):
    """Names whose bytes differ from the golden manifest (or are missing / new)."""
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    have = manifest(got)
    return sorted(k for k in set(have) | set(want) if have.get(k) != want.get(k))


if __name__ == "__main__":
    import argparse
    import tempfile
    ap = argparse.ArgumentParser(description="golden outputs with the weather reference off")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true", help=f"write {GOLDEN.name} (unchanged code only)")
    g.add_argument("--check", action="store_true", help=f"compare with {GOLDEN.name}")
    g.add_argument("--dump", metavar="FOLDER", help="write the outputs themselves, for a diff")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        got = collect(tmp)
    if a.write:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(manifest(got), indent=1) + "\n", encoding="utf-8")
        print(f"wrote {GOLDEN} ({len(got)} outputs)")
    elif a.check:
        bad = differences(got)
        print("identical" if not bad else "DIFFERENT: " + ", ".join(bad))
    else:
        for name, data in got.items():
            p = pathlib.Path(a.dump) / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        print(f"wrote {len(got)} outputs to {a.dump}")
