"""Replay: feed recorded logger files into a folder faster than real time, for demos.

    python -m scada.replay --source out/scada/demo_source --dest out/scada/replay_live --speed 120

The replay plays the part of the SD card being written: it appends the rows of the source
day files (and the lines of markers.csv) to files of the same names in --dest, each line
when its own timestamp comes up on a clock that runs `speed` times faster than real time.
The ingest service watches --dest exactly as it would watch a copy of the card, so the
whole chain is exercised: file tailing, format detection, historian, alarms, HMI.
At speed 120 a 12-hour day takes 6 minutes; 10 s rows arrive 12 per second.

It works on any folder of logger files (synthetic now, a copy of the real card later).
`python -m scada.run --replay <source>` starts it together with the rest of the stack.
"""
import argparse
import pathlib
import threading
import time

from scada.ingest import DAY_FILE, _marker_time_ms, _utc_seconds

SENTINEL = "SCADA_FOLDER.txt"    # marks a folder this package made and may empty


def load(source):
    """All lines to replay as (data time in s, file name, line), in time order, plus the
    header line of each file. A row without date and time takes the time of the row
    before it + 10 s."""
    source = pathlib.Path(source)
    events, headers = [], {}
    names = sorted(p.name for p in source.iterdir() if DAY_FILE.match(p.name))
    if not names:
        raise FileNotFoundError(f"no logger day files (YYYYMMDD.csv) in {source}")
    for name in names:
        lines = (source / name).read_text(encoding="utf-8").splitlines()
        headers[name], t = lines[0], None
        for line in lines[1:]:
            f = line.split(",")
            try:
                t = _utc_seconds(f[0], f[1])
            except (ValueError, IndexError):
                t = None if t is None else t + 10
            if t is not None:
                events.append((float(t), name, line))
    if (source / "markers.csv").exists():
        lines = (source / "markers.csv").read_text(encoding="utf-8").splitlines()
        headers["markers.csv"] = lines[0]
        for line in lines[1:]:
            f = line.split(",")
            try:
                t_ms = _marker_time_ms(f[0], f[1])
            except (ValueError, IndexError):
                continue
            if t_ms is not None and events[0][0] <= t_ms / 1000 <= events[-1][0]:
                events.append((t_ms / 1000, "markers.csv", line))
    events.sort(key=lambda e: e[0])               # stable: file order kept within a second
    return events, headers


def prepare_dest(dest, note="replayed files, not measurements"):
    """Create `dest`, or empty it if this package made it (it then holds SENTINEL). A
    folder with other content is refused, so a real copy of the card is never deleted.
    `note` (one line: what the files are) is kept in SENTINEL; the HMI shows it."""
    dest = pathlib.Path(dest)
    if dest.exists() and any(dest.iterdir()):
        if not (dest / SENTINEL).exists():
            raise SystemExit(f"{dest} is not empty and was not made by scada: not touched")
        for p in dest.iterdir():
            p.unlink()
    dest.mkdir(parents=True, exist_ok=True)
    (dest / SENTINEL).write_text(note + "\n", encoding="utf-8")
    return dest


def source_note(source):
    """What a source folder holds: its SENTINEL line if scada made it, else its name."""
    f = pathlib.Path(source) / SENTINEL
    return f.read_text(encoding="utf-8").strip() if f.exists() else f"files of {source}"


def feed(source, dest, speed, stop=None, tick_s=0.2, clock=time.monotonic):
    """Append the source lines to `dest` at `speed` x real time. Returns the lines written.
    Stops early when the `stop` event is set."""
    events, headers = load(source)
    dest, stop = pathlib.Path(dest), stop or threading.Event()
    t_data0, t_wall0, k = events[0][0], clock(), 0
    while k < len(events) and not stop.is_set():
        now = t_data0 + (clock() - t_wall0) * speed
        batch = {}
        while k < len(events) and events[k][0] <= now:
            batch.setdefault(events[k][1], []).append(events[k][2])
            k += 1
        for name, lines in batch.items():
            new = not (dest / name).exists()
            with open(dest / name, "a", encoding="utf-8", newline="\n") as f:
                f.write("".join(x + "\n" for x in ([headers[name]] if new else []) + lines))
        if k < len(events):
            stop.wait(tick_s)
    return k


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default="out/scada/demo_source")
    ap.add_argument("--dest", default="out/scada/replay_live")
    ap.add_argument("--speed", type=float, default=120.0, help="times real time (default 120)")
    a = ap.parse_args()
    n = feed(a.source, prepare_dest(a.dest, "replay of " + source_note(a.source)), a.speed)
    print(f"replay finished: {n} lines written to {a.dest}")


if __name__ == "__main__":
    main()
