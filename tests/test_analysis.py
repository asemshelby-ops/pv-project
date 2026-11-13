"""Tests of the paper pipeline (analysis/): ingestion of logger files, spike labelling,
the detectors under the 10 Nov definition, scoring, gamma and the logger comparison.
All data is synthetic and written to real files first (analysis/fake_logger.py)."""
import numpy as np
import pandas as pd
import pytest

import config
from analysis import (detection, events, fake_logger, gamma, ingest, metrics, pipeline, report,
                      validate)
from data import synth
from detect import gbt, stream
from detect.cusum_inc import CusumIncDetector
from detect.threshold import ThresholdDetector


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    out = tmp_path_factory.mktemp("rig")
    days, drivers = synth.generate(seed=5, n_days=6, hard=True)
    fake_logger.write_logger_files(days, out)
    dates = sorted(days["date"].unique())
    schedule = {d: [("06:10:00", "cleaning start", ""), ("06:20:00", "cleaning end", "")]
                for d in dates}
    schedule[dates[0]] += [("11:05:00", "gamma shade on", ""), ("11:30:00", "gamma uncover", ""),
                           ("11:50:00", "gamma end", "")]
    fake_logger.write_markers(schedule, out)
    return out, days, drivers, dates


def test_ingest_converts_utc_to_local_and_segments(rig):
    out, days, _, dates = rig
    df = ingest.read_logger(out)
    assert df["time_utc"].iloc[0] == "03:00:00" and df["time"].iloc[0] == "06:00:00"
    assert sorted(df["date"].unique()) == dates
    frame = ingest.detector_frame(df)
    assert frame["segment"].nunique() == len(dates)            # one unbroken run per day
    assert len(frame) == len(days) - len(dates)                # the 18:00:00 row is outside
    np.testing.assert_allclose(frame["T3"].to_numpy()[:50], days["T3"].to_numpy()[:50])
    q = ingest.day_quality(df)
    assert (q["completeness"] == 1.0).all() and (q["segments"] == 1).all()


def test_ingest_bridges_one_missing_value_and_breaks_at_a_long_gap(rig, tmp_path):
    out, _, _, _ = rig
    f = sorted(out.glob("2*.csv"))[0]
    lines = f.read_text().splitlines()
    cells = lines[500].split(",")
    cells[9] = ""                                              # one unusable row: bridged
    lines[500] = ",".join(cells)
    del lines[1000:1010]                                       # 10 rows absent: a new segment
    (tmp_path / f.name).write_text("\n".join(["# status: test"] + lines) + "\n")
    df = ingest.read_logger(tmp_path)
    assert ingest.detector_frame(df)["segment"].nunique() == 2
    assert (df["segment"] == -1).sum() >= 1


def test_markers_join_and_windows(rig):
    out, _, _, dates = rig
    m, no_time = ingest.read_markers(out / "markers.csv")
    assert no_time == 0 and m.groupby("date_local")["marker"].max().iloc[0] == 5
    joined, problems = ingest.join_logsheets(m, out / "logsheets")
    assert problems == []
    assert joined["t_local"].iloc[0] == pd.Timestamp(f"{dates[0]} 06:10:00.137")
    ex = ingest.excluded_windows(joined)
    assert (ex["reason"] == "cleaning").sum() == len(dates)
    assert (ex["reason"] == "gamma run").sum() == 1
    first = ex[ex["reason"] == "cleaning"].iloc[0]
    assert first["end"] - first["start"] == pd.Timedelta(
        minutes=10 + config.CLEANING_EXCLUDE_MIN, milliseconds=137)
    _, problems = ingest.join_logsheets(m, out / "no_such_folder")
    assert len(problems) == len(dates) and "no log sheet" in problems[0]


def test_find_rises_on_a_known_step():
    n = 600
    t = np.full(n, 40.0)
    k = np.arange(n - 200)
    t[200:] += 5.0 * (1 - np.exp(-k * config.DT_S / 380.0))    # 5 degC step, tau 6.3 min
    (i_on, i_th, i_pk), = events.find_rises(events.smooth(t), config.SPIKE_THETA_C)
    assert abs(i_on - 200) <= 4
    assert 200 + 25 < i_th < 200 + 60                           # 3 of 5 degC after ~0.92 tau
    assert i_pk > 200 + 100
    assert events.find_rises(events.smooth(40.0 + 0.01 * np.arange(200)), 3.0) == []


def test_labels_include_cloud_return_rises_with_cause(rig):
    out, _, drivers, _ = rig
    frame = ingest.detector_frame(ingest.read_logger(out))
    ev = events.attach_truth(events.label(frame), drivers)
    sp = ev[ev["class"] == "spike"]
    minor = ev[ev["class"] == "minor"]
    assert len(sp) >= 5 and set(ev["class"]) <= {"spike", "minor"}
    assert (sp["size_C"] >= config.SPIKE_THETA_C).all()
    assert (minor["size_C"] >= config.SPIKE_MINOR_C).all()
    pure = sp[sp["cause_true"].isin(["irradiance", "non_irradiance"])]
    assert (pure["cause"] == pure["cause_true"]).mean() >= 0.8  # the rule against the generator
    assert (sp["cause_true"] == "irradiance").any()             # sun-return rises ARE spikes now


def test_excluded_windows_and_tests_set_validity_and_origin(rig):
    out, _, _, _ = rig
    frame = ingest.detector_frame(ingest.read_logger(out))
    plain = events.label(frame)
    first = plain[plain["class"] == "spike"].iloc[0]
    t_on = pd.Timestamp(first["start"])
    excluded = pd.DataFrame([{"start": t_on - pd.Timedelta(minutes=5),
                              "end": t_on + pd.Timedelta(minutes=5), "reason": "cleaning"}])
    tests = pd.DataFrame([{"on": t_on - pd.Timedelta(minutes=20), "off": t_on,
                           "origin": "ctl_mesh"}])
    ev = events.label(frame, excluded, tests)
    row = ev[ev["start"] == first["start"]].iloc[0]
    assert not row["valid"] and row["note"] == "excluded: cleaning"
    assert row["origin"] == "ctl_mesh" and (ev["origin"] == "natural").sum() == len(ev) - 1


def test_array_alarm_rules_equal_the_streaming_detectors(rig):
    out, _, _, _ = rig
    frame = ingest.detector_frame(ingest.read_logger(out))
    seg = frame["segment"].to_numpy()
    X = gbt.build_features(frame, use_wind=False, hybrid=True)
    rise = X[:, detection.NAMES.index("rise_10m")]
    np.testing.assert_array_equal(detection.alarm_rule(rise, 2.0, seg),
                                  stream.run(ThresholdDetector(rise_c=2.0, gate=None), frame))
    t_f = detection.filtered_temperature(frame)
    fast = detection.cusum_inc_alarms(t_f, seg, 0.02, 1.0)
    np.testing.assert_array_equal(fast, stream.run(CusumIncDetector(0.02, 1.0), frame))
    assert fast.sum() > 0


def test_scoring_rules():
    ts = pd.date_range("2026-11-15 08:00:00", periods=720, freq="10s")
    day = "2026-11-15"
    ev = pd.DataFrame([
        dict(event_id=1, date_local=day, start=f"{day} 08:10:00", t_theta=f"{day} 08:15:00",
             end=f"{day} 08:25:00", size_C=5.0, cause="irradiance", origin="natural", valid=True),
        dict(event_id=2, date_local=day, start=f"{day} 09:00:00", t_theta=f"{day} 09:06:00",
             end=f"{day} 09:15:00", size_C=4.0, cause="non_irradiance", origin="natural", valid=True),
        dict(event_id=3, date_local=day, start=f"{day} 09:30:00", t_theta=f"{day} 09:35:00",
             end=f"{day} 09:40:00", size_C=2.5, cause="mixed", origin="natural", valid=True)])
    ev["class"] = ["spike", "spike", "minor"]
    alarms = np.zeros(len(ts), bool)
    # in spike 1 (TP), again in spike 1 (ignored), in its 2-min grace (ignored),
    # in the minor rise (neutral), outside everything (false alarm)
    for hms in ("08:12:00", "08:20:00", "08:26:30", "09:32:00", "09:50:00"):
        alarms[ts == pd.Timestamp(f"{day} {hms}")] = True
    m, fa, neutral = metrics.score(ts, alarms, ev)
    assert m["detected"].tolist() == [True, False] and neutral == 1 and len(fa) == 1
    assert m["delay_s"].iloc[0] == 120 and m["lead_s"].iloc[0] == 180   # 3 min before 3 degC
    table = metrics.per_day(m, fa, [day])
    s = metrics.summarise(table, m)
    assert (s["TP"], s["FN"], s["FP"]) == (1, 1, 1)
    assert s["recall_irradiance"] == 1.0 and s["recall_non_irradiance"] == 0.0
    assert s["F1"] == pytest.approx(0.5) and s["FA_per_day"] == 1.0
    assert s["delay_median_min_irradiance"] == 2.0 and np.isnan(s["delay_median_min_mixed"])
    ci = metrics.bootstrap(table, m, n=50)
    assert ci["F1"] == (0.5, 0.5)                               # one day: nothing to resample

    # valid observation time: 08:00-10:00 logged, 30 min of it excluded -> 1.5 h
    ex = pd.DataFrame({"start": [pd.Timestamp(f"{day} 08:30:00")],
                       "end": [pd.Timestamp(f"{day} 08:59:50")], "reason": ["cleaning"]})
    obs = metrics.observation_hours(ts, ex)
    assert obs[day] == pytest.approx(1.5)
    s2 = metrics.summarise(metrics.per_day(m, fa, [day], obs), m)
    assert s2["obs_hours"] == pytest.approx(1.5)
    assert s2["FA_per_day"] == pytest.approx(1 / (1.5 / 12))    # per 12 valid hours
    assert s2["FA_per_100h"] == pytest.approx(100 / 1.5)
    assert metrics.observation_hours(ts)[day] == pytest.approx(2.0)


def test_matched_setting_respects_the_budget_and_the_recall_floor():
    def item(name, fa, recall, delay):
        return (name, None, {"FA_per_day": fa, "recall": recall, "delay_median_min": delay}, {})
    scored = [item("slow", 0.0, 1.0, 8.0), item("fast", 0.3, 0.95, 2.0),
              item("faster but blind", 0.1, 0.5, 1.0), item("middle", 0.1, 0.92, 5.0)]
    assert detection._matched(scored, 0.05)[0] == "slow"
    assert detection._matched(scored, 0.1)[0] == "middle"       # not the one below 90 % recall
    assert detection._matched(scored, 0.5)[0] == "fast"
    assert detection._matched([item("x", 0.4, 1.0, 1.0)], 0.1) is None


def test_cause_sensitivity_counts_and_known_causes():
    f = [0.10, 0.30, 0.45, 0.55, 0.70, 0.90]
    ev = pd.DataFrame({"f_G": f, "class": "spike", "valid": True, "origin": "natural",
                       "cause_true": ["non_irradiance"] * 3 + ["irradiance"] * 3})
    ev["cause"] = ev["f_G"].map(events.cause_from_fraction)
    assert ev["cause"].tolist() == ["non_irradiance"] * 2 + ["mixed"] * 2 + ["irradiance"] * 2
    s = events.cause_sensitivity(ev).set_index("f_irr")
    used = s[s["configured"]].iloc[0]
    assert (used["irradiance"], used["non_irradiance"], used["mixed"], used["relabelled"]) == (2, 2, 2, 0)
    assert (used["known_right"], used["known_mixed"], used["known_opposite"]) == (4, 2, 0)
    half = s.loc[0.5]                                           # no "mixed" class at all
    assert (half["irradiance"], half["non_irradiance"], half["mixed"]) == (3, 3, 0)
    assert half["relabelled"] == 2 and half["known_right"] == 6
    wide = s.loc[0.75]                                          # limits 0.25 / 0.75
    assert (wide["mixed"], wide["relabelled"]) == (4, 2)
    ctl = ev.drop(columns="cause_true").assign(origin=["ctl_windbreak"] + ["natural"] * 4 + ["ctl_mesh"])
    assert events.cause_sensitivity(ctl)["known"].tolist() == [2] * 4


def test_pipeline_runs_end_to_end_with_the_reviewer_outputs(rig, tmp_path):
    out, _, drivers, _ = rig
    res = pipeline.run(out, tmp_path, drivers=drivers, seed=5, n_boot=20, synthetic=True,
                       log=lambda *a: None)
    det = res["detection"]
    assert "Irradiance step" in det["alarms"] and "irradiance_step_rel" in det["tuned"]
    assert set(det["curves"]["detector"]) == set(detection.SWEPT)
    assert det["curves"].groupby("detector")["tuned_F1"].sum().eq(1).all()
    assert len(det["matched"]) == len(config.MATCH_FA_PER_DAY) * len(detection.SWEPT)
    r = det["results"].set_index("detector")
    assert (r["obs_hours"] < 12.0 * len(det["parts"]["test"])).all()   # the cleaning is taken out
    for name in ("fig6_delay_vs_false_alarms.pdf", "table8_matched_false_alarms.tex",
                 "table9_cause_sensitivity.csv", "detection_curves.csv"):
        assert (tmp_path / name).exists(), name
    # wide tables span both columns; table 3 is one row per cause and detector
    for stem in report.WIDE_TABLES - {"table4_gamma", "table5_validation"}:   # no load, no reference here
        assert "\\begin{table*}" in (tmp_path / f"{stem}.tex").read_text(encoding="utf-8"), stem
    assert "\\begin{table}" in (tmp_path / "table3_per_cause.tex").read_text(encoding="utf-8")
    t3 = pd.read_csv(tmp_path / "table3_per_cause.csv")
    assert list(t3.columns) == ["cause", "n", "detector", "recall", "F1", "delay min"]
    assert len(t3) == len(metrics.CAUSES) * len(report.DETECTORS)


def test_paper_pulls_wide_tables_and_figures_with_the_wide_macros():
    import pathlib
    import re
    tex = (pathlib.Path(__file__).resolve().parent.parent / "paper/main.tex").read_text(encoding="utf-8")
    assert set(re.findall(r"^\\resulttablewide\{([^}]+)\}", tex, re.M)) == report.WIDE_TABLES
    assert set(re.findall(r"^\\resultfigwide\{([^}]+)\}", tex, re.M)) == report.WIDE_FIGURES
    narrow = set(re.findall(r"^\\result(?:table|fig)\{([^}]+)\}", tex, re.M))
    assert not narrow & (report.WIDE_TABLES | report.WIDE_FIGURES)


def test_gamma_recovers_the_generator_coefficient(tmp_path):
    runs = [("2026-12-01", "11:30:00"), ("2026-12-01", "13:00:00"), ("2026-12-02", "11:40:00")]
    true_gamma = fake_logger.write_gamma_runs(runs, tmp_path, seed=1)
    fake_logger.write_markers({
        "2026-12-01": [("11:30:00", "gamma uncover", ""), ("11:50:00", "gamma end", ""),
                       ("13:00:00", "gamma uncover", ""), ("13:20:00", "gamma end", "")],
        "2026-12-02": [("11:40:00", "gamma uncover", ""), ("12:00:00", "gamma end", "")]},
        tmp_path)
    m, _ = ingest.read_markers(tmp_path / "markers.csv")
    joined, _ = ingest.join_logsheets(m, tmp_path / "logsheets")
    points, summary = gamma.read_sweeps(tmp_path)
    sweeps = gamma.sweep_parameters(points, summary)
    assert sweeps["v_min"].max() < 0.5 * sweeps["V_oc"].min()   # the sweep reaches the flat part
    res = gamma.gamma_summary(gamma.gamma_runs(sweeps, joined))
    assert res["n_runs"] == 3 and res["n_days"] == 2
    assert -0.006 < true_gamma < -0.003                          # a plausible synthetic value
    assert res["gamma"] == pytest.approx(true_gamma, rel=0.05)
    assert res["gamma_isc_normalised"] == pytest.approx(true_gamma, rel=0.08)
    assert res["alpha"] == pytest.approx(fake_logger.SYN_ALPHA_ISC, abs=3e-4)


def test_validation_finds_the_clock_lag_and_offsets(rig, tmp_path):
    out, days, _, dates = rig
    fake_logger.write_reference_export(days, dates[2], tmp_path, clock_ahead_s=3.0,
                                       tc_offset_c=0.4)
    esp = ingest.read_logger(out)
    esp = esp[esp["date"] == dates[2]]
    ref = pd.read_csv(tmp_path / "reference.csv")
    mapping = {"T1": ("TC1", 1.0), "T3": ("TC2", 1.0), "irradiance": ("PYR_mV", 1.0),
               "voltage": ("V_dc", 1.0), "current": ("I_dc", 1.0)}
    table, lag, aligned = validate.compare(esp, ref, mapping)
    assert len(aligned["T3"][0]) == len(aligned["T3"][1]) > 4000
    assert abs(lag - 3.0) <= 1.0
    row = table.set_index("quantity")
    assert row.at["T3", "bias"] == pytest.approx(-0.4, abs=0.05)
    assert bool(row.at["T3", "passes"])
    assert row.at["current", "slope"] == pytest.approx(1 / 1.004, abs=0.003)


def test_sweeps_below_the_irradiance_limit_are_left_out_of_gamma(tmp_path):
    """config.SWEEP_MIN_G_WM2: one named limit for every use of sweep values."""
    assert config.SWEEP_MIN_G_WM2 == 200.0
    runs = [("2026-12-01", "11:30:00"), ("2026-12-01", "13:00:00")]
    true_gamma = fake_logger.write_gamma_runs(runs, tmp_path, seed=1)
    fake_logger.write_markers({"2026-12-01": [
        ("11:30:00", "gamma uncover", ""), ("11:50:00", "gamma end", ""),
        ("13:00:00", "gamma uncover", ""), ("13:20:00", "gamma end", "")]}, tmp_path)
    m, _ = ingest.read_markers(tmp_path / "markers.csv")
    joined, _ = ingest.join_logsheets(m, tmp_path / "logsheets")

    # second run: weak sun. Its sweeps keep flags = 0 and a steady G, so only the limit
    # can remove them; P_mp is scaled with G so that a fit, if made, would look normal.
    f = tmp_path / "SS20261201.csv"
    ss = pd.read_csv(f, dtype={"date": str, "time": str})
    second = ss["time"] >= "10:00:00"                             # 13:00 local = 10:00 UTC
    assert 0 < second.sum() < len(ss)
    ss.loc[second, ["G_before", "G_after"]] = 199
    ss.loc[ss.index[second][0], ["G_before", "G_after"]] = 200    # exactly at the limit: kept
    ss.to_csv(f, index=False)

    points, summary = gamma.read_sweeps(tmp_path)
    sweeps = gamma.sweep_parameters(points, summary)
    assert int(sweeps["low_G"].sum()) == int(second.sum()) - 1
    usable = gamma.usable_sweeps(sweeps)
    assert usable["G"].min() >= config.SWEEP_MIN_G_WM2 and len(usable) == len(sweeps) - sweeps["low_G"].sum()
    res = gamma.gamma_runs(sweeps, joined)
    assert [r["valid"] for r in res] == [True, False]             # the weak-sun run is not fitted
    assert res[1]["note"] == "fewer than 10 usable sweeps" and res[1]["n_low_G"] > 50
    assert res[0]["n_low_G"] == 0
    total = gamma.gamma_summary(res)
    assert total["n_runs"] == 1 and total["gamma"] == pytest.approx(true_gamma, rel=0.05)

    ss.loc[second, ["G_before", "G_after"]] = float("nan")        # G not recorded: left out too
    ss.to_csv(f, index=False)
    sweeps = gamma.sweep_parameters(*gamma.read_sweeps(tmp_path))
    assert int(sweeps["low_G"].sum()) == int(second.sum())
    assert len(gamma.usable_sweeps(sweeps)) == len(sweeps) - second.sum()
