"""Checks of detect/: ring buffer, features, the three detectors, no look-ahead."""
import numpy as np
import pandas as pd
import pvlib
import pytest

import config
from data import synth
from data.schema import BACK_SENSORS, COLUMNS
from detect import gbt, stream
from detect.cusum import CusumDetector
from detect.features import (N10, FeatureExtractor, Ring, clear_sky_poa_simple,
                             feature_names, median3, solar_angles)
from detect.threshold import ThresholdDetector
from sim import thermal

DT = config.DT_S


def make_day(t_mod, g, ta=30.0, wind=2.0, date="2026-12-01", start="10:00:00"):
    """Schema-format table from a given module temperature trace."""
    n = len(t_mod)
    ts = pd.date_range(f"{date} {start}", periods=n, freq="10s")
    df = pd.DataFrame({"date": ts.strftime("%Y-%m-%d"), "time": ts.strftime("%H:%M:%S")})
    for c in BACK_SENSORS:
        df[c] = t_mod
    df["T_ambient"] = ta
    df["humidity"] = 40.0
    df["irradiance"] = g
    df["voltage"], df["current"], df["power"] = 18.0, 5.0, 90.0
    df["wind"] = wind
    return df[COLUMNS]


@pytest.fixture(scope="module")
def synth_days():
    return synth.generate(seed=3, n_days=4)


# --- building blocks -----------------------------------------------------------

def test_ring_buffer():
    r = Ring(3)
    for x in (1.0, 2.0, 3.0, 4.0):
        r.push(x)
    assert r.full() and (r.ago(0), r.ago(1), r.ago(2)) == (4.0, 3.0, 2.0)


def test_median3():
    assert median3(1, 9, 5) == 5 and median3(9, 1, 1) == 1 and median3(2, 3, 1) == 2


def test_solar_model_matches_pvlib():
    # Clear-sky index normaliser: NOAA geometry vs pvlib SPA; simple vs Ineichen
    t = pd.date_range("2026-12-21 07:00", "2026-12-21 17:00", freq="10min", tz=config.TZ)
    sp = pvlib.solarposition.get_solarposition(t, config.LAT, config.LON)
    cz = np.array([solar_angles(x.day_of_year, x.hour * 60 + x.minute)[0] for x in t])
    assert np.abs(cz - np.cos(np.radians(sp.zenith.to_numpy()))).max() < 0.01
    mine = np.array([clear_sky_poa_simple(*solar_angles(x.day_of_year, x.hour * 60 + x.minute))
                     for x in t])
    ref = synth.clear_sky_poa(t)
    high = sp.elevation.to_numpy() > 20
    assert np.all(np.abs(mine[high] / ref[high] - 1) < 0.15)


def test_features_wait_for_full_buffers_and_wind_flag():
    day = make_day(np.full(200, 40.0), np.full(200, 800.0))
    for use_wind in (True, False):
        fx = FeatureExtractor(use_wind)
        out = [fx.update(s) for _, s in stream.samples(day)]
        first = next(i for i, f in enumerate(out) if f is not None)
        assert first == config.MEDIAN_N - 1 + N10      # median fill + 10-min window
        assert set(feature_names(use_wind)) <= set(out[-1])
        assert ("wind_drop" in out[-1]) == use_wind


# --- (a) threshold rule --------------------------------------------------------

def ramp(n, k0, rise, minutes):
    t = np.full(n, 40.0)
    k1 = k0 + int(minutes * 60 / DT)
    t[k0:k1] = 40.0 + rise * np.arange(k1 - k0) / (k1 - k0)
    t[k1:] = 40.0 + rise
    return t


def test_threshold_fires_on_3c_in_10min_once():
    day = make_day(ramp(400, 100, 3.0, 8), np.full(400, 800.0))
    al = stream.run(ThresholdDetector(), day)
    assert al.sum() == 1


def test_threshold_ignores_small_rise():
    day = make_day(ramp(400, 100, 2.0, 8), np.full(400, 800.0))
    assert stream.run(ThresholdDetector(), day).sum() == 0


def test_threshold_gate_blocks_irradiance_change():
    # The 3 degC ramp runs over rows 100-148; a +12.5 % irradiance step at row
    # 140 sits inside every window in which the rise reaches 2.5 degC.
    # (The gate only blocks while the step is inside the 10-min window: a rise
    # that continues after the step leaves it is NOT blocked -- the known
    # weakness of this baseline on irradiance-driven rises.)
    g = np.full(400, 800.0)
    g[140:] = 900.0
    day = make_day(ramp(400, 100, 3.0, 8), g)
    assert stream.run(ThresholdDetector(), day).sum() == 0
    g[:] = 800.0                         # same ramp, steady sun -> alarm
    assert stream.run(ThresholdDetector(), make_day(ramp(400, 100, 3.0, 8), g)).sum() == 1


def test_threshold_median_removes_single_glitch():
    t = np.full(300, 40.0)
    t[150] = 46.0
    assert stream.run(ThresholdDetector(), make_day(t, np.full(300, 800.0))).sum() == 0


def test_threshold_silence_10_min():
    # two ramps 5 min apart: only the first may alarm
    t = ramp(600, 100, 3.0, 5)
    t[160:] += np.clip(np.arange(440) / 30 * 3.0, 0, 3.0)
    al = stream.run(ThresholdDetector(), make_day(t, np.full(600, 800.0)))
    idx = np.flatnonzero(al)
    assert len(idx) >= 1 and np.all(np.diff(idx) >= config.SILENCE_MIN * 6)


# --- (b) CUSUM -----------------------------------------------------------------

def model_trace(g, v, ta=30.0):
    n = len(g)
    return thermal.simulate(thermal.steady_state(g[0], ta, v[0]), g, np.full(n, ta), v)


def test_cusum_quiet_when_model_explains_cloud():
    # Temperature follows the model through a cloud at the model's own wind
    n = 1500
    g = np.full(n, 850.0)
    g[500:600] = 300.0
    v = np.full(n, config.CUSUM_WIND_CONST)
    day = make_day(model_trace(g, v), g, wind=config.CUSUM_WIND_CONST)
    assert stream.run(CusumDetector(use_wind=False), day).sum() == 0


def test_cusum_fires_on_wind_lull():
    n = 1500
    g = np.full(n, 850.0)
    v = np.full(n, 2.0)
    v[700:820] = 1.0                       # 20-min lull: +5 degC steady state
    day = make_day(model_trace(g, v), g, wind=v)
    for use_wind in (True, False):
        al = stream.run(CusumDetector(use_wind=use_wind), day)
        assert al[700:820].any(), use_wind
        assert not al[:700].any(), use_wind


# --- (c) GBT -------------------------------------------------------------------

def test_targets_positive_window_and_neutral_mask():
    day = make_day(np.full(400, 40.0), np.full(400, 800.0))
    ev = pd.DataFrame({"start": ["2026-12-01 10:10:00", "2026-12-01 10:40:00"],
                       "end": ["2026-12-01 10:30:00", "2026-12-01 10:50:00"],
                       "class": ["spike", "minor_lull"], "effect_C": [4, 2], "note": ""})
    y, use = gbt.sample_targets(day, ev)
    k0 = 60                                # 10:10 is row 60
    assert y[k0:k0 + 60].all() and y.sum() == 60
    assert not use[k0 + 60:k0 + 121].any()          # spike tail: left out
    assert not use[240:301].any()                    # minor_lull: left out
    assert use[:k0].all()


def test_gbt_trains_small_and_deterministic(synth_days):
    days, events = synth_days
    for use_wind in (True, False):
        X = gbt.build_features(days, use_wind)
        y, use = gbt.sample_targets(days, events)
        m1, m2 = gbt.train(X, y, use, seed=0), gbt.train(X, y, use, seed=0)
        ok = ~np.isnan(X).any(axis=1)
        assert np.array_equal(m1.booster_.predict(X[ok]), m2.booster_.predict(X[ok]))
        dump = m1.booster_.dump_model()
        assert len(dump["tree_info"]) <= config.GBT_N_ESTIMATORS
        assert m1.n_features_in_ == len(feature_names(use_wind))


# --- no look-ahead: changing the future never changes past alarms ---------------

@pytest.mark.parametrize("which", ["threshold", "cusum", "gbt"])
def test_no_look_ahead(synth_days, which):
    days, events = synth_days
    day = days[days["date"] == days["date"].iloc[0]].reset_index(drop=True)
    if which == "threshold":
        make = ThresholdDetector
    elif which == "cusum":
        make = lambda: CusumDetector(use_wind=True)
    else:
        X = gbt.build_features(days, True)
        y, use = gbt.sample_targets(days, events)
        model = gbt.train(X, y, use, seed=0)
        make = lambda: gbt.GbtDetector(model, True, p_threshold=0.3)
    cut = len(day) // 2
    a1 = stream.run(make(), day)
    future = day.copy()
    future.loc[cut:, BACK_SENSORS] += 10.0          # violent change after `cut`
    future.loc[cut:, "irradiance"] *= 0.3
    a2 = stream.run(make(), future)
    assert np.array_equal(a1[:cut], a2[:cut])


# --- hybrid NASIM-GBT -------------------------------------------------------------

def test_hybrid_features_equal_cusum_baseline_state(synth_days):
    # The hybrid features must be computed by exactly the CUSUM baseline's code
    days, _ = synth_days
    day = days[days["date"] == days["date"].iloc[0]].reset_index(drop=True)
    fx = FeatureExtractor(use_wind=True, hybrid=True)
    det = CusumDetector(use_wind=True, k=config.HYBRID_CUSUM_K, h=1e12)   # never alarms
    for _, s in stream.samples(day):
        f = fx.update(s)
        det.update(s)
        if f is not None:
            assert f["cusum_d"] == det.core.d and f["cusum_S"] == det.core.S
    assert f is not None and feature_names(True, True)[-2:] == ["cusum_d", "cusum_S"]


def test_sqrt_class_weighting_is_milder(synth_days):
    days, events = synth_days
    X = gbt.build_features(days, False)
    y, use = gbt.sample_targets(days, events)
    full = gbt.train(X, y, use, seed=0, weighting="full").get_params()["scale_pos_weight"]
    sqrt = gbt.train(X, y, use, seed=0, weighting="sqrt").get_params()["scale_pos_weight"]
    assert sqrt == pytest.approx(np.sqrt(full)) and sqrt < full


def test_gbt_persistence_needs_consecutive_samples():
    class Fixed:                       # stand-in booster returning a scripted p
        def __init__(self, ps):
            self.ps = iter(ps)

        def predict(self, x):
            return [next(self.ps)]
    day = make_day(np.full(200, 40.0), np.full(200, 800.0))
    first = config.MEDIAN_N - 1 + N10                 # first row with features
    ps = [0.0] * 200
    ps[0] = 0.9                                       # single spike of p: no alarm
    ps[10], ps[11] = 0.9, 0.9                         # two in a row: alarm
    det = gbt.GbtDetector(Fixed(ps), use_wind=True, p_threshold=0.5, persist=2)
    al = stream.run(det, day)
    assert np.flatnonzero(al).tolist() == [first + 11]
