"""Build a small demo data set for the replay from the SYNTHETIC run of the paper pipeline.

    python -m scada.demo_data                      (2 test days of out/paper_demo/seed5)
    python -m scada.demo_data --dates 2027-03-20 2027-03-21 --no-faults

The synthetic logger files (analysis/fake_logger.py) hold `alarm` = 0 and an empty `p`:
they are the files BEFORE any detector ran. A SCADA demo needs the two columns the board
would have written, and some faults to show. This script, without changing anything in
analysis/ or firmware/:

  1. copies the chosen day files and their markers;
  2. unless --no-faults, injects a fixed fault scenario into the first day (FAULTS below):
     fields blanked and status bits set as firmware/logger/logger.ino sets them,
     including DET_SKIPPED and DET_RESET;
  3. fills `p` and `alarm` with the run's three detectors, computed by the pipeline's own
     code (analysis/detection.py, the rules the C code was checked against) on the files of
     step 2, so a skipped row or a reset changes the alarms as it would on the board.

Everything it writes is synthetic and says so (SCADA_FOLDER.txt in the output folder).
"""
import argparse
import json
import pathlib
import warnings

import lightgbm as lgb
import numpy as np
import pandas as pd

import config
from analysis import detection
from analysis import ingest as offline
from data.schema import ALARM_BITS, STATUS_BITS
from detect import gbt
from scada import replay

# [SCENARIO] injected for the demo, first day only: (local start, rows, fields blanked, bit)
FAULTS = [
    ("09:30:00", 9, ["T3"], "TEMP_FAIL"),
    ("10:40:00", 2, ["irradiance"], "IRRAD_FAIL"),
    ("11:50:00", 6, ["date", "time"], "RTC_INVALID"),
    ("13:10:00", 1, [], "SD_RETRY"),
    ("14:20:00", 12, ["voltage", "current", "power"], "PANEL_FAIL"),
]
STOPS_DETECTOR = {"TEMP_FAIL", "IRRAD_FAIL", "AMBIENT_FAIL", "RTC_INVALID"}   # logger.ino


def inject_faults(lines, utc_date):
    """lines: one day file as text lines (header first). Returns the lines with FAULTS."""
    header = lines[0].split(",")
    col = {name: k for k, name in enumerate(header)}
    rows = [line.split(",") for line in lines[1:]]
    for start, n, blank, bit in FAULTS:
        utc = pd.Timestamp(f"{utc_date} {start}", tz=config.TZ).tz_convert("UTC")
        first = next(k for k, r in enumerate(rows) if r[col["time"]] == f"{utc:%H:%M:%S}")
        for k in range(n):
            r = rows[first + k]
            status = int(r[col["status"]]) | 1 << STATUS_BITS[bit]
            if bit in STOPS_DETECTOR:
                status |= 1 << STATUS_BITS["DET_SKIPPED"]
                if k == config.DET_RESET_AFTER_FAILS - 1:
                    status |= 1 << STATUS_BITS["DET_RESET"]
            for name in blank:
                r[col[name]] = ""
            r[col["status"]] = str(status)
    return [lines[0]] + [",".join(r) for r in rows]


def detector_columns(folder, results):
    """{(UTC date, UTC time): (p, alarm)} for every row the detectors got, from the run's
    saved model and tuned settings."""
    tuned = json.loads((results / "tuned.json").read_text())
    booster = lgb.Booster(model_file=str(results / "nasim_gbt_nowind.txt"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        frame = offline.detector_frame(offline.read_logger(folder))
    X = gbt.build_features(frame, False, hybrid=True)
    ok = ~np.isnan(X).any(axis=1)
    p = np.full(len(frame), np.nan)
    p[ok] = booster.predict(X[ok])
    seg = frame["segment"].to_numpy()
    fired = {
        "NASIM": detection.alarm_rule(p, tuned["nasim_p_threshold"], seg, config.GBT_PERSIST),
        "THRESHOLD": detection.alarm_rule(X[:, detection.NAMES.index("rise_10m")],
                                          tuned["threshold_rise_C"], seg),
        "CUSUM": detection.cusum_inc_alarms(detection.filtered_temperature(frame), seg,
                                            tuned["cusum_kappa_C_per_sample"],
                                            tuned["cusum_h_C"]),
    }
    alarm = sum(fired[name].astype(int) << bit for name, bit in ALARM_BITS.items())
    return {(d, t): (pk, int(ak)) for d, t, pk, ak in
            zip(frame["date_utc"], frame["time_utc"], p, alarm)}, \
        {name: int(a.sum()) for name, a in fired.items()}


def build(run, out, dates=None, n_days=2, faults=True):
    run, out = pathlib.Path(run), pathlib.Path(out)
    raw, results = run / "raw", run / "results"
    if not (results / "tuned.json").exists():
        raise SystemExit(f"{results} has no pipeline results. Create them first:\n"
                         f"    python -m analysis.demo_synth")
    if not dates:
        first = json.loads((results / "tuned.json").read_text())["split_days"]["test"][0]
        dates = [f"{d:%Y-%m-%d}" for d in pd.date_range(first, periods=n_days)]
    note = (f"SYNTHETIC days {', '.join(dates)} of {run.as_posix()} (hard variant), detector "
            f"columns filled by the pipeline's code" +
            (", with an INJECTED fault scenario on the first day" if faults else ""))
    replay.prepare_dest(out, note)
    for k, date in enumerate(dates):               # the synthetic day lies inside one UTC date
        name = date.replace("-", "") + ".csv"
        lines = (raw / name).read_text().splitlines()
        if faults and k == 0:
            lines = inject_faults(lines, date)
        (out / name).write_text("\n".join(lines) + "\n")
    values, counts = detector_columns(out, results)
    for date in dates:
        f = out / (date.replace("-", "") + ".csv")
        lines = f.read_text().splitlines()
        col = {name: k for k, name in enumerate(lines[0].split(","))}
        for n in range(1, len(lines)):
            r = lines[n].split(",")
            p, alarm = values.get((r[col["date"]], r[col["time"]]), (np.nan, 0))
            r[col["p"]], r[col["alarm"]] = ("" if np.isnan(p) else f"{p:.6f}"), str(alarm)
            lines[n] = ",".join(r)
        f.write_text("\n".join(lines) + "\n")
    markers = (raw / "markers.csv").read_text().splitlines()
    (out / "markers.csv").write_text("\n".join(
        [markers[0]] + [m for m in markers[1:] if m[:10] in dates]) + "\n")
    return dates, counts, note


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", default=f"out/paper_demo/seed{config.EXPORT_SEED}")
    ap.add_argument("--out", default="out/scada/demo_source")
    ap.add_argument("--dates", nargs="*", help="local dates YYYY-MM-DD (default: first test days)")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--no-faults", action="store_true")
    a = ap.parse_args()
    dates, counts, note = build(a.run, a.out, a.dates, a.days, not a.no_faults)
    print(note)
    print(f"alarms on these days: {counts}")
    print(f"wrote {a.out}. Replay it with:  python -m scada.run --replay {a.out}")


if __name__ == "__main__":
    main()
