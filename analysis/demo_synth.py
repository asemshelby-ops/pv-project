"""End-to-end run of the paper pipeline on SYNTHETIC data written as rig files.

    python -m analysis.demo_synth                 (hard variant, seed 5, 150 days)
    python -m analysis.demo_synth --days 30       (quick look)

1. data/synth.py generates the days (hard variant). Its own event table is kept as
   `drivers.csv`: the generator's record of what it injected (clouds, hazes, wind lulls,
   warm-air steps). It is used only to check the cause labels, never to make them.
2. analysis/fake_logger.py writes the files a real campaign leaves behind: logger CSVs in
   UTC, markers and log sheets (a cleaning every morning; every 5th day one cloud is
   declared a "mesh test"), sweep files of six gamma runs, a reference-logger export.
3. analysis/pipeline.py runs on those files exactly as it will on real ones. The spikes
   are labelled by the paper's definition, so rises after a cloud are spikes with the
   cause "irradiance".
"""
import argparse
import pathlib

import pandas as pd

import config
from analysis import fake_logger, pipeline
from data import synth


def build(seed, n_days, root):
    raw = root / "raw"
    days, drivers = synth.generate(seed, n_days=n_days, hard=True)
    fake_logger.write_logger_files(days, raw)
    drivers.to_csv(root / "drivers.csv", index=False)
    dates = sorted(days["date"].unique())
    schedule = {d: [("06:10:00", "cleaning start", "synthetic"),
                    ("06:20:00", "cleaning end", "synthetic")] for d in dates}
    clouds = drivers[drivers["class"] == "irradiance_rise"]
    for d in dates[::5]:                                   # declare one cloud a mesh test
        c = clouds[clouds["start"].str.startswith(d)]
        c = c[(c["start"].str[11:] >= "08:30:00") & (c["start"].str[11:] <= "15:00:00")]
        if len(c):
            off = pd.Timestamp(c["start"].iloc[0])
            schedule[d] += [(str((off - pd.Timedelta(minutes=10)).time()), "mesh on", "synthetic"),
                            (str(off.time()), "mesh off", "synthetic")]
    gamma_days = [d for d in (dates[2:3] + dates[9:10] + dates[16:17])]
    runs = [(d, hms) for d in gamma_days for hms in ("11:30:00", "13:00:00")]
    for d, hms in runs:
        t = pd.Timestamp(f"{d} {hms}")
        schedule[d] += [(str((t - pd.Timedelta(minutes=25)).time()), "gamma shade on", "synthetic"),
                        (hms, "gamma uncover", "synthetic"),
                        (str((t + pd.Timedelta(minutes=20)).time()), "gamma end", "synthetic")]
    fake_logger.write_markers(schedule, raw)
    true_gamma = fake_logger.write_gamma_runs(runs, raw, seed=seed)
    val_date = dates[min(3, len(dates) - 1)]
    fake_logger.write_reference_export(days, val_date, raw, seed=seed)
    return raw, drivers, val_date, true_gamma


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=config.EXPORT_SEED)
    ap.add_argument("--days", type=int, default=config.HARD_N_DAYS)
    ap.add_argument("--out", default="out/paper_demo")
    a = ap.parse_args()
    root = pathlib.Path(a.out) / f"seed{a.seed}"
    raw, drivers, val_date, true_gamma = build(a.seed, a.days, root)
    res = pipeline.run(raw, root / "results", reference=raw / "reference.csv",
                       validation_date=val_date, drivers=drivers, seed=a.seed, synthetic=True)
    g = res["gamma"]
    if g:
        print(f"gamma: estimated {100 * g['gamma']:.3f} %/degC, generator {100 * true_gamma:.3f} %/degC")
    print(pipeline.SYNTHETIC)


if __name__ == "__main__":
    main()
