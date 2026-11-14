# Phone alerts through a Telegram bot

Code: `scada/notify.py`. Tests: `tests/test_scada_notify.py` (mocked Telegram API).

The SCADA sends a Telegram message to a phone (or a team group) when an alarm of the
**real module** is raised or cleared, and a heartbeat every morning. It is one-way: the bot
sends, it does not take commands, and nothing can be acknowledged from the phone.

## 1. What is sent

| Message | When | Content |
|---|---|---|
| Alarm | an alarm of priority 1 or 2 is RAISED or CLEARED | code, priority, text, row time (Makkah), the alarm's detail, how long it was active, the alarms active now |
| Heartbeat | every day at 08:00 Makkah time (UTC+3) | time of the last row, rows of the last 24 h, the maximum module temperature today so far and yesterday, the open alarms |

Alarm codes of priority 1 or 2 today (scada/model.py): `NO_DATA`, `RTC_INVALID`,
`TEMP_FAIL`, `AMBIENT_FAIL`, `IRRAD_FAIL`, `PANEL_FAIL`, `SD_ERROR`, `LOAD_TRIP`,
`DET_RESET`, and the three detector alarms `DET_NASIM`, `DET_THRESHOLD`, `DET_CUSUM`.
`TROM_UNMAPPED` (priority 3) is not sent. If a priority is changed in `scada/model.py`,
the alerts follow it.

Rules:

- **Real module only.** Alerts run only for live data (`--folder` / `--serial`). Never for
  a replay, never for the rooftop simulation: codes starting with `SIM_` are filtered out,
  and the notifier refuses to start on a historian that is not a live one.
- **Rate limit: at most one message per alarm code every 30 minutes.** Events of a code
  inside its 30 minutes are held, then sent as one line, for example
  `DET_NASIM ... raised 19x, cleared 19x between 09:00:20 and 09:06:30; still ACTIVE`.
  Nothing is dropped: every raise and clear is counted in some message.
- **Grouping.** Everything that is due at the same moment goes into one message. A new
  alarm waits 20 s before it is sent, so alarms of the same fault (for example
  `NO_DATA` and a sensor fault) arrive together.
- **Heartbeat.** If the SCADA (or the PC) stops, no heartbeat comes at 08:00: **the
  missing heartbeat is the warning.** If the SCADA was not running at 08:00, the
  heartbeat of that day is sent as soon as it starts again and is marked `LATE`.
- **First start.** Alarms already in the historian when alerts are switched on for the
  first time are history and are not sent; they appear in the heartbeat if still open.
- **Times** in the messages are Makkah time (UTC+3). An alarm's time is the time of the
  logger row; when the row reached the SCADA much later (an SD card copied afterwards), the
  message says so.

The numbers 30 min, 20 s and 08:00 are constants at the top of `scada/notify.py`.

## 2. When Telegram cannot be reached

The ingest never waits for Telegram. The notifier is its own thread, only reads the
historian, and keeps its state in a separate file (`out/scada/historian.telegram.json`).
If a send fails (no internet, Telegram down, wrong token, HTTP 429 "too many requests"):

- the events stay pending, also across a restart of the SCADA;
- it tries again after 30 s, 60 s, 2 min ... up to every 15 min, or after the time
  Telegram asks for (`retry_after`);
- the console shows the error once (`telegram: ... ingest is not affected`) and again
  when it changes, then `delivered again` when it works;
- `/api/state` shows `"telegram": {"pending": [...], "last_error": ..., ...}`.

The rate limit counts from the last message that was **delivered**, not from a failed try.

## 3. Setup (once, about 10 minutes)

### 3.1 Create the bot with BotFather

1. In Telegram, search for **@BotFather** (it has a blue verified tick) and open the chat.
2. Send `/newbot`.
3. Give it a display name, for example `PV Rig UQU`.
4. Give it a username that ends in `bot`, for example `pv_rig_uqu_bot` (must be unique).
5. BotFather answers with the **token**, a line like `123456789:AAH...` (digits, a colon,
   about 35 characters). **The token is a password**: whoever has it can send messages
   as the bot. Do not paste it in a chat, a screenshot, a commit or the shared drive.

### 3.2 Choose who receives the alerts and get the chat ID

A bot cannot write to anyone first. Someone has to write to it once.

**Only you (private chat):** open the bot (search its username), press **Start** or send
any message.

**The team (a group, recommended):** create a group, add the bot as a member, then send
`/start@<bot username>` in the group (by default a bot in a group only sees commands
addressed to it).

Then, with the token in the config file (next step; `chat_id` can still be `0`):

```
.venv\Scripts\python -m scada.notify --chat-id
```

It lists the chats that wrote to the bot, for example

```
chat_id 512345678   Asim
chat_id -1001234567890   PV rig alerts
```

A private chat has a positive number, a group a negative one. Telegram keeps these
updates for about a day only: if the list is empty, send the message again and rerun.

(Without the script: open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser
and read `"chat":{"id": ...}`. Close the tab afterwards: the token is in the address.)

### 3.3 The config file

```
copy scada\telegram.example.json scada\telegram.local.json
```

Edit `scada/telegram.local.json`:

```json
{
  "bot_token": "123456789:AAH...",
  "chat_id": -1001234567890,
  "label": "PV rig"
}
```

`label` is the first word of every message (optional). `scada/telegram.local.json` is in
`.gitignore`: `git status` must never show it. Check with
`git check-ignore -v scada/telegram.local.json` (it prints the matching line).

### 3.4 Test and start

```
.venv\Scripts\python -m scada.notify --test
```

The phone should show `PV rig SCADA: test message ...`. Then start the SCADA as usual:

```
.venv\Scripts\python -m scada.run --folder D:\sd_copy
```

It prints `Telegram alerts: on (chat ...)`. Without the file it prints
`Telegram alerts: off`; with a broken file it refuses to start (so nobody believes
alerts are on when they are not); `--no-telegram` switches them off,
`--telegram <file>` uses another config file. To see a heartbeat without waiting for
08:00: `python -m scada.notify --heartbeat`. To show the team what an alarm looks like:
`python -m scada.notify --demo-alarm` sends two messages (a back-sheet sensor fault
raised, then cleared 4 min later) in exactly the format of real alarms, each headed
`DEMO - NOT A REAL ALARM`. It reads no historian, writes no file and does not count
toward the 30-min limit, so it can be run while the SCADA is running.

## 4. Problems

| Console message | Cause |
|---|---|
| `HTTP 401: Unauthorized` | wrong token, or it was revoked; copy it again from BotFather |
| `HTTP 400: Bad Request: chat not found` | wrong `chat_id`, or nobody wrote to the bot yet (3.2) |
| `HTTP 403: Forbidden: bot was blocked by the user` / `kicked from the group` | unblock the bot / add it to the group again |
| `HTTP 400: ... group chat was upgraded to a supergroup chat` | the group's ID changed; run `--chat-id` again |
| `Telegram not reachable: ...` | no internet on the PC, or the network blocks `api.telegram.org` (check from the lab PC) |
| `HTTP 429: Too Many Requests` | handled: it waits as long as Telegram asks |

**If the token leaked:** in @BotFather send `/revoke`, choose the bot, put the new token in
`scada/telegram.local.json`, restart the SCADA.

## 5. Not checked yet

- The tests use a local imitation of the Telegram Bot API; no message was sent to the real
  Telegram from this code. First real check: `--test` from the lab PC.
- Whether the campus network lets the lab PC reach `api.telegram.org`.
- What leaves the lab: alarm codes, times, temperatures and row counts go through
  Telegram's servers. No personal data, but tell the supervisor before switching it on.
