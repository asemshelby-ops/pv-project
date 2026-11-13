"""HMI server: a small JSON API and three static files, standard library only.

It listens on 127.0.0.1 only: there is no login, so it must not be reachable from the
network (remote access is on the roadmap, docs/scada/roadmap.md). The only thing the HMI
can change is the acknowledgement of an alarm; it cannot reach the logger.

  GET  /api/model      tags, bits and alarm definitions (scada/model.py)
  GET  /api/state      newest row decoded, sensor health, alarm counts, ingest counters
  GET  /api/series     ?span=<s>&end=<unix s>&points=<n>   time series for the charts
  GET  /api/alarms     ?which=open|all
  GET  /api/journal
  POST /api/ack        {"id": n | "all", "by": "name", "note": "..."}
"""
import json
import math
import pathlib
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from data.schema import BACK_SENSORS
from scada import alarms, historian, model

WEB = pathlib.Path(__file__).resolve().parent / "web"
STATIC = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
          "/style.css": ("style.css", "text/css")}
ROW_S = 10                      # the logger's sample period
MAX_SPAN_S = 31 * 86400
SERIES = ["T_module", "T_ambient", "irradiance", "power", "voltage", "current", "p", "D"]


def _mean(values):
    v = [x for x in values if x is not None]
    return sum(v) / len(v) if v else None


def _or(values):
    v = [x for x in values if x is not None]
    out = 0
    for x in v:
        out |= x
    return out if v else None


def series(conn, span, end=None, points=1500):
    """Rows of the last `span` seconds up to `end` (default: the newest row). When there
    are more rows than `points`, they are grouped into equal time buckets: analog values
    are averaged; `alarm` and `status` are OR-ed, so no alarm or fault disappears;
    `load_mode` is "trip" if any row tripped, else the last value."""
    newest = conn.execute("SELECT MAX(t_utc) FROM samples").fetchone()[0]
    span = int(min(max(span, 60), MAX_SPAN_S))
    out = {"t": [], "alarm": [], "status": [], "load_mode": [], **{k: [] for k in SERIES},
           "markers": [], "span": span, "bucket_s": ROW_S, "end": end, "newest": newest}
    if newest is None:
        return out
    end = newest if end is None else int(end)
    start = end - span
    bucket = max(ROW_S, math.ceil(span / points / ROW_S) * ROW_S)
    out.update(end=end, bucket_s=bucket)
    rows = conn.execute(
        f"SELECT t_utc, {', '.join(BACK_SENSORS)}, T_ambient, irradiance, power, voltage, "
        "current, p, D, alarm, status, load_mode FROM samples WHERE t_utc > ? AND t_utc <= ? "
        "ORDER BY t_utc", (start, end)).fetchall()
    groups = {}
    for r in rows:
        groups.setdefault(r["t_utc"] // bucket, []).append(dict(r))
    for key, g in groups.items():
        out["t"].append(g[0]["t_utc"] if bucket == ROW_S else key * bucket)
        out["T_module"].append(_mean([model.t_module(r) for r in g]))
        for k in SERIES[1:]:
            out[k].append(_mean([r[k] for r in g]))
        out["alarm"].append(_or([r["alarm"] for r in g]))
        out["status"].append(_or([r["status"] for r in g]))
        modes = [r["load_mode"] for r in g if r["load_mode"] is not None]
        out["load_mode"].append("trip" if "trip" in modes else modes[-1] if modes else None)
    out["markers"] = [dict(m) for m in conn.execute(
        "SELECT t_utc_ms, note FROM markers WHERE t_utc_ms > ? AND t_utc_ms <= ? ORDER BY "
        "t_utc_ms", (start * 1000, end * 1000 + 999))]
    return out


def state(conn, info):
    row = historian.latest(conn)
    if row:
        tm = model.t_module(row)
        row["T_module"] = tm
        row["dT_module_ambient"] = (tm - row["T_ambient"]
                                    if tm is not None and row["T_ambient"] is not None else None)
        row["alarm_names"] = model.alarm_names(row["alarm"])
        row["status_names"] = model.status_names(row["status"])
    count = lambda table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: E731
    bad = sum(json.loads(r[0]).get("bad_lines", 0)
              for r in conn.execute("SELECT state_json FROM ingest_files"))
    last = historian.get_meta(conn, "last_row_wall")
    return {**info, "server_wall": time.time(),
            "last_row_wall": float(last) if last is not None else None,
            "latest": row, "health": model.health(row), "alarms": alarms.summary(conn),
            "counts": {"samples": count("samples"), "unplaced_rows": count("unplaced_rows"),
                       "markers": count("markers"), "bad_lines": bad}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):               # keep the console for the ingest messages
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path in STATIC:
            name, ctype = STATIC[url.path]
            return self._send(200, (WEB / name).read_bytes(), ctype)
        conn = historian.connect(self.server.db_path)
        try:
            if url.path == "/api/model":
                body = model.as_json()
            elif url.path == "/api/state":
                body = state(conn, self.server.info)
            elif url.path == "/api/series":
                body = series(conn, float(q.get("span", 3 * 3600)),
                              float(q["end"]) if q.get("end") else None,
                              int(q.get("points", 1500)))
            elif url.path == "/api/alarms":
                body = alarms.list_alarms(conn, "all" if q.get("which") == "all" else "open")
            elif url.path == "/api/journal":
                body = alarms.journal(conn)
            else:
                return self._send(404, {"error": "not found"})
            self._send(200, body)
        except ValueError as e:
            self._send(400, {"error": str(e)})
        finally:
            conn.close()

    def do_POST(self):
        # A JSON content type cannot be sent by a plain form on another site.
        if self.path != "/api/ack" or \
                not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._send(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            by = str(body.get("by", "")).strip()[:40]
            note = str(body.get("note", "")).strip()[:200]
            if not by:
                raise ValueError("the operator's name is required to acknowledge")
            conn = historian.connect(self.server.db_path)
            try:
                if body.get("id") == "all":
                    n = alarms.acknowledge_all(conn, by, note)
                else:
                    n = int(alarms.acknowledge(conn, int(body["id"]), by, note))
                conn.commit()
            finally:
                conn.close()
            self._send(200, {"acknowledged": n})
        except (ValueError, KeyError, TypeError) as e:
            self._send(400, {"error": str(e)})


def make_server(db_path, info, port=8050):
    """info: {"mode": "live" | "replay", ...} shown by the HMI. port 0 = any free port."""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.db_path, server.info = str(db_path), info
    return server

