"""Tests of the paper pipeline (analysis/): ingestion of logger files, spike labelling,
the detectors under the 10 Nov definition, scoring, gamma and the logger comparison.
All data is synthetic and written to real files first (analysis/fake_logger.py)."""
import numpy as np
import pandas as pd
import pytest

import config
from analysis import detection, events, fake_logger, gamma, ingest, metrics, validate
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
    ci = metrics.bootstrap(table, m, n=50)
    assert ci["F1"] == (0.5, 0.5)                               # one day: nothing to resample


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
