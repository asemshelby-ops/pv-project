"""SCADA phone alerts (scada/notify.py) against a MOCKED Telegram Bot API: a local HTTP
server that answers like api.telegram.org, can fail (500, 401, 429), hang, or be absent.
No message leaves this machine. Rows and times are typed here; nothing is measured."""
import argparse
import calendar
import json
import pathlib
import re
import socket
import threading
import time
import urllib.request
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from data.schema import STATUS_BITS
from scada import alarms, historian, model, notify, run, server, sim_source

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOKEN = "123456789:" + "AAbbCCdd_-" * 4                       # shape of a real token, not one
CHAT = -1001234567890
BIT = {name: 1 << bit for name, bit in STATUS_BITS.items()}
T0 = calendar.timegm((2026, 11, 15, 6, 0, 0))                 # 09:00 Makkah


# --- the mocked Telegram API -------------------------------------------------------------------

class FakeTelegram:
    """Answers POST /bot<token>/<method> like the Bot API. mode: ok | 500 | 401 | 429 | hang."""

    def __init__(self):
        self.mode, self.retry_after, self.hang_s = "ok", 7, 2.0
        self.messages, self.attempts = [], 0
        self.in_flight = threading.Event()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                m = re.fullmatch(r"/bot([^/]+)/(\w+)", self.path)
                fake.attempts += 1
                if not m or m.group(1) != TOKEN or fake.mode == "401":
                    return self._answer(401, {"ok": False, "error_code": 401,
                                              "description": "Unauthorized"})
                if fake.mode == "500":
                    return self._answer(500, {"ok": False, "error_code": 500,
                                              "description": "Internal Server Error"})
                if fake.mode == "429":
                    return self._answer(429, {"ok": False, "error_code": 429,
                                              "description": "Too Many Requests",
                                              "parameters": {"retry_after": fake.retry_after}})
                if fake.mode == "hang":
                    fake.in_flight.set()
                    time.sleep(fake.hang_s)
                    fake.in_flight.clear()
                    return
                method = m.group(2)
                if method == "sendMessage":
                    fake.messages.append(payload)
                    return self._answer(200, {"ok": True, "result": {"message_id": len(
                        fake.messages), "chat": {"id": payload["chat_id"]}}})
                if method == "getUpdates":
                    return self._answer(200, {"ok": True, "result": [
                        {"update_id": 1, "message": {"chat": {"id": 42, "first_name": "Asim"}}},
                        {"update_id": 2, "message": {"chat": {"id": CHAT, "title": "PV rig"}}}]})
                return self._answer(404, {"ok": False, "description": "Not Found"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def texts(self):
        return [m["text"] for m in self.messages]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def tg():
    t = FakeTelegram()
    yield t
    t.close()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "historian.sqlite"
    run.open_historian(path, "live")
    return path


def closed_port_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


def client(url, timeout=2.0):
    return notify.TelegramClient(TOKEN, CHAT, api_url=url, timeout=timeout)


def make(db, url, now, logs=None):
    return notify.Notifier(db, client(url), clock=lambda: now,
                           log=(logs.append if logs is not None else lambda m: None))


def feed(db, rows, start, wall_lag=1.0):
    """One 10 s row per dict, evaluated by the alarm engine with wall = row time + lag."""
    conn = historian.connect(db)
    for k, r in enumerate(rows):
        row = dict.fromkeys(historian.SAMPLE_COLUMNS)
        row.update({"T1": 40.0, "T2": 40.0, "T3": 40.0, "T4": 40.0, "T5": 40.0,
                    "T_ambient": 30.0, "irradiance": 900.0, "alarm": 0, "status": 0,
                    "load_mode": "hold", "time_source": "rtc", "source": "test",
                    "t_utc": start + 10 * k, "ingested_wall": start + 10 * k + wall_lag})
        row.update(r)
        historian.insert_samples(conn, [row])
        alarms.evaluate(conn, start + 10 * k + wall_lag)
    conn.commit()
    conn.close()
    return start + 10 * len(rows)


def raise_direct(db, d, t, wall):
    conn = historian.connect(db)
    alarms._raise(conn, d, t, wall, "typed in the test", 1)
    conn.commit()
    conn.close()


# --- the client ----------------------------------------------------------------------------------

def test_client_posts_the_message_to_the_bot_api(tg):
    client(tg.url).send("hello")
    assert tg.messages == [{"chat_id": CHAT, "text": "hello", "disable_web_page_preview": True}]


def test_a_too_long_message_is_cut_to_telegrams_limit(tg):
    client(tg.url).send("x" * 5000)
    assert len(tg.messages[0]["text"]) <= notify.MAX_TEXT


@pytest.mark.parametrize("mode, expect", [("401", "HTTP 401: Unauthorized"),
                                          ("500", "HTTP 500"), ("429", "Too Many Requests")])
def test_client_errors_never_contain_the_token(tg, mode, expect):
    tg.mode = mode
    with pytest.raises(notify.TelegramError) as e:
        client(tg.url).send("x")
    assert expect in str(e.value) and TOKEN not in str(e.value)
    assert e.value.retry_after == (7.0 if mode == "429" else None)


def test_unreachable_and_hanging_api_raise_a_telegram_error_quickly(tg):
    with pytest.raises(notify.TelegramError, match="not reachable") as e:
        client(closed_port_url()).send("x")
    assert TOKEN not in str(e.value)
    tg.mode, t = "hang", time.monotonic()
    with pytest.raises(notify.TelegramError):
        client(tg.url, timeout=0.3).send("x")
    assert time.monotonic() - t < 1.5


# --- what is sent ----------------------------------------------------------------------------------

@pytest.mark.parametrize("d", model.ALARM_DEFS, ids=lambda d: d.code)
def test_every_alarm_of_priority_1_or_2_is_sent_and_priority_3_never(db, tg, d):
    n = make(db, tg.url, T0)
    raise_direct(db, d, T0, T0 + 1)
    n.tick(T0 + 2)
    assert tg.messages == []                                     # waits to group
    n.tick(T0 + 2 + notify.GROUP_WAIT_S)
    if d.priority in (1, 2):
        (text,) = tg.texts()
        assert f"RAISED {d.code} (P{d.priority}) {d.text}" in text
    else:
        assert tg.messages == [] and n.state["pending"] == {}


def test_the_named_alarms_have_priority_1_or_2_so_they_are_sent():
    for code in ("NO_DATA", "TEMP_FAIL", "IRRAD_FAIL", "PANEL_FAIL", "AMBIENT_FAIL",
                 "RTC_INVALID", "SD_ERROR"):
        assert model.ALARM_BY_CODE[code].priority in notify.NOTIFY_PRIORITIES, code


def test_a_sensor_fault_from_logger_rows_raised_then_cleared(db, tg):
    n = make(db, tg.url, T0)
    t = feed(db, [{"status": BIT["TEMP_FAIL"], "T3": None}], T0)
    n.tick(T0 + 30)
    (text,) = tg.texts()
    assert "\U0001F534 RAISED TEMP_FAIL (P1)" in text and "no value: T3" in text
    assert "2026-11-15 09:00:00" in text and "Active now: TEMP_FAIL (P1)" in text
    t = feed(db, [{}] * 200, t)                                  # clears after 3 good rows
    n.tick(T0 + 40)
    assert len(tg.messages) == 1                                 # still inside its 30 min
    n.tick(T0 + 30 + notify.RATE_LIMIT_S)
    text = tg.texts()[1]
    assert "✅ CLEARED TEMP_FAIL" in text and "was active 30 s" in text
    assert "Active now: none" in text


def test_several_alarms_at_once_are_one_message_high_priority_first(db, tg):
    n = make(db, tg.url, T0)
    feed(db, [{"status": BIT["TEMP_FAIL"] | BIT["IRRAD_FAIL"] | BIT["SD_RETRY"]
               | BIT["TROM_UNMAPPED"], "alarm": 1, "p": 0.97, "T2": None}], T0)
    n.tick(T0 + 30)
    (text,) = tg.texts()
    assert text.startswith("PV rig SCADA: 4 alarm events")
    order = [c for c in re.findall(r"RAISED (\w+)", text)]
    assert order == ["IRRAD_FAIL", "SD_ERROR", "TEMP_FAIL", "DET_NASIM"]
    assert "TROM_UNMAPPED (P" not in text.split("Active now")[0]  # P3: not an event line


def test_alarms_that_arrive_within_the_group_wait_go_together(db, tg):
    n = make(db, tg.url, T0)
    raise_direct(db, model.ALARM_BY_CODE["NO_DATA"], None, T0)
    n.tick(T0 + 1)
    raise_direct(db, model.ALARM_BY_CODE["PANEL_FAIL"], T0 + 10, T0 + 11)
    n.tick(T0 + 12)
    n.tick(T0 + 25)
    (text,) = tg.texts()
    assert "NO_DATA" in text and "PANEL_FAIL" in text


def test_no_more_than_one_message_per_alarm_type_every_30_minutes_and_nothing_lost(db, tg):
    """A detector that flickers on and off for 25 min, and another fault meanwhile."""
    n = make(db, tg.url, T0)
    start, sent_at = T0, {}
    for k in range(int(65 * 60 / 10)):
        t = start + 10 * k
        if k < 150:
            feed(db, [{"alarm": 1 if k % 2 == 0 else 0}], t)
        elif k == 150:
            feed(db, [{}] * 240, t)
        if k == 30:
            raise_direct(db, model.ALARM_BY_CODE["SD_ERROR"], t, t + 1)
        before = len(tg.messages)
        n.tick(t + 5)
        for text in tg.texts()[before:]:
            for code in set(re.findall(r"(?:RAISED |CLEARED |\U0001F501 )(\w+) \(P", text)):
                sent_at.setdefault(code, []).append(t + 5)
    nasim = sent_at["DET_NASIM"]
    assert len(nasim) >= 2
    assert all(b - a >= notify.RATE_LIMIT_S for a, b in zip(nasim, nasim[1:]))
    sd = sent_at["SD_ERROR"]                    # raised, then cleared by rows without the bit
    assert sd[0] == start + 300 + 5 + 20                         # not held by DET_NASIM's limit
    assert len(sd) == 2 and sd[1] - sd[0] >= notify.RATE_LIMIT_S
    sd_msg = next(x for x in tg.texts() if "SD_ERROR" in x)
    assert "DET_NASIM (P" not in sd_msg.split("Active now")[0]
    # every raise and clear of the journal reached the phone, folded or not
    raised = cleared = 0
    for text in tg.texts():
        for m in re.finditer(r"DET_NASIM \(P2\)[^\n]*\n\s+raised (\d+)x, cleared (\d+)x", text):
            raised, cleared = raised + int(m.group(1)), cleared + int(m.group(2))
        raised += text.count("RAISED DET_NASIM")
        cleared += text.count("CLEARED DET_NASIM")
    conn = historian.connect(db)
    j = [r["event"] for r in conn.execute("SELECT event FROM alarm_journal WHERE code = "
                                          "'DET_NASIM'")]
    conn.close()
    assert (raised, cleared) == (j.count("RAISED"), j.count("CLEARED")) == (75, 75)
    assert any("grouped by the 30-min limit" in x for x in tg.texts())


def test_no_data_alarm_from_the_pc_clock_is_sent(db, tg):
    n = make(db, tg.url, T0)
    feed(db, [{}], T0)
    conn = historian.connect(db)
    historian.set_meta(conn, "last_row_wall", T0 + 1)
    alarms.check_no_data(conn, T0 + 100)
    conn.commit()
    conn.close()
    n.tick(T0 + 130)
    (text,) = tg.texts()
    assert "\U0001F534 RAISED NO_DATA (P1) No data arriving from the logger" in text
    assert "no new row received for more than 60 s" in text


def test_simulation_alarms_are_never_sent(db, tg):
    n = make(db, tg.url, T0)
    for d in sim_source.alarm_defs(2):
        raise_direct(db, d, T0, T0 + 1)
    n.tick(T0 + 60)
    assert tg.messages == [] and n.state["pending"] == {}


@pytest.mark.parametrize("mode", ["replay", "simulation"])
def test_the_notifier_refuses_a_historian_that_is_not_live(tmp_path, tg, mode):
    path = tmp_path / "h.sqlite"
    conn = historian.connect(path)
    historian.set_meta(conn, "mode", mode)
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="live data of the real module only"):
        make(path, tg.url, T0)


def test_the_first_start_does_not_send_the_old_history(db, tg):
    feed(db, [{"status": BIT["PANEL_FAIL"]}, {}, {}, {}], T0 - 3600)
    n = make(db, tg.url, T0)
    n.tick(T0 + 60)
    assert tg.messages == []


# --- Telegram unreachable --------------------------------------------------------------------------

def test_unreachable_telegram_keeps_the_events_retries_later_and_survives_a_restart(db, tg):
    logs = []
    n = notify.Notifier(db, client(closed_port_url(), 0.5), clock=lambda: T0, log=logs.append)
    raise_direct(db, model.ALARM_BY_CODE["IRRAD_FAIL"], T0, T0)
    assert n.tick(T0 + 30) == 0                                  # no exception, nothing lost
    assert list(n.state["pending"]) == ["IRRAD_FAIL"]
    assert n.state["next_try"] == T0 + 30 + 30 and len(logs) == 1
    assert "not reachable" in logs[0] and TOKEN not in logs[0]
    n.tick(T0 + 40)                                              # before next_try: no try
    assert n.state["failures"] == 1
    n.tick(T0 + 61)
    assert n.state["failures"] == 2 and n.state["next_try"] == T0 + 61 + 60
    assert len(logs) == 1                                        # same error: not repeated
    assert n.status()["pending"] == ["IRRAD_FAIL"] and "not reachable" in n.status()["last_error"]
    # the SCADA restarts while Telegram is still down: the event is still pending
    n2 = make(db, tg.url, T0 + 100, logs)
    assert list(n2.state["pending"]) == ["IRRAD_FAIL"]
    n2.tick(T0 + 100)
    assert tg.messages == []                                     # waits for the saved next_try
    n2.tick(T0 + 121)
    (text,) = tg.texts()
    assert "RAISED IRRAD_FAIL" in text
    assert n2.state["failures"] == 0 and n2.state["pending"] == {}
    assert "delivered again" in logs[-1]


def test_a_429_answer_waits_as_long_as_telegram_asks(db, tg):
    tg.mode, tg.retry_after = "429", 120
    n = make(db, tg.url, T0)
    raise_direct(db, model.ALARM_BY_CODE["SD_ERROR"], T0, T0)
    n.tick(T0 + 30)
    assert n.state["next_try"] == T0 + 30 + 120
    tg.mode = "ok"
    n.tick(T0 + 100)
    assert tg.messages == []
    n.tick(T0 + 150)
    assert len(tg.messages) == 1


def test_the_rate_limit_counts_from_delivery_not_from_a_failed_try(db, tg):
    tg.mode = "500"
    n = make(db, tg.url, T0)
    raise_direct(db, model.ALARM_BY_CODE["SD_ERROR"], T0, T0)
    n.tick(T0 + 30)
    assert "SD_ERROR" not in n.state["last_sent"]
    tg.mode = "ok"
    n.tick(T0 + 61)
    assert n.state["last_sent"]["SD_ERROR"] == T0 + 61


def test_a_hanging_telegram_never_blocks_the_ingest(tmp_path, tg, monkeypatch):
    """Ingest and notifier in their threads, as scada.run starts them; Telegram hangs."""
    monkeypatch.setattr(notify, "GROUP_WAIT_S", 0.0)
    db = tmp_path / "historian.sqlite"
    run.open_historian(db, "live")
    folder = tmp_path / "sd"
    folder.mkdir()
    tg.mode, tg.hang_s = "hang", 3.0
    row = "2026-11-15,{},40,40,40,40,40,30,20,900,17.9,5.1,91.3,NaN,,0,NaN,hold,0,{},{}"
    header = ",".join(model.FORMAT_NEW)
    day = folder / "20261115.csv"
    day.write_text(header + "\n" + row.format("06:00:00", 1000, BIT["PANEL_FAIL"]) + "\n")
    n = notify.Notifier(db, client(tg.url, timeout=2.5), log=lambda m: None)
    stop = threading.Event()
    threads = [threading.Thread(target=run.ingest.run_folder, args=(db, folder, stop, 0.05)),
               threading.Thread(target=n.run, args=(stop, 0.05))]
    for t in threads:
        t.start()

    def count():
        with closing(historian.connect(db)) as c:
            return c.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
    try:
        assert tg.in_flight.wait(5), "the notifier never tried to send"
        t = time.monotonic()
        with day.open("a") as f:
            for k in range(1, 6):
                f.write(row.format(f"06:00:{10 * k:02d}", 1000 + 10000 * k, 0) + "\n")
        while count() < 6 and time.monotonic() - t < 2:
            time.sleep(0.02)
        assert count() == 6 and tg.in_flight.is_set()            # ingested during the hang
        assert time.monotonic() - t < 1.0
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=5)
    assert all(not t.is_alive() for t in threads)
    assert n.state["failures"] >= 1 and "PANEL_FAIL" in n.state["pending"]


# --- heartbeat ---------------------------------------------------------------------------------------

def heartbeat_rows(db):
    """Yesterday (Makkah 2026-11-14) 06:00-18:00 with a 70 degC peak at 13:00; today
    06:00-07:59:50 rising to 35 degC; an open fault."""
    y0, d0 = calendar.timegm((2026, 11, 14, 3, 0, 0)), calendar.timegm((2026, 11, 15, 3, 0, 0))
    ys = [{"T1": 70.0 if k == 7 * 360 else 40.0} for k in range(12 * 360)]
    ds = [{**{s: 20.0 + k / 48 for s in ("T1", "T3", "T4", "T5")}, "T2": None}
          for k in range(720)]
    ds[-1] = {"T1": 35.0, "T2": None, "T3": 35.0, "T4": 35.0, "T5": 35.0,
              "status": BIT["IRRAD_FAIL"]}
    feed(db, ys, y0)
    feed(db, ds, d0)
    return [y0 + 10 * k for k in range(len(ys))] + [d0 + 10 * k for k in range(len(ds))]


def test_heartbeat_at_8_makkah_once_a_day_with_the_numbers(db, tg):
    times = heartbeat_rows(db)
    eight = calendar.timegm((2026, 11, 15, 5, 0, 0))             # 08:00 Makkah
    n = make(db, tg.url, eight - 3600)                           # first start at 07:00
    n.tick(eight - 3600)
    n.tick(eight - 1)
    assert not any("heartbeat" in x for x in tg.texts())
    n.tick(eight)
    hb = [x for x in tg.texts() if "heartbeat" in x]
    assert len(hb) == 1
    text = hb[0]
    assert text.startswith("PV rig SCADA heartbeat 2026-11-15 08:00 Makkah\n")
    assert "LATE" not in text
    assert "Last row: 2026-11-15 07:59:50 Makkah (10 s ago)" in text
    n24 = sum(1 for t in times if eight - 86400 < t <= eight)
    assert f"Rows in the last 24 h: {n24} " in text
    assert "Max module temperature today so far: 35.0 degC at 07:59:50" in text
    # yesterday's peak row: mean of T1 = 70 and T2..T5 = 40 -> 46.0
    assert "Max module temperature yesterday: 46.0 degC at 13:00:00" in text
    assert "Open alarms: 1\n  IRRAD_FAIL (P1) since 2026-11-15 07:59:50" in text
    n.tick(eight + 1800)
    n.tick(eight + 86400 - 1)
    assert sum("heartbeat" in x for x in tg.texts()) == 1
    n.tick(eight + 86400)
    assert sum("heartbeat" in x for x in tg.texts()) == 2


def test_a_heartbeat_missed_while_the_scada_was_down_is_sent_late_at_restart(db, tg):
    eight = calendar.timegm((2026, 11, 15, 5, 0, 0))
    make(db, tg.url, eight - 7200).tick(eight - 7200)            # ran at 06:00, then stopped
    n = make(db, tg.url, eight + 4 * 3600)                       # back at 12:00
    n.tick(eight + 4 * 3600)
    (text,) = tg.texts()
    assert "heartbeat 2026-11-15 08:00 Makkah - LATE, sent 12:00:00" in text
    assert "Last row: none in the historian" in text and "Open alarms: 0 (none)" in text


def test_a_failed_heartbeat_is_retried(db, tg):
    eight = calendar.timegm((2026, 11, 15, 5, 0, 0))
    tg.mode = "500"
    n = make(db, tg.url, eight - 60)
    n.tick(eight)
    assert tg.messages == [] and n.state["heartbeat_day"] != notify.heartbeat_slot(eight)
    tg.mode = "ok"
    n.tick(eight + 31)
    assert len(tg.messages) == 1 and "heartbeat" in tg.texts()[0]


def test_heartbeat_slot_is_8_makkah():
    eight = calendar.timegm((2026, 11, 15, 5, 0, 0))
    assert notify.heartbeat_slot(eight) == notify.heartbeat_slot(eight - 1) + 1
    assert notify.day_iso(notify.heartbeat_slot(eight)) == "2026-11-15"
    assert notify.day_iso(notify.heartbeat_slot(eight - 1)) == "2026-11-14"


# --- configuration, start, API -------------------------------------------------------------------------

def write_config(path, **kw):
    path.write_text(json.dumps({"bot_token": TOKEN, "chat_id": CHAT, **kw}))
    return path


def test_config_is_read_and_its_repr_hides_the_token(tmp_path):
    cfg = notify.load_config(write_config(tmp_path / "c.json", label="Rig 1"))
    assert (cfg.bot_token, cfg.chat_id, cfg.label) == (TOKEN, CHAT, "Rig 1")
    assert TOKEN not in repr(cfg)
    assert notify.load_config(write_config(tmp_path / "c.json", chat_id="42")).chat_id == 42
    assert notify.load_config(write_config(tmp_path / "c.json", chat_id="@pv_rig_alerts")) \
        .chat_id == "@pv_rig_alerts"


@pytest.mark.parametrize("change, match", [
    ({"bot_token": "abc"}, "bot_token"), ({"chat_id": 0}, "chat_id"),
    ({"chat_id": None}, "chat_id"), ({"chat_id": True}, "chat_id"),
    ({"botToken": "x"}, "unknown keys")])
def test_bad_config_is_refused(tmp_path, change, match):
    with pytest.raises(notify.ConfigError, match=match):
        notify.load_config(write_config(tmp_path / "c.json", **change))


def test_the_committed_example_is_refused_until_filled_in_and_the_real_file_is_ignored():
    with pytest.raises(notify.ConfigError):
        notify.load_config(ROOT / "scada/telegram.example.json")
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert notify.CONFIG_PATH in ignored


def args(**kw):
    base = {"no_telegram": False, "telegram": None, "folder": "sd", "serial": None,
            "replay": None}
    return argparse.Namespace(**{**base, **kw})


def test_scada_run_starts_the_notifier_for_live_data_only(tmp_path, db, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    threads = []
    assert run.start_notifier(args(), db, threading.Event(), threads) is None   # no config
    assert "Telegram alerts: off" in capsys.readouterr().out
    write_config(tmp_path / "c.json")
    (tmp_path / "scada").mkdir()
    write_config(tmp_path / notify.CONFIG_PATH)
    assert run.start_notifier(args(no_telegram=True), db, threading.Event(), threads) is None
    assert run.start_notifier(args(folder=None), db, threading.Event(), threads) is None  # --sim
    n = run.start_notifier(args(), db, threading.Event(), threads)
    assert isinstance(n, notify.Notifier) and [t.name for t in threads] == ["telegram"]
    assert TOKEN not in capsys.readouterr().out
    n = run.start_notifier(args(telegram="c.json", folder=None, serial="COM5"), db,
                           threading.Event(), threads)
    assert isinstance(n, notify.Notifier)
    (tmp_path / notify.CONFIG_PATH).write_text("{}")
    with pytest.raises(SystemExit, match="bot_token"):
        run.start_notifier(args(), db, threading.Event(), [])


def test_api_state_reports_the_telegram_alerts(db, tg):
    n = make(db, tg.url, T0)
    for status, expect in ((None, {"enabled": False}), (n.status, n.status())):
        httpd = server.make_server(db, {"mode": "live"}, port=0, notify_status=status)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}"
                                        "/api/state", timeout=5) as r:
                assert json.loads(r.read())["telegram"] == expect
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_command_line_lists_chats_and_sends_a_test(tmp_path, tg, monkeypatch, capsys):
    monkeypatch.setattr(notify, "API_URL", tg.url)
    monkeypatch.setattr(notify.TelegramClient.__init__, "__defaults__", (tg.url, 2.0))
    cfg = write_config(tmp_path / "c.json")
    assert notify.main(["--config", str(cfg), "--chat-id"]) == 0
    out = capsys.readouterr().out
    assert "chat_id 42   Asim" in out and f"chat_id {CHAT}   PV rig" in out
    assert notify.main(["--config", str(cfg), "--test"]) == 0
    assert "test message" in tg.texts()[0]
    tg.mode = "401"
    assert notify.main(["--config", str(cfg), "--test"]) == 1
    assert TOKEN not in capsys.readouterr().err
