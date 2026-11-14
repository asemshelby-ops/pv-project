"""Start the SCADA stack: ingest + historian + alarms + HMI, in one process.

    python -m scada.run --folder D:\\sd_copy              live: watch a copy of the SD card
    python -m scada.run --folder D:\\sd_copy --serial COM5   ... and the logger's serial port
    python -m scada.run --replay                          demo: replay out/scada/demo_source
    python -m scada.run --replay <folder> --speed 600     replay any folder of logger files
    python -m scada.run --sim                             only the rooftop SIMULATION
    python -m scada.run --replay --sim nasim              ... or next to a replay / live data
    python -m scada.run --replay --speed 600 --sim        everything (start-scada.bat)

Then open http://127.0.0.1:8050 ; the bar at the top switches between the real module's
dashboard and the rooftop page. Stop with Ctrl+C.

Phone alerts (Telegram, scada/notify.py) start by themselves for LIVE data (--folder /
--serial) when scada/telegram.local.json exists; --no-telegram turns them off, --telegram
<file> uses another config file. Never for a replay or the simulation.
Setup: docs/scada/telegram.md.

--sim runs the rooftop twin (twin/, 120 simulated modules) and shows it on the page
/rooftop, marked SIMULATION. It has its own file (out/scada/simulation.sqlite) and never
touches the historian: the real module stays the only real data source.

Live and replay never share a historian: live data goes to out/scada/historian.sqlite, a
replay to out/scada/replay.sqlite, which is emptied at every replay start. A historian
that holds live data is never emptied and never receives replayed rows.
"""
import argparse
import pathlib
import threading

from scada import historian, ingest, notify, replay, server, sim_source

LIVE_DB = "out/scada/historian.sqlite"
REPLAY_DB = "out/scada/replay.sqlite"
REPLAY_FOLDER = "out/scada/replay_live"
DEMO_SOURCE = "out/scada/demo_source"


def open_historian(path, mode):
    """Create the historian for `mode` ("live" / "replay") or check that it is of that
    mode. A replay historian is emptied; a live one is never touched by a replay."""
    path = pathlib.Path(path)
    if path.exists():
        conn = historian.connect(path)
        found = historian.get_meta(conn, "mode")
        conn.close()
        if found != mode:
            raise SystemExit(f"{path} is a '{found}' historian, not '{mode}': not touched. "
                             f"Give another file with --db.")
        if mode == "replay":
            try:
                for p in (path, path.with_name(path.name + "-wal"),
                          path.with_name(path.name + "-shm")):
                    p.unlink(missing_ok=True)
            except PermissionError:
                raise SystemExit(f"{path} is in use: another scada.run is still running. "
                                 f"Stop it first.") from None
    conn = historian.connect(path)
    historian.set_meta(conn, "mode", mode)
    conn.commit()
    conn.close()


def start_notifier(a, db, stop, threads):
    """The Telegram notifier for live data, or None. A bad config stops the start: an
    operator who set up alerts must not believe they are on when they are not."""
    if a.no_telegram or not (a.folder or a.serial):
        return None
    path = a.telegram or notify.CONFIG_PATH
    if not a.telegram and not pathlib.Path(path).exists():
        print(f"Telegram alerts: off (no {path}; see docs/scada/telegram.md)", flush=True)
        return None
    try:
        cfg = notify.load_config(path)
    except notify.ConfigError as e:
        raise SystemExit(f"Telegram alerts: {e}. Fix it or start with --no-telegram.") from None
    n = notify.Notifier(db, notify.TelegramClient.from_config(cfg), label=cfg.label)
    threads.append(threading.Thread(target=n.run, args=(stop,), name="telegram"))
    print(f"Telegram alerts: on (chat {cfg.chat_id}); state file {n.state_path}", flush=True)
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--folder", help="folder with the logger's files (a copy of the SD card)")
    ap.add_argument("--serial", help="the logger's serial port, e.g. COM5 (read-only)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--replay", nargs="?", const=DEMO_SOURCE, metavar="SOURCE",
                    help=f"replay a folder of logger files (default {DEMO_SOURCE})")
    ap.add_argument("--speed", type=float, default=120.0, help="replay speed, times real time")
    ap.add_argument("--db", help=f"historian file (default {LIVE_DB}; replay: {REPLAY_DB})")
    ap.add_argument("--port", type=int, default=8050)
    ap.add_argument("--sim", nargs="?", const="predictive", metavar="CONTROLLER",
                    choices=sim_source.CONTROLLERS,
                    help="run the rooftop SIMULATION (page /rooftop) with this controller: "
                         + ", ".join(sim_source.CONTROLLERS) + " (default predictive)")
    ap.add_argument("--sim-speed", type=float, default=120.0,
                    help="simulation speed, times real time")
    ap.add_argument("--sim-days", type=int, default=2, help="simulated days")
    ap.add_argument("--telegram", metavar="CONFIG",
                    help=f"phone alerts with this config file (default {notify.CONFIG_PATH} "
                         "if it exists; live data only)")
    ap.add_argument("--no-telegram", action="store_true", help="no phone alerts")
    a = ap.parse_args()
    if a.telegram and (a.no_telegram or a.replay or not (a.folder or a.serial)):
        ap.error("--telegram needs live data (--folder / --serial) and no --no-telegram: "
                 "phone alerts are for the real module only, never a replay or the simulation")
    if a.replay and (a.folder or a.serial):
        ap.error("--replay cannot be combined with --folder or --serial")
    if not (a.replay or a.folder or a.serial or a.sim):
        ap.error("give --folder and/or --serial for live data, --replay for a demo, "
                 "or --sim for the rooftop simulation")

    stop, threads = threading.Event(), []
    if a.replay:
        db, folder = a.db or REPLAY_DB, REPLAY_FOLDER
        note = replay.source_note(a.replay)
        replay.load(a.replay)                       # fails now if the source has no day files
        open_historian(db, "replay")
        replay.prepare_dest(folder, "replay of " + note)
        info = {"mode": "replay", "speed": a.speed, "replay_note": note}
        threads.append(threading.Thread(target=replay.feed, args=(a.replay, folder, a.speed, stop)))
    else:
        db, folder = a.db or LIVE_DB, a.folder
        open_historian(db, "live")
        info = {"mode": "live", "folder": a.folder, "serial": a.serial}
    if folder:
        threads.append(threading.Thread(target=ingest.run_folder, args=(db, folder, stop)))
    if a.serial:
        threads.append(threading.Thread(target=ingest.run_serial, args=(db, a.serial, a.baud, stop)))
    sim_db, sim_info = None, None
    if a.sim:
        feeder = sim_source.Feeder(sim_source.SIM_DB, a.sim, a.sim_days)   # fails now if e.g.
        sim_db = sim_source.SIM_DB                                         # NASIM's model is missing
        sim_info = {"speed": a.sim_speed}
        threads.append(threading.Thread(target=feeder.run, args=(stop, a.sim_speed)))
    notifier = start_notifier(a, db, stop, threads) if not a.replay else None
    for t in threads:
        t.daemon = True
        t.start()
    httpd = server.make_server(db, info, a.port, sim_db, sim_info,
                               notifier.status if notifier else None)
    print(f"SCADA ({info['mode']}) on http://127.0.0.1:{httpd.server_address[1]}   "
          f"historian: {db}   Ctrl+C stops", flush=True)
    if a.sim:
        print(f"Rooftop SIMULATION ({feeder.controller.name}) on "
              f"http://127.0.0.1:{httpd.server_address[1]}/rooftop   file: {sim_db}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
        for t in threads:
            t.join(timeout=5)


if __name__ == "__main__":
    main()
