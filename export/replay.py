"""Step 6b: NASIM-GBT (no wind) on the ESP32 by replay (supervisor task 2, part 1).

    python -m export.replay --port COM3 --precision double
    python -m export.replay --port COM3 --precision float

No sensors. For each precision: build firmware/nasim_replay against export/c
(the same C files as the PC parity test), flash it, stream the held-out test
days (hard variant, seed 5, as in out/export/parity.md) over serial, and
compare the board's answers with the Python detector:
  * alarm mismatches (must be 0 in double), max |dp|, event F1;
  * time per gbt_update() measured on the board with micros();
  * flash (whole firmware and the two NASIM objects) and RAM (static detector
    state, global variables, minimum free stack and heap during the run).
Writes out/replay/replay_<precision>.json and out/replay/replay.md.
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import time

import lightgbm as lgb
import numpy as np
import serial

import config
from data import synth
from eval.run import evaluate
from eval.split import split_days
from export.run import python_reference, write_input

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKETCH = ROOT / "firmware/nasim_replay"
OUT = ROOT / "out/replay"
FQBN = "esp32:esp32:esp32wrover"
BAUD = 921600
WINDOW = 64            # lines in flight; 64 x ~71 B fits the board's 8 KB receive buffer
TAG, ROW = "nowind", "NASIM-GBT (no wind)"


# --- build and flash ------------------------------------------------------------------

def size_tool():
    root = pathlib.Path(os.environ["LOCALAPPDATA"]) / "Arduino15/packages/esp32/tools/esp-x32"
    return str(sorted(root.glob("*/bin/xtensa-esp32-elf-size.exe"))[-1])


def build(precision):
    """Compile the sketch; return the build path and the flash/RAM numbers."""
    build_dir = OUT / f"build_{precision}"
    inc = f"-I{ROOT / 'export/c'} -I{ROOT / 'export/c/generated'}".replace("\\", "/")
    c_flags, cpp_flags = inc, inc
    if precision == "float":
        c_flags += " -DNASIM_FLOAT -fsingle-precision-constant"
        cpp_flags += " -DNASIM_FLOAT"
    r = subprocess.run(["arduino-cli", "compile", "-b", FQBN, "--build-path", str(build_dir),
                        "--build-property", f"compiler.c.extra_flags={c_flags}",
                        "--build-property", f"compiler.cpp.extra_flags={cpp_flags}",
                        str(SKETCH)], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(r.stdout + r.stderr)
    sizes = {
        "firmware_flash_bytes": int(re.search(r"Sketch uses (\d+) bytes", r.stdout)[1]),
        "global_ram_bytes": int(re.search(r"Global variables use (\d+) bytes", r.stdout)[1]),
    }
    for name in ("nasim_core", "nasim_model"):
        out = subprocess.run([size_tool(), str(build_dir / f"sketch/{name}.c.o")],
                             capture_output=True, text=True, check=True).stdout
        text, data, bss = (int(x) for x in out.splitlines()[1].split()[:3])
        sizes[f"{name}_object_bytes"] = text + data + bss
    return build_dir, sizes


def flash(build_dir, port):
    subprocess.run(["arduino-cli", "upload", "-b", FQBN, "-p", port,
                    "--input-dir", str(build_dir), str(SKETCH)],
                   check=True, capture_output=True)


# --- serial replay --------------------------------------------------------------------

def replay(port, rows):
    """Send every row, return (p, alarm, us) arrays, the READY banner and STATS."""
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = port, BAUD, 5
    ser.dtr = ser.rts = False          # open without touching EN / IO0
    ser.open()
    ser.rts = True                     # RTS pulls EN low: reset the board
    time.sleep(0.1)
    ser.rts = False
    deadline = time.time() + 10
    banner = b""
    while b"READY" not in banner:
        if time.time() > deadline:
            raise SystemExit("no READY line from the board")
        banner = ser.readline()
    # The ROM boot log (115200 baud) reads as junk at 921600 with no newline,
    # so READY arrives glued to the end of that junk: keep only READY onwards.
    banner = banner[banner.index(b"READY"):].strip()

    n = len(rows)
    p, alarm, us = np.empty(n), np.zeros(n, bool), np.empty(n)
    sent = got = 0
    t0 = time.time()
    while got < n:
        upto = min(n, got + WINDOW)
        if sent < upto:
            ser.write(b"".join(rows[sent:upto]))
            sent = upto
        line = ser.readline()
        if not line.endswith(b"\n"):
            raise SystemExit(f"timeout after {got} samples")
        a, b, c = line.decode().strip().split(",")
        p[got], alarm[got], us[got] = float(a), b == "1", float(c)
        got += 1
        if got % 20000 == 0:
            print(f"  {got}/{n} samples, {time.time() - t0:.0f} s", flush=True)
    ser.write(b"STATS\n")
    stats = ser.readline().decode().strip()
    ser.close()
    return p, alarm, us, banner.decode(), stats, time.time() - t0


def kv(line):
    return {k: int(v) if v.isdigit() else v
            for k, v in (x.split("=") for x in line.split()[1:])}


# --- reference and comparison ------------------------------------------------------------

def test_part():
    days, events = synth.generate(config.EXPORT_SEED, n_days=config.HARD_N_DAYS, hard=True)
    return split_days(days, events, config.HARD_SPLIT_DAYS)["test"]


def reference(test, threshold, inp):
    """Python detector on the test days, cached by input file hash and threshold."""
    key = hashlib.sha256(inp.read_bytes()).hexdigest()[:16] + f"_{threshold!r}"
    cache = OUT / "python_reference.npz"
    if cache.exists():
        z = np.load(cache)
        if str(z["key"]) == key:
            return z["p"], z["alarm"]
    booster = lgb.Booster(model_file=str(
        ROOT / f"out/results/hard/seed{config.EXPORT_SEED}/gbt_hybrid_{TAG}.txt"))
    print("Python reference on the test days ...", flush=True)
    p, alarm, _ = python_reference(booster, False, threshold, test[0])
    np.savez(cache, key=key, p=p, alarm=alarm)
    return p, alarm


def compare(p_py, a_py, p_b, a_b, us, test):
    valid_py, valid_b = ~np.isnan(p_py), ~np.isnan(p_b)
    both = valid_py & valid_b
    ip, ib = np.flatnonzero(a_py), np.flatnonzero(a_b)
    shifts = [int(np.min(np.abs(ib - i))) if len(ib) else None for i in sorted(set(ip) - set(ib))]
    t_valid, t_warm = us[valid_b], us[~valid_b]
    return {
        "samples": int(len(p_py)),
        "valid_samples": int(both.sum()),
        "warm_up_pattern_equal": bool(np.array_equal(valid_py, valid_b)),
        "max_abs_dp": float(np.max(np.abs(p_py[both] - p_b[both]))),
        "samples_with_dp_nonzero": int(np.count_nonzero(p_py[both] != p_b[both])),
        "alarms_python": int(a_py.sum()),
        "alarms_esp32": int(a_b.sum()),
        "alarm_mismatches": int((a_py != a_b).sum()),
        "python_alarms_missing_on_esp32_nearest_alarm_samples": shifts,
        "event_python": {k: evaluate(a_py, test)[k] for k in ("precision", "recall", "F1")},
        "event_esp32": {k: evaluate(a_b, test)[k] for k in ("precision", "recall", "F1")},
        "update_us_valid": {"mean": float(t_valid.mean()), "median": float(np.median(t_valid)),
                            "p99": float(np.percentile(t_valid, 99)), "max": float(t_valid.max())},
        "update_us_warm_up": {"mean": float(t_warm.mean()), "max": float(t_warm.max())},
    }


def vs_pc(p_b, a_b, precision):
    """Board vs the PC build of the same C code (out/export, if present)."""
    f = ROOT / f"out/export/c_{TAG}_{precision}.csv"
    if not f.exists():
        return None
    rows = [line.split(",", 2) for line in f.read_text().splitlines()]
    p_pc = np.array([float(r[0]) for r in rows])
    a_pc = np.array([r[1] == "1" for r in rows])
    both = ~np.isnan(p_pc) & ~np.isnan(p_b)
    return {"max_abs_dp": float(np.max(np.abs(p_pc[both] - p_b[both]))),
            "alarm_mismatches": int((a_pc != a_b).sum())}


# --- report -------------------------------------------------------------------------------

def write_markdown():
    runs = {pr: json.loads((OUT / f"replay_{pr}.json").read_text())
            for pr in ("double", "float") if (OUT / f"replay_{pr}.json").exists()}
    L = [f"# NASIM-GBT (no wind) on the ESP32 - replay report (hard synthetic data, seed "
         f"{config.EXPORT_SEED})", "",
         "**Model trained on SYNTHETIC data: this proves the on-board pipeline, not the "
         "detector. Retrain on real data before deployment.**", "",
         "Board: Freenove ESP32-WROVER (ESP32-D0WD-V3 rev 3.1, 240 MHz, 4 MB flash, 4 MB PSRAM), "
         "Arduino core 3.3.8, FQBN esp32:esp32:esp32wrover. The PC streams the held-out "
         "test days (out/export/test_input.csv format) at 921600 baud; the board runs "
         "gbt_update() on every sample and returns p, alarm and the update time (micros).", "",
         "## Agreement with Python", "",
         "| precision | samples (valid) | warm-up equal | max abs dp | alarms Py / ESP32 | "
         "alarm mismatches | event F1 Py / ESP32 |", "|---|---|---|---|---|---|---|"]
    for pr, r in runs.items():
        q = r["compare"]
        L.append(f"| {pr} | {q['samples']} ({q['valid_samples']}) | {q['warm_up_pattern_equal']} | "
                 f"{q['max_abs_dp']:.3g} | {q['alarms_python']} / {q['alarms_esp32']} | "
                 f"{q['alarm_mismatches']} | {q['event_python']['F1']:.3f} / "
                 f"{q['event_esp32']['F1']:.3f} |")
    for pr, r in runs.items():
        q = r["compare"]
        if q["python_alarms_missing_on_esp32_nearest_alarm_samples"]:
            L.append(f"\n{pr}: Python alarms missing on the ESP32, distance to the nearest "
                     f"ESP32 alarm in samples (10 s each): "
                     f"{q['python_alarms_missing_on_esp32_nearest_alarm_samples']}.")
    L += ["", "Board vs the PC build of the same C code (out/export/c_nowind_*.csv):", "",
          "| precision | max abs dp | alarm mismatches |", "|---|---|---|"]
    for pr, r in runs.items():
        if r.get("vs_pc"):
            L.append(f"| {pr} | {r['vs_pc']['max_abs_dp']:.3g} | {r['vs_pc']['alarm_mismatches']} |")
    L += ["", "## Time per update on the board (microseconds)", "",
          "Valid samples = features ready (model runs); warm-up = buffers still filling. "
          "Measured with micros() around gbt_update() while the serial port is receiving "
          "(interrupts included). Budget: one sample every 10 s = 10,000,000 us.", "",
          "| precision | mean | median | p99 | max | warm-up mean |", "|---|---|---|---|---|---|"]
    for pr, r in runs.items():
        t, w = r["compare"]["update_us_valid"], r["compare"]["update_us_warm_up"]
        L.append(f"| {pr} | {t['mean']:.1f} | {t['median']:.0f} | {t['p99']:.0f} | "
                 f"{t['max']:.0f} | {w['mean']:.1f} |")
    L += ["", "## Memory", "",
          "| precision | firmware flash | nasim.c object | model object | global RAM | "
          "detector state | min free stack | min free heap |", "|---|---|---|---|---|---|---|---|"]
    for pr, r in runs.items():
        s, b, st = r["sizes"], r["banner"], r["stats"]
        L.append(f"| {pr} | {s['firmware_flash_bytes']} | {s['nasim_core_object_bytes']} | "
                 f"{s['nasim_model_object_bytes']} | {s['global_ram_bytes']} | "
                 f"{b['sizeof_detector']} | {st['stack_free_min']} | {st['heap_free_min']} |")
    L += ["", "Bytes. Firmware flash and global RAM are the whole program (Arduino core, "
          "serial, printf included); app partition 1,310,720 B, RAM for globals 327,680 B. "
          "Object sizes are text + data + bss of the two NASIM files only. Detector state = "
          "sizeof(gbt_detector_t), the whole detector memory. Stack and heap: minimum free "
          "during the run (loop task stack 8192 B)."]
    (OUT / "replay.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--precision", choices=["double", "float"], nargs="+", default=["double"])
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    tuned = json.loads((ROOT / f"out/results/hard/seed{config.EXPORT_SEED}/tuned.json").read_text())
    threshold = tuned[f"{ROW} p_threshold"]["value"]
    print("Generating the test days ...", flush=True)
    test = test_part()
    inp = OUT / "test_input.csv"
    write_input(test[0], inp)
    rows = inp.read_bytes().splitlines(keepends=True)[1:]
    p_py, a_py = reference(test, threshold, inp)

    for pr in args.precision:
        print(f"[{pr}] build and flash ...", flush=True)
        build_dir, sizes = build(pr)
        flash(build_dir, args.port)
        print(f"[{pr}] replay of {len(rows)} samples ...", flush=True)
        p_b, a_b, us, banner, stats, wall = replay(args.port, rows)
        report = {"precision": pr, "p_threshold": threshold, "persist": config.GBT_PERSIST,
                  "banner": kv(banner), "stats": kv(stats), "sizes": sizes,
                  "replay_wall_s": wall, "compare": compare(p_py, a_py, p_b, a_b, us, test),
                  "vs_pc": vs_pc(p_b, a_b, pr)}
        (OUT / f"replay_{pr}.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report["compare"], indent=1), flush=True)
    write_markdown()
    print((OUT / "replay.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
