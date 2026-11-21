"""Phone alerts through a Telegram bot: alarm messages and a daily heartbeat.

    python -m scada.notify --chat-id      list the chats that wrote to the bot (setup)
    python -m scada.notify --test         send one test message
    python -m scada.notify --heartbeat    send the heartbeat of the live historian now
    python -m scada.notify --demo-alarm   send a sample alarm (raised + cleared), marked DEMO

Setup (BotFather, chat ID, config file): docs/scada/telegram.md.
`scada.run` starts the notifier by itself for LIVE data when scada/telegram.local.json
exists; never for a replay or the rooftop simulation.

What is sent
  * an alarm of priority 1 or 2 of the REAL module is raised or cleared (every code of
    scada/model.py with that priority, NO_DATA included). Codes starting with SIM_ are
    never sent, and the notifier refuses any historian that is not a live one;
  * at most one message per alarm code every RATE_LIMIT_S (30 min). Events of a code that
    arrive inside its window are held and folded into one line ("raised 3x, cleared 3x")
    in the first message after the window. Everything due at the same moment goes into
    ONE message; a new event waits GROUP_WAIT_S so alarms of the same fault arrive
    together;
  * a heartbeat every day at 08:00 Makkah time (UTC+3): last row, rows of the last 24 h,
    the day's maximum module temperature, the SD card (lines waiting in the logger's
    LittleFS ring, lines given up, SD events of the last 24 h), open alarms. If the SCADA stops, the heartbeat
    does not come: its absence is the warning. After a restart that missed 08:00, the
    heartbeat of that day is sent at once and marked late.

What it never does
  * block the ingest: it runs in its own thread with its own short-lived SQLite
    connections and only READS the historian (its own state is a JSON file next to it);
  * lose an event when Telegram is unreachable: the events stay pending (also across a
    restart) and the send is retried with a growing delay (30 s ... 15 min), or after the
    `retry_after` Telegram asks for;
  * print or log the bot token.
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from contextlib import closing
from dataclasses import dataclass

from data.schema import BACK_SENSORS
from scada import historian

CONFIG_PATH = "scada/telegram.local.json"       # git-ignored; never commit it
API_URL = "https://api.telegram.org"
MAKKAH_OFFSET_S = 3 * 3600                      # Asia/Riyadh, UTC+3, no daylight saving
NOTIFY_PRIORITIES = (1, 2)
RATE_LIMIT_S = 30 * 60
GROUP_WAIT_S = 20.0
HEARTBEAT_LOCAL_S = 8 * 3600                    # 08:00 Makkah
LATE_AFTER_S = 15 * 60                          # a heartbeat sent later than this is "late"
TICK_S = 5.0
RETRY_MIN_S, RETRY_MAX_S = 30.0, 15 * 60.0
TIMEOUT_S = 10.0
MAX_TEXT = 4000                                 # Telegram's limit is 4096 characters
BACKFILL_NOTE_S = 15 * 60                       # row time this much before reception: say so
TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")
STATE_VERSION = 1


# --- configuration ---------------------------------------------------------------------------

class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    bot_token: str
    chat_id: int | str
    label: str = "PV rig"

    def __repr__(self):                         # never show the token
        return f"Config(bot_token=<hidden>, chat_id={self.chat_id!r}, label={self.label!r})"


def load_config(path=CONFIG_PATH):
    """Read and check the local config file. Keys starting with "_" are comments."""
    path = pathlib.Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{path} not found (see docs/scada/telegram.md)") from None
    except ValueError as e:
        raise ConfigError(f"{path} is not valid JSON: {e}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a JSON object")
    raw = {k: v for k, v in raw.items() if not k.startswith("_")}
    unknown = set(raw) - {"bot_token", "chat_id", "label"}
    if unknown:
        raise ConfigError(f"{path}: unknown keys {sorted(unknown)}")
    token = str(raw.get("bot_token", "")).strip()
    if not TOKEN_RE.match(token):
        raise ConfigError(f"{path}: bot_token does not look like a BotFather token "
                          "(<digits>:<about 35 characters>)")
    chat = raw.get("chat_id")
    if isinstance(chat, str) and re.fullmatch(r"-?\d+", chat.strip()):
        chat = int(chat)
    if not (isinstance(chat, int) and not isinstance(chat, bool) and chat != 0) and \
            not (isinstance(chat, str) and re.fullmatch(r"@\w{5,}", chat)):
        raise ConfigError(f"{path}: chat_id must be a number (negative for a group) "
                          "or @channelname")
    return Config(token, chat, str(raw.get("label", "PV rig"))[:40])


# --- Telegram Bot API ------------------------------------------------------------------------

class TelegramError(Exception):
    """A call that did not succeed. `retry_after`: seconds Telegram asked to wait (429)."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class TelegramClient:
    """The two Bot API methods used here, over HTTPS with the standard library."""

    def __init__(self, token, chat_id, api_url=API_URL, timeout=TIMEOUT_S):
        self.token, self.chat_id = token, chat_id
        self.api_url, self.timeout = api_url.rstrip("/"), timeout

    @classmethod
    def from_config(cls, cfg, **kw):
        return cls(cfg.bot_token, cfg.chat_id, **kw)

    def _redact(self, text):
        return str(text).replace(self.token, "<token>")

    def _call(self, method, payload):
        req = urllib.request.Request(
            f"{self.api_url}/bot{self.token}/{method}", method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8"))
            except (ValueError, OSError):
                body = {}
            desc = body.get("description") or e.reason
            retry = (body.get("parameters") or {}).get("retry_after")
            raise TelegramError(self._redact(f"HTTP {e.code}: {desc}"),
                                float(retry) if retry is not None else None) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            reason = getattr(e, "reason", e)
            raise TelegramError(self._redact(f"Telegram not reachable: {reason}")) from None
        if not isinstance(body, dict) or not body.get("ok"):
            raise TelegramError(self._redact(f"Telegram refused: {body.get('description')}"
                                             if isinstance(body, dict) else "bad answer"))
        return body.get("result")

    def send(self, text):
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT - 20] + "\n... (cut)"
        return self._call("sendMessage", {"chat_id": self.chat_id, "text": text,
                                          "disable_web_page_preview": True})

    def updates(self):
        return self._call("getUpdates", {"timeout": 0})


# --- time and number formats -------------------------------------------------------------------

def local_dt(t):
    """Makkah wall time of a Unix second, as a naive datetime."""
    return datetime.datetime.fromtimestamp(t + MAKKAH_OFFSET_S, datetime.UTC).replace(tzinfo=None)


def fmt_time(t, with_date=True):
    return local_dt(t).strftime("%Y-%m-%d %H:%M:%S" if with_date else "%H:%M:%S")


def fmt_duration(s):
    s = max(0.0, float(s))
    if s < 120:
        return f"{s:.0f} s"
    if s < 2 * 3600:
        return f"{s / 60:.0f} min"
    if s < 2 * 86400:
        return f"{s / 3600:.1f} h"
    return f"{s / 86400:.1f} d"


def local_day(t):
    """Index of the Makkah calendar day of a Unix second."""
    return int((t + MAKKAH_OFFSET_S) // 86400)


def day_start(day):
    """Unix second of 00:00 Makkah of a day index."""
    return day * 86400 - MAKKAH_OFFSET_S


def heartbeat_slot(now):
    """Day index of the latest 08:00 Makkah at or before `now`."""
    day = local_day(now)
    return day if now >= day_start(day) + HEARTBEAT_LOCAL_S else day - 1


def day_iso(day):
    return (datetime.date(1970, 1, 1) + datetime.timedelta(days=day)).isoformat()


# --- what the historian says -------------------------------------------------------------------

_T_MODULE_SQL = ("(" + " + ".join(f"COALESCE({s}, 0)" for s in BACK_SENSORS) + ") / NULLIF("
                 + " + ".join(f"({s} IS NOT NULL)" for s in BACK_SENSORS) + ", 0)")


def max_module_temp(conn, start, end):
    """(max of the mean back-sheet temperature, its row time) over start <= t_utc < end,
    or (None, None)."""
    r = conn.execute(f"SELECT {_T_MODULE_SQL} AS tm, t_utc FROM samples "
                     "WHERE t_utc >= ? AND t_utc < ? AND tm IS NOT NULL "
                     "ORDER BY tm DESC, t_utc LIMIT 1", (start, end)).fetchone()
    return (r["tm"], r["t_utc"]) if r else (None, None)


def open_alarms(conn):
    """Active (not cleared) alarms of the real module, high priority first."""
    return [dict(r) for r in conn.execute(
        "SELECT code, priority, text, t_raised, raised_wall, acked_wall FROM alarms "
        "WHERE cleared = 0 AND code NOT LIKE 'SIM\\_%' ESCAPE '\\' ORDER BY priority, id")]


def _open_line(a):
    since = a["t_raised"] if a["t_raised"] is not None else a["raised_wall"]
    return (f"  {a['code']} (P{a['priority']}) since {fmt_time(since)}"
            + ("" if a["acked_wall"] is None else ", acknowledged"))


SD_EVENTS = ("sd_lost", "sd_back", "ring_full", "ring_error", "ring_flushed")


def sd_lines(conn, now):
    """The SD card in the heartbeat: the newest row's ring columns and the logger's SD
    events of the last 24 h. An outage alarms by itself (SD_OUTAGE, RING_DROPPED); this
    line is the daily summary."""
    r = conn.execute("SELECT ring_rows, ring_dropped, t_utc FROM samples "
                     "WHERE ring_rows IS NOT NULL ORDER BY t_utc DESC LIMIT 1").fetchone()
    if r is None:
        line = "SD card: not reported (no CSV v2 row in the historian)"
    elif r["ring_rows"] == 0:
        line = "SD card: up to date (0 lines waiting in the LittleFS ring)"
    else:
        line = f"SD card: NOT up to date, {r['ring_rows']} lines wait in the LittleFS ring"
    if r is not None and r["ring_dropped"]:
        line += f"; {r['ring_dropped']} lines given up"
    counts = conn.execute(
        f"SELECT event, COUNT(*) AS n FROM logger_events WHERE event IN "
        f"({', '.join('?' * len(SD_EVENTS))}) AND COALESCE(t_utc, ingested_wall) > ? "
        f"GROUP BY event", (*SD_EVENTS, now - 86400)).fetchall()
    got = {c["event"]: c["n"] for c in counts}
    events = ", ".join(f"{e} {got[e]}" for e in SD_EVENTS if e in got)
    return [line, "SD events in the last 24 h: " + (events or "none")]


def heartbeat_text(conn, now, label="PV rig", slot=None):
    slot = heartbeat_slot(now) if slot is None else slot
    due = day_start(slot) + HEARTBEAT_LOCAL_S
    late = now - due > LATE_AFTER_S
    lines = [f"{label} SCADA heartbeat {day_iso(slot)} 08:00 Makkah"
             + (f" - LATE, sent {fmt_time(now, False)}: the SCADA was not running at 08:00"
                if late else "")]
    last = conn.execute("SELECT MAX(t_utc) FROM samples").fetchone()[0]
    if last is None:
        lines.append("Last row: none in the historian")
    else:
        lines.append(f"Last row: {fmt_time(last)} Makkah ({fmt_duration(now - last)} ago)")
    n = conn.execute("SELECT COUNT(*) FROM samples WHERE t_utc > ? AND t_utc <= ?",
                     (now - 86400, now)).fetchone()[0]
    lines.append(f"Rows in the last 24 h: {n} (one row per 10 s all day would be 8640)")
    today = local_day(now)
    for name, d, end in (("today so far", today, now + 1), ("yesterday", today - 1, None)):
        tm, t = max_module_temp(conn, day_start(d), end if end is not None else day_start(d + 1))
        lines.append(f"Max module temperature {name}: "
                     + (f"{tm:.1f} degC at {fmt_time(t, False)}" if tm is not None else "no data"))
    lines += sd_lines(conn, now)
    alarms_ = open_alarms(conn)
    lines.append(f"Open alarms: {len(alarms_)}" + ("" if alarms_ else " (none)"))
    lines += [_open_line(a) for a in alarms_]
    lines.append("Next heartbeat tomorrow 08:00. No heartbeat = the SCADA is not running.")
    return "\n".join(lines)


# --- alarm message format (shared by the notifier and --demo-alarm) -----------------------------

def _when(ev):
    t = ev["t"] if ev["t"] is not None else ev["wall"]
    s = fmt_time(t)
    if ev["t"] is not None and ev["wall"] - ev["t"] > BACKFILL_NOTE_S:
        s += f" (row time; received {fmt_duration(ev['wall'] - ev['t'])} later)"
    return s


def _alarm_line(code, agg, held):
    head = f"{code} (P{agg['priority']}) {agg['text']}"
    n = agg["n_raised"] + agg["n_cleared"]
    ev = agg["last"]
    if n == 1:
        mark = ("\U0001F534" if agg["priority"] == 1 else "\U0001F7E0") \
            if ev["event"] == "RAISED" else "\u2705"
        out = f"{mark} {ev['event']} {head}\n   {_when(ev)}"
        if ev["event"] == "RAISED" and ev["detail"]:
            out += f" - {ev['detail']}"
        if ev["event"] == "CLEARED" and ev.get("active_s") is not None:
            out += f", was active {fmt_duration(ev['active_s'])}"
        return out
    now_state = "still ACTIVE" if ev["event"] == "RAISED" else "now cleared"
    return (f"\U0001F501 {head}\n   raised {agg['n_raised']}x, cleared {agg['n_cleared']}x "
            f"between {_when(agg['first'])} and {_when(ev)}; {now_state}"
            + (" (grouped by the 30-min limit)" if held else ""))


def format_alarm_message(label, pending, codes, active, held=()):
    """The text of one alarm message. pending: {code: aggregate of events}; active: rows
    of open_alarms(); held: codes that already had a message (rate limit)."""
    n_ev = sum(pending[c]["n_raised"] + pending[c]["n_cleared"] for c in codes)
    lines = [f"{label} SCADA: {n_ev} alarm event{'s' if n_ev > 1 else ''} "
             "(times Makkah, UTC+3)"]
    lines += [_alarm_line(c, pending[c], c in held) for c in codes]
    lines.append("Active now: " + (", ".join(f"{a['code']} (P{a['priority']})"
                                             for a in active) if active else "none"))
    return "\n".join(lines)


DEMO_BANNER = ("DEMO - NOT A REAL ALARM. Sample of the alarm format, sent by "
               "scada.notify --demo-alarm; no logger row, historian or rate limit involved.")


def demo_alarm_texts(label="PV rig", now=None):
    """Two sample messages in the real format: TEMP_FAIL raised, then cleared 4 min later.
    Built from the alarm definition in scada/model.py; touches no file and no state."""
    from scada import model
    d = model.ALARM_BY_CODE["TEMP_FAIL"]
    now = time.time() if now is None else now
    t0 = int(now) - 240
    base = {"priority": d.priority, "text": d.text, "seen": t0}
    raised = {"event": "RAISED", "t": t0, "wall": t0 + 1, "detail": "no value: T3"}
    cleared = {"event": "CLEARED", "t": t0 + 240, "wall": t0 + 241, "detail": "",
               "active_s": 240}
    out = []
    for ev, active in ((raised, [{"code": d.code, "priority": d.priority}]), (cleared, [])):
        agg = {**base, "n_raised": int(ev is raised), "n_cleared": int(ev is cleared),
               "first": ev, "last": ev}
        out.append(DEMO_BANNER + "\n\n"
                   + format_alarm_message(label, {d.code: agg}, [d.code], active))
    return out


# --- the notifier --------------------------------------------------------------------------------

def state_path_for(db_path):
    return pathlib.Path(db_path).with_suffix(".telegram.json")


class Notifier:
    """Reads new events of the alarm journal, holds them per alarm code under the rate
    limit, and sends what is due. One tick() every TICK_S from its own thread (run())."""

    def __init__(self, db_path, client, state_path=None, label="PV rig", clock=time.time,
                 log=None):
        self.db_path, self.client, self.label, self.clock = str(db_path), client, label, clock
        self.log = log or (lambda m: print(m, flush=True))
        self.state_path = pathlib.Path(state_path or state_path_for(db_path))
        with closing(historian.connect(self.db_path)) as conn:
            mode = historian.get_meta(conn, "mode")
            if mode != "live":
                raise ValueError(f"{self.db_path} is a '{mode}' historian: phone alerts are "
                                 "sent for live data of the real module only")
            self.state = self._load_state(conn)
        self.last_error = None
        self._save()
        self._snapshot()

    # state -----------------------------------------------------------------------------------
    def _load_state(self, conn):
        max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM alarm_journal").fetchone()[0]
        try:
            s = json.loads(self.state_path.read_text(encoding="utf-8"))
            if s.get("version") != STATE_VERSION:
                raise ValueError("other version")
        except (OSError, ValueError):
            # First start: what is already in the journal is history, not news; the
            # heartbeat starts with the next 08:00.
            s = {"version": STATE_VERSION, "cursor": max_id, "last_sent": {}, "pending": {},
                 "heartbeat_day": heartbeat_slot(self.clock()), "failures": 0,
                 "next_try": 0.0, "last_ok_wall": None}
        if s["cursor"] > max_id:                # the historian was replaced
            s["cursor"] = max_id
        return s

    def _save(self):
        tmp = self.state_path.with_suffix(".tmp")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(self.state, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _snapshot(self):
        s = self.state
        self._status = {"enabled": True, "pending": sorted(s["pending"]),
                        "failures": s["failures"], "last_ok_wall": s["last_ok_wall"],
                        "last_error": self.last_error, "next_try": s["next_try"] or None}

    def status(self):
        """For /api/state (another thread): a copy made at the end of each tick."""
        return dict(self._status)

    # reading ---------------------------------------------------------------------------------
    def _read_events(self, conn, now):
        rows = conn.execute(
            "SELECT j.id, j.code, j.event, j.t_data, j.t_wall, j.detail, a.priority, a.text, "
            "a.detail AS alarm_detail, a.t_raised, a.raised_wall FROM alarm_journal j "
            "JOIN alarms a ON a.id = j.alarm_id WHERE j.id > ? ORDER BY j.id",
            (self.state["cursor"],)).fetchall()
        for r in rows:
            self.state["cursor"] = r["id"]
            if r["event"] not in ("RAISED", "CLEARED") or r["code"].startswith("SIM_") \
                    or r["priority"] not in NOTIFY_PRIORITIES:
                continue
            ev = {"event": r["event"], "t": r["t_data"], "wall": r["t_wall"],
                  "detail": r["alarm_detail"] if r["event"] == "RAISED" else r["detail"]}
            if r["event"] == "CLEARED":
                if r["t_data"] is not None and r["t_raised"] is not None:
                    ev["active_s"] = r["t_data"] - r["t_raised"]
                else:
                    ev["active_s"] = r["t_wall"] - r["raised_wall"]
            agg = self.state["pending"].setdefault(r["code"], {
                "priority": r["priority"], "text": r["text"], "n_raised": 0, "n_cleared": 0,
                "first": ev, "seen": min(now, r["t_wall"])})   # group wait from the raise
            agg["n_raised" if r["event"] == "RAISED" else "n_cleared"] += 1
            agg["priority"] = min(agg["priority"], r["priority"])
            agg["last"] = ev
        return len(rows)

    def _due_codes(self, now):
        pending, sent = self.state["pending"], self.state["last_sent"]
        allowed = [c for c in pending if now - sent.get(c, -1e18) >= RATE_LIMIT_S]
        if not any(now - pending[c]["seen"] >= GROUP_WAIT_S for c in allowed):
            return []
        return sorted(allowed, key=lambda c: (pending[c]["priority"], c))

    # composing -------------------------------------------------------------------------------
    def alarm_text(self, conn, codes):
        return format_alarm_message(self.label, self.state["pending"], codes,
                                    open_alarms(conn), held=set(self.state["last_sent"]))

    # sending ---------------------------------------------------------------------------------
    def _send(self, text, now):
        try:
            self.client.send(text)
        except TelegramError as e:
            s = self.state
            s["failures"] += 1
            wait = min(RETRY_MAX_S, RETRY_MIN_S * 2 ** (s["failures"] - 1))
            if e.retry_after is not None:
                wait = max(wait if s["failures"] > 1 else 0, float(e.retry_after))
            s["next_try"] = now + wait
            if s["failures"] == 1 or str(e) != self.last_error:
                self.log(f"telegram: {e}; retrying in {fmt_duration(wait)} "
                         f"(ingest is not affected)")
            self.last_error = str(e)
            return False
        if self.state["failures"]:
            self.log(f"telegram: delivered again after {self.state['failures']} failed tries")
        self.state.update(failures=0, next_try=0.0, last_ok_wall=now)
        self.last_error = None
        return True

    def tick(self, now=None):
        """Read new events and send what is due. Never raises for a Telegram problem.
        Returns the number of messages delivered."""
        now = self.clock() if now is None else now
        sent = 0
        with closing(historian.connect(self.db_path)) as conn:
            changed = self._read_events(conn, now)
            if now >= self.state["next_try"]:
                codes = self._due_codes(now)
                if codes:
                    changed = True
                    if self._send(self.alarm_text(conn, codes), now):
                        sent += 1
                        for c in codes:
                            del self.state["pending"][c]
                            self.state["last_sent"][c] = now
                slot = heartbeat_slot(now)
                if now >= self.state["next_try"] and slot != self.state["heartbeat_day"]:
                    changed = True
                    if self._send(heartbeat_text(conn, now, self.label, slot), now):
                        sent += 1
                        self.state["heartbeat_day"] = slot
        if changed:
            self._save()
        self._snapshot()
        return sent

    def run(self, stop, tick_s=TICK_S):
        """Thread body. Any error (Telegram, a locked or busy historian) is logged and the
        loop goes on; it never touches the ingest threads."""
        token = getattr(self.client, "token", None)
        while not stop.is_set():
            try:
                self.tick()
            except Exception as e:              # noqa: BLE001 - the loop must survive anything
                msg = str(e).replace(token, "<token>") if token else str(e)
                kind = "historian busy" if isinstance(e, sqlite3.Error) else type(e).__name__
                self.log(f"telegram notifier: {kind}: {msg}; trying again")
            stop.wait(tick_s)


# --- command line ------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=CONFIG_PATH)
    ap.add_argument("--db", default="out/scada/historian.sqlite")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--chat-id", action="store_true",
                   help="list the chats that wrote to the bot (send it a message first)")
    g.add_argument("--test", action="store_true", help="send one test message")
    g.add_argument("--heartbeat", action="store_true",
                   help="send the heartbeat of the live historian now")
    g.add_argument("--demo-alarm", action="store_true",
                   help="send a sample alarm (a sensor fault raised, then cleared), marked "
                        "DEMO; touches neither the historian nor the rate limit")
    a = ap.parse_args(argv)
    try:
        if a.chat_id:
            raw = json.loads(pathlib.Path(a.config).read_text(encoding="utf-8"))
            token = str(raw.get("bot_token", "")).strip()
            if not TOKEN_RE.match(token):
                raise ConfigError(f"{a.config}: put the bot_token in first")
            seen = {}
            for u in TelegramClient(token, None).updates() or []:
                msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
                chat = msg.get("chat") or {}
                if "id" in chat:
                    seen[chat["id"]] = chat.get("title") or " ".join(
                        x for x in (chat.get("first_name"), chat.get("last_name")) if x) \
                        or chat.get("username") or ""
            if not seen:
                print("No chats yet. Send the bot a message (or /start in the group), "
                      "then run this again.")
            for cid, name in seen.items():
                print(f"chat_id {cid}   {name}")
            return 0
        cfg = load_config(a.config)
        client = TelegramClient.from_config(cfg)
        if a.demo_alarm:
            for text in demo_alarm_texts(cfg.label):
                client.send(text)
        elif a.test:
            client.send(f"{cfg.label} SCADA: test message, sent "
                        f"{fmt_time(time.time())} Makkah. Phone alerts work.")
        else:
            with closing(historian.connect(a.db)) as conn:
                client.send(heartbeat_text(conn, time.time(), cfg.label))
        print("sent")
        return 0
    except (ConfigError, TelegramError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
