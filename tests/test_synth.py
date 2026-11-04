"""Checks of data/synth.py: schema, sampling, sensors, labels, reproducibility."""
import numpy as np
import pandas as pd
import pytest

import config
from data import synth
from data.schema import BACK_SENSORS, COLUMNS, EVENT_CLASSES, EVENT_COLUMNS
from sim import thermal


@pytest.fixture(scope="module")
def three_days():
    return synth.generate(seed=0, n_days=3)


def test_schema_exact(three_days):
    days, events = three_days
    assert list(days.columns) == COLUMNS
    assert list(events.columns) == EVENT_COLUMNS
    assert set(events["class"]) <= set(EVENT_CLASSES)


def test_10s_sampling_without_gaps(three_days):
    days, _ = three_days
    ts = pd.to_datetime(days["date"] + " " + days["time"])
    for _, t in ts.groupby(days["date"]):
        assert (t.diff().dropna() == pd.Timedelta(seconds=10)).all()
        assert t.iloc[0].strftime("%H:%M:%S") == config.DAY_START
        assert t.iloc[-1].strftime("%H:%M:%S") == config.DAY_END


def test_ds18b20_quantised(three_days):
    days, _ = three_days
    steps = days[BACK_SENSORS].to_numpy() / config.DS18B20_LSB_C
    assert np.allclose(steps, np.round(steps))


def test_same_seed_same_data_other_seed_differs(three_days):
    days, events = three_days
    d2, e2 = synth.generate(seed=0, n_days=3)
    pd.testing.assert_frame_equal(days, d2)
    pd.testing.assert_frame_equal(events, e2)
    d3, _ = synth.generate(seed=1, n_days=3)
    assert not days["T1"].equals(d3["T1"])


def test_adding_days_keeps_earlier_days(three_days):
    days, _ = three_days
    longer, _ = synth.generate(seed=0, n_days=4)
    pd.testing.assert_frame_equal(days, longer.iloc[:len(days)])


def test_irradiance_peak_near_solar_noon():
    # Clear-sky in-plane peak for a south-facing module falls at solar noon,
    # in Makkah ~12:04-12:35 local depending on season (PROJECT_STATE sec.3)
    times = pd.date_range("2026-12-15 06:00", "2026-12-15 18:00", freq="10s",
                          tz=config.TZ)
    poa = synth.clear_sky_poa(times)
    peak = times[int(np.argmax(poa))]
    assert pd.Timestamp("2026-12-15 12:00", tz=config.TZ) <= peak \
        <= pd.Timestamp("2026-12-15 12:40", tz=config.TZ)


def test_label_rule_spike_vs_minor_and_mixed():
    lulls = [dict(start=100, end=200, effect=3.5),   # spike
             dict(start=400, end=500, effect=2.0),   # minor_lull
             dict(start=800, end=900, effect=5.0)]   # overlaps the cloud -> mixed
    clouds = [dict(start=850, end=950, rise_end=1040),
              dict(start=2000, end=2050, rise_end=2140)]  # alone -> irradiance_rise
    ev = synth.label_events(lulls, clouds)
    got = [(e["start"], e["end"], e["cls"]) for e in ev]
    assert got == [(100, 200, "spike"), (400, 500, "minor_lull"),
                   (800, 1040, "mixed"), (2050, 2140, "irradiance_rise")]


def test_lull_inside_rise_window_is_mixed():
    # The overlap test covers the rise window after the cloud, not only the cloud
    ev = synth.label_events([dict(start=1000, end=1100, effect=4.0)],
                            [dict(start=800, end=950, rise_end=1040)])
    assert [e["cls"] for e in ev] == ["mixed"]


def test_spike_threshold_is_independent_of_detector():
    # Labels come from the supervisor's 3 degC, not the detector's 2.5 degC
    assert config.SPIKE_EFFECT_C == 3.0


def test_spike_events_show_a_temperature_rise():
    # In the generated data every spike should raise the measured back-sheet
    # temperature; check the median over all spikes of 10 seeded days
    days, events = synth.generate(seed=0, n_days=10)
    ts = pd.to_datetime(days["date"] + " " + days["time"])
    t = days[BACK_SENSORS].mean(axis=1).to_numpy()
    rises = []
    for _, e in events[events["class"] == "spike"].iterrows():
        i0 = int(np.searchsorted(ts, pd.Timestamp(e["start"])))
        i1 = int(np.searchsorted(ts, pd.Timestamp(e["end"])))
        rises.append(t[i0:i1 + 1].max() - t[i0])
    assert len(rises) >= 10
    assert np.median(rises) >= 2.5


def test_steady_effect_matches_thermal_model():
    # 2 -> 1 m/s at 900 W/m2 (constant): label effect equals the 5 degC of Step 1
    g, ta = np.full(10, 900.0), np.full(10, 30.0)
    eff = np.mean(thermal.steady_state(g, ta, np.full(10, 1.0))
                  - thermal.steady_state(g, ta, np.full(10, 2.0)))
    assert eff == pytest.approx(5.0, abs=0.1)


def _block(n, s, e, value):
    x = np.zeros(n)
    x[s:e] = value
    return x


def test_natural_episode_labelling():
    w = config.NATURAL_WINDOW_MIN * 6          # 15 min of 10 s samples
    # 20 min of 4 degC background effect -> natural_spike
    ep = synth.natural_episodes(_block(2000, 500, 620, 4.0), w)
    assert [e["cls"] for e in ep] == ["natural_spike"]
    assert ep[0]["effect"] == pytest.approx(4.0)
    assert ep[0]["start"] >= 500 - w and ep[0]["end"] <= 620 + w
    # 2.5 degC (between the floor and 3 degC) -> neutral natural_minor
    ep = synth.natural_episodes(_block(2000, 500, 620, 2.5), w)
    assert [e["cls"] for e in ep] == ["natural_minor"]
    # below the floor -> nothing
    assert synth.natural_episodes(_block(2000, 500, 620, 1.5), w) == []
    # strong but only 3 min long: the module cannot follow it -> nothing
    assert synth.natural_episodes(_block(2000, 500, 518, 6.0), w) == []


def test_natural_episode_uses_same_rule_as_lulls():
    # The same 3 degC threshold separates spike from neutral for both sources
    w = config.NATURAL_WINDOW_MIN * 6
    lo = synth.natural_episodes(_block(2000, 500, 700, config.SPIKE_EFFECT_C - 0.01), w)
    hi = synth.natural_episodes(_block(2000, 500, 700, config.SPIKE_EFFECT_C), w)
    assert lo[0]["cls"] == "natural_minor" and hi[0]["cls"] == "natural_spike"


def test_natural_overlapping_an_event_becomes_mixed():
    events = [dict(start=100, end=200, cls="spike", effect=4.0)]
    natural = [dict(start=180, end=300, cls="natural_spike", effect=3.5),
               dict(start=1000, end=1100, cls="natural_minor", effect=2.2)]
    got = [(e["start"], e["end"], e["cls"]) for e in synth.add_natural(events, natural)]
    assert got == [(100, 300, "mixed"), (1000, 1100, "natural_minor")]


def test_full_run_natural_effect_is_against_slow_baseline(three_days):
    # The generator emits only known classes, and natural rows carry an effect
    _, events = three_days
    nat = events[events["class"].str.startswith("natural")]
    assert nat["effect_C"].ge(config.NATURAL_MINOR_C).all()
