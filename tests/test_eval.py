"""Checks of eval/: scoring rules, chronological split, fast GBT path = streaming."""
import numpy as np
import pandas as pd
import pytest

from data import synth
from detect import gbt, stream
from eval import run
from eval.metrics import score
from eval.split import split_days

T0 = pd.Timestamp("2026-12-01 10:00:00")


def ts_grid(n=600):
    return pd.date_range(T0, periods=n, freq="10s")


def alarms_at(ts, minutes):
    a = np.zeros(len(ts), dtype=bool)
    for m in minutes:
        a[int(m * 6)] = True
    return a


def ev(rows):
    return pd.DataFrame([{"start": T0 + pd.Timedelta(minutes=s),
                          "end": T0 + pd.Timedelta(minutes=e), "class": c,
                          "effect_C": 0, "note": ""} for s, e, c in rows])


def test_first_alarm_in_spike_is_tp_later_ones_ignored():
    ts = ts_grid()
    events = ev([(10, 30, "spike")])
    m = score(ts, alarms_at(ts, [14, 25]), events, n_days=1)
    assert (m["TP"], m["FP"], m["FN"]) == (1, 0, 0)
    assert m["delay_median_min"] == pytest.approx(4.0)
    assert m["precision"] == 1.0 and m["recall"] == 1.0


def test_missed_spike_and_false_alarms():
    ts = ts_grid()
    events = ev([(10, 30, "spike"), (40, 55, "irradiance_rise")])
    m = score(ts, alarms_at(ts, [5, 45, 50]), events, n_days=2)
    assert (m["TP"], m["FN"], m["FP"], m["FA_irradiance_rise"]) == (0, 1, 3, 2)
    assert m["FA_per_day"] == 1.5 and m["F1"] == 0.0


def test_neutral_windows_ignored_and_mixed_counted():
    ts = ts_grid()
    events = ev([(5, 15, "minor_lull"), (20, 30, "mixed"), (35, 45, "natural_minor")])
    m = score(ts, alarms_at(ts, [10, 25, 40]), events, n_days=1)
    assert m["FP"] == 0 and m["mixed_excluded"] == 1


def test_natural_spike_scored_apart():
    ts = ts_grid()
    events = ev([(10, 30, "spike"), (40, 60, "natural_spike"), (70, 85, "natural_spike")])
    m = score(ts, alarms_at(ts, [12, 45, 50]), events, n_days=1)
    assert (m["TP"], m["FP"], m["spikes"]) == (1, 0, 1)        # main P/R unaffected
    assert (m["natural_spikes"], m["natural_detected"], m["natural_missed"],
            m["natural_alarms"]) == (2, 1, 1, 2)


def test_split_is_chronological_whole_days():
    days, events = synth.generate(seed=0, n_days=6)
    parts = split_days(days, events, sizes=(3, 2, 1))
    d = {k: sorted(v[0]["date"].unique()) for k, v in parts.items()}
    assert d["train"][-1] < d["val"][0] and d["val"][-1] < d["test"][0]
    assert not set(d["train"]) & set(d["val"]) and not set(d["val"]) & set(d["test"])
    for name, (dd, ee) in parts.items():
        assert set(pd.to_datetime(ee["start"]).dt.strftime("%Y-%m-%d")) <= set(dd["date"])


def test_fast_p_path_equals_streaming_detector():
    days, events = synth.generate(seed=2, n_days=3)
    y, use = gbt.sample_targets(days, events)
    model = gbt.train(gbt.build_features(days, True), y, use, seed=0)
    p = run.p_trace(model, True, days)
    for thr in (0.2, 0.5, 0.8):
        fast = run.alarms_from_p(p, thr, days)
        slow = stream.run(gbt.GbtDetector(model, True, p_threshold=thr), days)
        assert np.array_equal(fast, slow)


def test_mismatch_sets_are_8_and_20_percent():
    sets = list(run.mismatch_sets())
    assert len(sets) == 8
    c = sorted({round(mp["c"]) for _, mp in sets})
    assert c == [6960, 10440]                       # 8700 x 0.8, x 1.2


def test_minor_lull_alarms_counted_but_not_scored():
    ts = ts_grid()
    events = ev([(5, 15, "minor_lull"), (20, 30, "minor_lull")])
    m = score(ts, alarms_at(ts, [10]), events, n_days=1)
    assert m["FP"] == 0 and (m["minor_lulls"], m["minor_lulls_alarmed"]) == (2, 1)


def test_ambient_rise_is_neutral():
    ts = ts_grid()
    m = score(ts, alarms_at(ts, [10]), ev([(5, 20, "ambient_rise")]), n_days=1)
    assert m["FP"] == 0
