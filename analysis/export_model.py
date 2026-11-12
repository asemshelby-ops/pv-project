"""Export the NASIM-GBT model of a pipeline run to C and check C against Python.

    python -m analysis.export_model --run out/paper_demo/seed5

Reads <run>/raw (logger files) and <run>/results (events.csv, tuned.json, the saved
model). Steps:
  1. retrain on the training days (training is deterministic) and require the result to
     equal the saved model;
  2. write export/c/generated/nasim_model_nowind.c (m2cgen) and nasim_config.h;
  3. run the test days through the Python detector and through the compiled C detector
     (double and float builds) and compare every probability, alarm and feature;
  4. write <run>/results/parity.json and table7_parity.csv.
The on-board check is NOT done here. export/replay.py flashes the board and replays a
test set, but it still builds that set from the pre-10-Nov evaluation (eval/run.py); it
must be pointed at this run's test days before the on-board result can be quoted.
The wind model file in export/c/generated is NOT touched: there is no anemometer in this
phase; the file is only linked because the PC harness references its symbol.
"""
import argparse
import json
import pathlib
import subprocess

import lightgbm as lgb
import numpy as np
import pandas as pd

import config
from analysis import detection, ingest
from detect import gbt
from export import run as ex


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", default=f"out/paper_demo/seed{config.EXPORT_SEED}")
    ap.add_argument("--note", default="SYNTHETIC data (hard variant), paper spike definition")
    a = ap.parse_args()
    run, res = pathlib.Path(a.run), pathlib.Path(a.run) / "results"
    tuned = json.loads((res / "tuned.json").read_text())
    thr, seed = tuned["nasim_p_threshold"], config.EXPORT_SEED
    frame = ingest.detector_frame(ingest.read_logger(run / "raw"))
    events = pd.read_csv(res / "events.csv")
    m = ingest.read_markers(run / "raw" / "markers.csv")[0]
    excluded = ingest.excluded_windows(ingest.join_logsheets(m, run / "raw" / "logsheets")[0])
    parts = detection.split_days(frame["date"].unique())
    train = frame[frame["date"].isin(parts["train"])].reset_index(drop=True)
    test = frame[frame["date"].isin(parts["test"])].reset_index(drop=True)

    print(f"retraining on {len(parts['train'])} training days ...", flush=True)
    y, use = gbt.sample_targets(train, detection.training_events(
        events[events["date_local"].isin(parts["train"])], excluded))
    clf = gbt.train(gbt.build_features(train, False, hybrid=True), y, use, seed,
                    names=detection.NAMES, weighting=config.GBT_CLASS_WEIGHT)
    saved = lgb.Booster(model_file=str(res / "nasim_gbt_nowind.txt"))
    same = clf.booster_.model_to_string().split("parameters:")[0] == \
        saved.model_to_string().split("parameters:")[0]
    if not same:
        raise SystemExit("retrained model differs from the saved one")

    ex.GEN_DIR.mkdir(parents=True, exist_ok=True)
    ex.OUT.mkdir(parents=True, exist_ok=True)
    (ex.GEN_DIR / "nasim_config.h").write_text(ex.config_header())
    (ex.GEN_DIR / "nasim_model_nowind.c").write_text(
        ex.model_source(clf, "nowind", detection.NAMES, thr, f"Source: {a.note}."))

    gcc = ex.find_gcc()
    if gcc is None:
        raise SystemExit("model exported, but no gcc found: parity NOT checked")
    inp = ex.OUT / "paper_test_input.csv"
    ex.write_input(test, inp)
    print(f"Python reference on {len(parts['test'])} test days ...", flush=True)
    py = ex.python_reference(clf, False, thr, test)
    rows = []
    for single in (False, True):
        exe = ex.compile_parity(gcc, single)
        outp = ex.OUT / f"paper_c_{'float' if single else 'double'}.csv"
        subprocess.run([str(exe), "0", repr(float(thr)), str(config.GBT_PERSIST), str(inp),
                        str(outp)], check=True)
        r = ex.compare(py, ex.read_c_output(outp, len(detection.NAMES)), detection.NAMES)
        rows.append({"build": "float" if single else "double", "samples": r["samples"],
                     "valid samples": r["valid_samples"], "max |dp|": f"{r['max_abs_dp']:.3g}",
                     "alarms Python": r["alarms_python"], "alarms C": r["alarms_c"],
                     "alarm mismatches": r["alarm_mismatches"],
                     "worst feature diff": f"{max(r['max_abs_feature_diff'].values()):.3g}"})
        print(rows[-1], flush=True)
    table = pd.DataFrame(rows)
    table.to_csv(res / "table7_parity.csv", index=False)
    (res / "parity.json").write_text(json.dumps(
        {"p_threshold": thr, "persist": config.GBT_PERSIST, "identical_to_saved_model": same,
         "model": ex.model_structure(clf.booster_), "builds": rows,
         "on_board": "not run: needs the ESP32 and an adapted export/replay.py"}, indent=2))
    if rows[0]["alarm mismatches"] != 0:
        raise SystemExit("double build does not reproduce Python")
    print(f"p threshold for firmware/logger/logger.ino: {thr}")


if __name__ == "__main__":
    main()
