"""The paper's analysis, from the rig's files to figures and tables.

    python -m analysis.pipeline --data <folder> --out <folder>
           [--reference ref.csv --validation-date YYYY-MM-DD]

<folder> holds what comes off the SD card: /YYYYMMDD.csv (10 s rows, UTC), /markers.csv,
logsheets/YYYY-MM-DD.csv (typed log sheets), and, once the load exists, the sweep files
/SWYYYYMMDD.csv and /SSYYYYMMDD.csv. Steps:
  1. ingest (UTC -> Makkah time, segments, day quality), markers joined to log sheets
  2. label spikes and their cause; excluded windows; controlled tests
  3. detectors: threshold, CUSUM, NASIM-GBT -- chronological split, tuned on validation
  4. gamma from the shade-and-uncover runs (if sweep files exist)
  5. comparison with the reference data logger (if an export is given)
  6. figures (PDF + PNG), tables (CSV + LaTeX) and summary.md in --out
A step whose input is missing is skipped and said so in summary.md.

Optional, OFF by default (config.WX_ENABLED, or --weather): Open-Meteo MODEL data for the
same days -- wx_model_hourly.csv, wx_daily_check.csv (the PYR20 check) and a section in
summary.md; res["frame_wx"] has the model columns (wx_model_*, incl. wind) joined by time
for analysis and the twin. The detectors never see them. Off: every output as before.
"""
import argparse
import json
import pathlib

import numpy as np
import pandas as pd

import config
from analysis import detection, events, gamma, ingest, report, soiling, validate, verdict

DEFAULT_MAPPING = {   # ESP32 column -> (reference column, scale); an ASSUMPTION until the
    "T1": ("TC1", 1.0), "T3": ("TC2", 1.0), "T5": ("TC3", 1.0),   # data-logger model is known
    "irradiance": ("PYR_mV", 1.0), "voltage": ("V_dc", 1.0), "current": ("I_dc", 1.0)}
SYNTHETIC = ("SYNTHETIC DATA. These numbers show that the pipeline runs end to end on files in "
             "the rig's formats. They say nothing about the real module.")


def run(data, out, reference=None, mapping=None, validation_date=None, drivers=None,
        seed=0, n_boot=2000, synthetic=False, log=print, weather=None):
    data, out = pathlib.Path(data), pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    report.style()
    notes, files = [], []

    df = ingest.read_logger(data)
    frame = ingest.detector_frame(df)
    quality = ingest.day_quality(df)
    quality.to_csv(out / "day_quality.csv", index=False)
    n_days = frame["date"].nunique()
    log(f"ingested {len(df)} rows, {n_days} days, {frame['segment'].nunique()} segments")

    joined = excluded = tests = None
    dusty_dates = []
    if (data / "markers.csv").exists():
        m, no_time = ingest.read_markers(data / "markers.csv")
        joined, problems = ingest.join_logsheets(m, data / "logsheets")
        excluded, tests = ingest.excluded_windows(joined), ingest.controlled_tests(joined)
        notes += [f"marker problem: {p}" for p in problems]
        if no_time:
            notes.append(f"{no_time} marker presses had no RTC time and were left out")
        # soiling housekeeping (docs/paper/05 sec. 9): flags and a check, nothing is excluded
        dusty = soiling.dust_days(joined)
        dusty_dates = list(dusty["date"])
        quality["after_dust"] = quality["date"].isin(dusty["date"])
        quality.to_csv(out / "day_quality.csv", index=False)
        if len(dusty):
            notes.append(f"{len(dusty)} days flagged after a dust storm line (until the next "
                         "cleaning): " + ", ".join(dusty["date"]) + ". They are NOT excluded")
        soil = soiling.cleaning_check(df, joined)
        soil.to_csv(out / "soiling_check.csv", index=False)
        n_chk, n_ok = int((soil["note"] == "check").sum()), int(soil["change"].notna().sum())
        notes.append(f"cleaning check (P/G at midday, day before vs day after): {n_ok} of "
                     f"{len(soil)} cleanings computable, {n_chk} with a change above "
                     f"{config.SOIL_CHECK_REL:.0%} (soiling_check.csv)")
    else:
        notes.append("no markers.csv: no excluded windows, no controlled-test labels")

    ev = events.label(frame, excluded, tests)
    if drivers is not None:
        ev = events.attach_truth(ev, drivers)
    ev.to_csv(out / "events.csv", index=False)
    spikes = ev[(ev["class"] == "spike") & ev["valid"]]
    log(f"events: {len(spikes)} valid spikes, {(ev['class'] == 'minor').sum()} minor rises")

    det = detection.run(frame, ev, excluded, seed=seed, n_boot=n_boot, log=log)
    det["results"].to_csv(out / "detection_results.csv", index=False)
    det["curves"].to_csv(out / "detection_curves.csv", index=False)
    det["matched"].to_csv(out / "detection_matched.csv", index=False)
    claim = verdict.evaluate(det["matched"], det["spikes_per_part"]["train"])
    (out / "verdict.json").write_text(json.dumps(claim, indent=2, default=str), encoding="utf-8")
    log(f"verdict on the detector claim: {claim['outcome']}")
    (out / "tuned.json").write_text(json.dumps(
        {**{k: float(v) for k, v in det["tuned"].items()}, "split_days": {
            k: [v[0], v[-1], len(v)] for k, v in det["parts"].items()},
         "spikes_per_part": det["spikes_per_part"]}, indent=2))
    det["model"].booster_.save_model(str(out / "nasim_gbt_nowind.txt"))

    g_sum, runs = None, []
    points, summary = gamma.read_sweeps(data)
    if len(points) and joined is not None:
        sweeps = gamma.sweep_parameters(points, summary)
        if sweeps["low_G"].any():
            notes.append(f"{int(sweeps['low_G'].sum())} of {len(sweeps)} sweeps below "
                         f"{config.SWEEP_MIN_G_WM2:.0f} W/m2 (or without G) left out of gamma "
                         "and of the power cost")
        runs = gamma.gamma_runs(sweeps, joined)
        g_sum = gamma.gamma_summary(runs)
        if not g_sum["n_runs"]:
            g_sum = None
            notes.append("sweep files found but no valid gamma run")
    else:
        notes.append("no sweep files (load not fitted): gamma and the power cost are not computed")

    v_table = None
    if reference is not None:
        esp = df[df["date"] == validation_date] if validation_date else df
        v_table, lag, aligned = validate.compare(esp, pd.read_csv(reference),
                                                 mapping or DEFAULT_MAPPING)
        report.table_validation(v_table, out)
        files.append(report.fig_validation(*aligned["T3"], "T3 (°C)", out))
    else:
        notes.append("no reference export: the data-logger comparison is not computed")

    # the flagged days stay in every result; this shows what they change (05 sec. 9)
    sens_s = sens_d = None
    if dusty_dates:
        sens_s, sens_d, n_dusty_test = soiling.sensitivity(frame, ev, excluded, det, dusty_dates)
        sens_s.to_csv(out / "sensitivity_dust_spikes.csv", index=False)
        sens_d.to_csv(out / "sensitivity_dust_detection.csv", index=False)

    test_days = det["parts"]["test"]
    in_test = spikes[spikes["date_local"].isin(test_days)]
    pick = test_days[0]
    if len(in_test):                     # the test day with the most causes, then most spikes
        c = in_test.groupby("date_local")["cause"].agg(["nunique", "size"])
        pick = c.sort_values(["nunique", "size"]).index[-1]
    files.append(report.fig_example_day(frame, ev, det["alarms"], pick, out, synthetic))
    files.append(report.fig_spike_statistics(ev, n_days, out))
    files.append(report.fig_detection_delay(det["detail"], out))
    files.append(report.fig_delay_vs_false_alarms(det["curves"], out))
    t1 = report.table_spikes(ev, n_days, g_sum["gamma"] if g_sum else None, out)
    t2 = report.table_detection(det["results"], out)
    t3 = report.table_per_cause(det["results"], out)
    t6 = report.table_cause_check(ev, out)
    t8 = report.table_matched(det["matched"], out)
    t9 = report.table_cause_sensitivity(events.cause_sensitivity(ev), out)
    if g_sum:
        files.append(report.fig_gamma(runs, g_sum, out))
        report.table_gamma(runs, g_sum, out)

    md = lambda t: t.to_markdown(index=False) if hasattr(t, "to_markdown") else str(t)  # noqa: E731
    try:
        md(t1)
    except ImportError:                      # pandas needs `tabulate` for to_markdown
        md = lambda t: "```\n" + t.to_string(index=False) + "\n```"  # noqa: E731
    lines = ["# Analysis summary", ""]
    if synthetic:
        lines += [f"**{SYNTHETIC}**", ""]
    lines += [f"Data: {n_days} days, {len(df)} rows, mean completeness "
              f"{quality['completeness'].mean():.1%}, {frame['segment'].nunique()} detector segments.",
              f"Split (whole local days): " + ", ".join(
                  f"{k} {len(v)} days ({det['spikes_per_part'][k]} spikes)"
                  for k, v in det["parts"].items()) + ".", "",
              "## Table 1 - spikes", "", md(t1), "",
              "## Table 2 - detection, test days", "", md(t2), "",
              f"Tuned on validation days: {json.dumps({k: float(v) for k, v in det['tuned'].items()})}",
              "", "NASIM-GBT minus baseline, same resampled days (mean, 95 % interval):", ""]
    for base, d in det["differences"].items():
        lines.append(f"- vs {base}: " + "; ".join(
            f"{k} {v[0]:+.2f} ({v[1]:+.2f} to {v[2]:+.2f})" for k, v in d.items()))
    lines += ["", "## Table 3 - per cause", "", md(t3), "",
              "## Table 8 - detectors at equal false-alarm budgets", "", md(t8), ""]
    lines += verdict.markdown(claim, synthetic)
    if t6 is not None:
        lines += ["## Table 6 - cause rule against known causes", "", md(t6), ""]
    lines += ["## Table 9 - sensitivity of the cause labels to the two limits", "", md(t9), ""]
    lines += ["## Sensitivity - with and without the days flagged after a dust storm", ""]
    if sens_s is None:
        lines += ["No day is flagged `after_dust`: the two versions are the same.", ""]
    else:
        lines += [f"{len(dusty_dates)} days are flagged ({', '.join(dusty_dates)}); "
                  f"{n_dusty_test} of them are test days. **The flagged days are part of every "
                  "result above.** The second line of each pair only shows what they change. "
                  "The detectors are not retrained or retuned for it: the same alarms are "
                  "scored on fewer days.", "", "Spikes, all days of the data set:", "",
                  md(sens_s.round(2)), "", "Detection, test days:", ""]
        lines += ([md(sens_d.round(2)), ""] if n_dusty_test and len(sens_d) else
                  ["No test day is flagged, or none is left without the flagged ones: "
                   "the detection results have no second version.", ""])
    if g_sum:
        lines += ["## Gamma", "", f"gamma = {100 * g_sum['gamma']:.3f} %/degC, U(k=2) = "
                  f"{100 * g_sum['U_k2']:.3f} %/degC, {g_sum['n_runs']} runs on {g_sum['n_days']} days; "
                  f"Isc-normalised {100 * g_sum['gamma_isc_normalised']:.3f} %/degC.", ""]
    if v_table is not None:
        lines += ["## Table 5 - against the reference logger", "",
                  md(v_table.drop(columns=["kind", "intercept", "ref_mean"]).round(3)), ""]
    wx = None
    if config.WX_ENABLED if weather is None else weather:    # MODEL data, opt-in (wxref/)
        from wxref import analysis as wxa
        wx = wxa.model_for(df, log=log)
        wxa.hourly_table(wx, df, "synthetic" if synthetic else "logger").to_csv(
            out / "wx_model_hourly.csv", index=False)
        wx["checks"].to_csv(out / "wx_daily_check.csv", index=False)
        wx["frame_wx"] = wxa.frame_with_model(frame, wx)
        lines += wxa.summary_lines(wx)
    lines += ["## Notes", ""] + [f"- {n}" for n in notes] + ["", "## Figures", ""] + [f"- {f}" for f in files]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"-> {out / 'summary.md'}")
    res = {"frame": frame, "events": ev, "detection": det, "verdict": claim, "gamma": g_sum,
           "validation": v_table, "notes": notes, "out": out}
    if wx is not None:
        res["weather"], res["frame_wx"] = wx, wx["frame_wx"]
    return res


def main():
    ap = argparse.ArgumentParser(description="PV thermal-spike paper analysis")
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--reference", default=None, help="data-logger export (CSV)")
    ap.add_argument("--validation-date", default=None, help="local date of the comparison day")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--weather", action="store_true",
                    help="add the Open-Meteo MODEL outputs for this run (as WX_ENABLED)")
    a = ap.parse_args()
    run(a.data, a.out, a.reference, None, a.validation_date, seed=a.seed,
        weather=a.weather or None)


if __name__ == "__main__":
    main()
