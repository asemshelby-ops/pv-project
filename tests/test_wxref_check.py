"""wxref/check.py: the daily PYR20 check on clear-sky hours, on SYNTHETIC clear days."""
import numpy as np
import pandas as pd
import pytest

import config
from tests.wx_fixtures import SITE, clear_days
from wxref import Site, check


@pytest.fixture(scope="module")
def step_days():
    """12 clear days; from day 10 on the PYR20 reads 0.9 x what it read before."""
    return clear_days(n=12, scale=lambda d: 0.9 if d >= 9 else 1.0)


def _status(res):
    return dict(zip(res["date"], res["status"]))


def test_irradiance_scaled_by_0_9_is_flagged(step_days):
    meas, model = step_days
    res, hours = check.run(meas, model, SITE)
    st = list(res["status"])
    assert st[:6] == ["BASELINE"] * 6                       # 7 valid days build the reference
    assert st[6:10] == ["OK"] * 4                           # day 10: one low day is no flag yet
    assert st[10:] == ["DRIFT", "DRIFT"]                    # rolling median of 3 has moved
    last = res.iloc[-1]
    assert last["drift_pct"] == pytest.approx(-10.0, abs=0.2)
    assert last["reference_ratio"] == pytest.approx(0.97, abs=0.002)
    assert last["trend_pct_per_week"] < -2
    assert "dirty dome" in last["message"]
    assert (res["clear_hours"] == res["clear_hours"].iloc[0]).all()   # 0.9 hides no hour


def test_fixed_reference_flags_a_sensor_that_was_low_from_the_start(monkeypatch):
    meas, model = clear_days(n=4, scale=lambda d: 0.9)
    monkeypatch.setattr(config, "WX_RATIO_REFERENCE", 0.97)
    res, _ = check.run(meas, model, SITE)
    assert (res["status"] == "DRIFT").all()


def test_gross_error_is_flagged_at_once_and_its_hours_are_still_found():
    meas, model = clear_days(n=2, scale=lambda d: 0.6)       # e.g. a wrong scaling
    res, _ = check.run(meas, model, SITE)
    assert list(res["status"]) == ["OUT_OF_RANGE"] * 2
    assert (res["clear_hours"] >= config.WX_CHECK_MIN_HOURS).all()


def test_tilt_change_is_a_known_event_and_restarts_the_reference():
    site = Site(21.42, 39.83, 180.0, (("2000-01-01", 35.0), ("2026-11-08", 20.0)))
    # after the re-tilt the model and the PYR20 agree less well (transposition): ratio -6 %
    meas, model = clear_days(n=12, site=site, scale=lambda d: 0.94 if d >= 7 else 1.0)
    res, hours = check.run(meas, model, site)
    st = _status(res)
    assert st["2026-11-08"] == "KNOWN_EVENT"
    assert "tilt change 35 -> 20" in res.set_index("date").loc["2026-11-08", "message"]
    after = [st[d] for d in sorted(st) if d > "2026-11-08"]
    assert "DRIFT" not in after and after[0] == "BASELINE"     # new reference, no false flag
    assert set(res["epoch"]) == {0, 1}
    assert set(hours[hours["date"] >= "2026-11-08"]["tilt_deg"]) == {20.0}


def test_cloudy_hours_are_skipped_not_flagged():
    meas, model = clear_days(n=2)
    model = model.copy()
    model["wx_model_poa_wm2"] *= 0.5                         # the model sees cloud all day
    res, hours = check.run(meas, model, SITE)
    assert list(res["status"]) == ["NO_CLEAR_HOURS"] * 2 and not hours["clear"].any()
    # model cloud cover at the hour ends also rules hours out (from a weather source)
    meas, model = clear_days(n=1)
    cloud = model.assign(wx_source="forecast", wx_model_cloud_cover_pct=80.0)
    res, hours = check.run(meas, pd.concat([model, cloud]), SITE)
    assert res["status"].iloc[0] == "NO_CLEAR_HOURS"


def test_unsteady_pyr20_hours_are_skipped():
    meas, model = clear_days(n=1)
    rng = np.random.default_rng(0)
    passing = (meas.index // 360) % 2 == 0                  # broken cloud every other 1 h
    meas = meas.copy()
    meas.loc[passing, "irradiance"] *= rng.uniform(0.3, 1.0, passing.sum())
    _, hours = check.run(meas, model, SITE)
    day = hours[hours["cs_poa_wm2"] >= config.WX_CS_MIN_POA_WM2]
    assert 0 < day["clear"].sum() < len(day)
    assert (day.loc[day["clear"], "meas_cv"] <= config.WX_CS_MAX_CV).all()


def test_no_measured_rows_or_no_model_gives_no_data_rows():
    meas, model = clear_days(n=1)
    res, _ = check.run(meas, model.iloc[0:0], SITE, dates=["2026-11-01"])
    assert list(res["status"]) == ["NO_DATA"]


def test_clear_sky_index_is_taken_in_the_plane_of_the_module():
    t = np.array([int(pd.Timestamp("2026-12-21 12:10", tz="Asia/Riyadh").timestamp())])
    s35 = Site(21.42, 39.83, 180.0, (("2000-01-01", 35.0),))
    s0 = Site(21.42, 39.83, 180.0, (("2000-01-01", 0.0),))
    assert check.clear_sky_poa(t, s35)[0] > 1.1 * check.clear_sky_poa(t, s0)[0]   # winter sun


def test_ratio_uses_the_hour_ending_at_the_model_stamp():
    meas, model = clear_days(n=1)
    _, hours = check.run(meas, model, SITE)
    h = hours.iloc[5]
    inside = meas[(meas["t_utc"] > h["t_utc"] - 3600) & (meas["t_utc"] <= h["t_utc"])]
    assert h["meas_mean_wm2"] == pytest.approx(inside["irradiance"].mean())
    assert h["coverage"] == pytest.approx(1.0)
