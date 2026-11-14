"""scada.run --lan: listens on the local network only when asked, prints the address for
teammates and the no-login warning; without it the server stays on 127.0.0.1."""
import os
import pathlib
import queue
import subprocess
import sys
import threading
import time
import urllib.request

from scada import historian, run, server

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _db(tmp_path):
    db = tmp_path / "h.sqlite"
    conn = historian.connect(db)
    conn.close()
    return db


def test_default_host_is_loopback(tmp_path):
    httpd = server.make_server(_db(tmp_path), {"mode": "live"}, port=0)
    try:
        assert httpd.server_address[0] == "127.0.0.1"
    finally:
        httpd.server_close()


def test_lan_host_listens_on_all_interfaces(tmp_path):
    httpd = server.make_server(_db(tmp_path), {"mode": "live"}, port=0, host=run.LAN_HOST)
    try:
        assert httpd.server_address[0] == "0.0.0.0"
    finally:
        httpd.server_close()


def test_lan_address_is_none_or_a_non_loopback_ipv4():
    ip = run.lan_address()
    if ip is not None:
        parts = ip.split(".")
        assert len(parts) == 4 and all(0 <= int(p) <= 255 for p in parts)
        assert not ip.startswith("127.")


def _start(tmp_path, *extra):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    return subprocess.Popen(
        [sys.executable, "-m", "scada.run", "--folder", str(tmp_path / "sd"), "--no-telegram",
         "--port", "0", *extra],
        cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _lines(proc, until, timeout=30):
    """Lines printed by `proc` up to the first that starts with `until`. Read in a thread,
    so a missing line ends in a test failure after `timeout`, not in a hang."""
    q = queue.Queue()
    threading.Thread(target=lambda: [q.put(l) for l in proc.stdout] + [q.put(None)],
                     daemon=True).start()
    out, deadline = [], time.time() + timeout
    while time.time() < deadline:
        try:
            line = q.get(timeout=max(0.1, deadline - time.time()))
        except queue.Empty:
            break
        if line is None:
            break
        out.append(line.rstrip("\n"))
        if out[-1].startswith(until):
            break
    return out


def test_run_with_lan_prints_warning_and_address(tmp_path):
    (tmp_path / "sd").mkdir()
    proc = _start(tmp_path, "--lan")
    try:
        lines = _lines(proc, "If teammates cannot connect")
        text = "\n".join(lines)
        assert lines and lines[0].startswith("SCADA (live) on http://127.0.0.1:"), text
        assert lines[-1].startswith("If teammates cannot connect"), text
        assert "NO login" in text and "trusted network only" in text
        assert "Teammates on this network open: http://" in text or \
               "No local network address found" in text
        port = lines[0].split()[3].rsplit(":", 1)[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=5) as r:
            assert r.status == 200
    finally:
        proc.terminate()
        proc.wait(timeout=20)


def test_run_without_lan_has_no_warning(tmp_path):
    (tmp_path / "sd").mkdir()
    proc = _start(tmp_path)
    try:
        first = _lines(proc, "SCADA")[0]
        assert first.startswith("SCADA (live) on http://127.0.0.1:")
        time.sleep(1.0)
    finally:
        proc.terminate()
        out, _ = proc.communicate(timeout=20)
    assert "NO login" not in first + out and "Teammates" not in out
