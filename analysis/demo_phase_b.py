"""The Phase B preparation end to end on SYNTHETIC rig files. NOT ADOPTED: it shows that the
new parts run and find again what the generator put into the data; it says nothing about
a real spray, a real module or a real load-off test.

    python -m analysis.demo_phase_b                  (20 days, seed 5)
    python -m analysis.demo_phase_b --days 10        (quick look)

Two synthetic campaigns, each run through analysis/pipeline.py exactly as real files
would be, with the switches of config.py turned on for this run only (they stay off in
the file):
  trial     a wet spray trial (analysis/fake_phase_b.py): the six Phase B columns, the
            spray log and a sweep every minute. Switches PHASE_B_ENABLED and ECT_ENABLED:
            arms side by side, energy balance, cleaning check, ECT against the sensors.
  loadoff   two open-circuit tests a day (fake_logger.add_load_off_tests) with coded
            markers. Switch LOADOFF_CLASS_ENABLED: the cause `load_off` and the verdict
            with and without those spikes.
Results: <out>/seed<seed>/trial/results and .../loadoff/results (summary.md and the CSV
tables). The sections that are new are also printed.
"""
import argparse
import pathlib

import config
from analysis import fake_logger, fake_phase_b, pipeline
from data import synth

NEW_SECTIONS = ("## Phase B", "## Equivalent cell temperature", "## Verdict")


def _with_switches(names, run):
    """Runs `run()` with the named switches of config.py on, and puts them back."""
    before = {n: getattr(config, n) for n in names}
    try:
        for n in names:
            setattr(config, n, True)
        return run()
    finally:
        for n, v in before.items():
            setattr(config, n, v)


def trial(seed, n_days, root, n_boot=2000, log=print):
    """Synthetic wet spray trial -> pipeline with the Phase B and ECT steps."""
    raw = root / "trial" / "raw"
    days, _ = synth.generate(seed, n_days=n_days, hard=True)
    truth, _ = fake_phase_b.build(days, raw)
    truth.to_csv(root / "trial" / "generator_events.csv", index=False)
    return _with_switches(("PHASE_B_ENABLED", "ECT_ENABLED"), lambda: pipeline.run(
        raw, root / "trial" / "results", seed=seed, n_boot=n_boot, synthetic=True, log=log))


def loadoff(seed, n_days, root, n_boot=2000, log=print):
    """Synthetic campaign with two open-circuit tests a day -> both verdicts."""
    raw = root / "loadoff" / "raw"
    days, _ = synth.generate(seed, n_days=n_days, hard=True)
    dates = sorted(days["date"].unique())
    days, schedule = fake_logger.add_load_off_tests(
        days, [(d, hms, 12) for d in dates for hms in ("10:30:00", "14:00:00")])
    for d in dates:
        schedule[d] += [("06:10:00", "cleaning start", "synthetic"),
                        ("06:20:00", "cleaning end", "synthetic")]
    fake_logger.write_logger_files(days, raw)
    fake_logger.write_markers(schedule, raw, codes=True)
    return _with_switches(("LOADOFF_CLASS_ENABLED",), lambda: pipeline.run(
        raw, root / "loadoff" / "results", seed=seed, n_boot=n_boot, synthetic=True, log=log))


def new_sections(summary_path):
    """The sections of a summary.md that the Phase B preparation adds."""
    keep, out = False, []
    for line in pathlib.Path(summary_path).read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            keep = line.startswith(NEW_SECTIONS)
        if keep:
            out.append(line)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=config.EXPORT_SEED)
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--out", default="out/phase_b_demo")
    ap.add_argument("--only", choices=("trial", "loadoff"))
    a = ap.parse_args()
    root = pathlib.Path(a.out) / f"seed{a.seed}"
    for name, step in (("trial", trial), ("loadoff", loadoff)):
        if a.only in (None, name):
            res = step(a.seed, a.days, root)
            print(f"\n===== {name}: SYNTHETIC, {a.days} days, seed {a.seed} =====\n")
            print("\n".join(new_sections(pathlib.Path(res["out"]) / "summary.md")))
    print(pipeline.SYNTHETIC)


if __name__ == "__main__":
    main()
