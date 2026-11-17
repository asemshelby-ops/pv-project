"""Minimum detectable effect of the spray trial (analysis/mde.py): the power calculation
against textbook values, against a direct numerical integration and against a simulation
of the test itself; the spread taken from an event table; the script on a SYNTHETIC dry-run
campaign. Every number here is synthetic or typed in: nothing says what the real module
will show."""
import math
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
from scipy import integrate, stats

import config
from analysis import mde


def power_by_integration(ncp, dof, alpha):
    """P(|T| > t_crit) for T = (Z + ncp) / sqrt(V / dof), Z standard normal and V chi-square
    with dof degrees of freedom: the definition, integrated over V. Independent of the
    non-central distributions that mde.power() uses."""
    t = stats.t.ppf(1 - alpha / 2, dof)

    def f(v):
        s = math.sqrt(v / dof)
        return (stats.norm.sf(t * s - ncp) + stats.norm.cdf(-t * s - ncp)) * stats.chi2.pdf(v, dof)
    lo, hi = stats.chi2.ppf(1e-13, dof), stats.chi2.ppf(1 - 1e-13, dof)
    return integrate.quad(f, lo, hi, limit=400, epsabs=1e-12, epsrel=1e-10)[0]


def test_power_is_the_false_alarm_rate_without_an_effect_and_grows_with_effect_and_events():
    for n in (2, 5, 20, 200):
        assert mde.power(0.0, 1.0, n) == config.MDE_ALPHA
        steps = [mde.power(e, 1.0, n) for e in (0.0, 0.1, 0.3, 1.0, 3.0, 10.0)]
        assert steps == sorted(steps) and steps[-1] <= 1.0
    assert [mde.power(0.5, 1.0, n) for n in (5, 20, 80)] == sorted(mde.power(0.5, 1.0, n) for n in (5, 20, 80))
    assert mde.power(-0.7, 2.0, 12) == mde.power(0.7, 2.0, 12)            # two-sided
    assert math.isnan(mde.power(1.0, 1.0, 1)) and math.isnan(mde.power(1.0, 0.0, 10))


@pytest.mark.parametrize("n", (2, 3, 10, 20, 200, 1000))
def test_power_agrees_with_a_direct_integration_and_is_never_nan(n):
    scale = math.sqrt(2.0 / n)
    for ncp in (1e-6, 0.01, 0.5, 2.0, 2.8, 5.0, 10.0, 23.68, 37.0, 200.0):    # scipy's non-central
        p = mde.power(ncp * scale, 1.0, n)                                     # t is NaN at some of these
        assert p == p and abs(p - power_by_integration(ncp, 2 * n - 2, config.MDE_ALPHA)) < 1e-8


def test_textbook_sample_sizes():
    """Cohen's table for a two-sided test at 0.05 and power 0.80: 393 / 64 / 26 per group for
    a difference of 0.2 / 0.5 / 0.8 standard deviations (exact values 393.4, 63.8, 25.5,
    so the whole number that reaches the power is 394, 64 and 26)."""
    assert [mde.events_needed(1.0, d, 0.05, 0.80) for d in (0.2, 0.5, 0.8)] == [394, 64, 26]
    assert mde.events_needed(3.0, 1.5, 0.05, 0.80) == 64                     # only the ratio counts
    assert mde.events_needed(1.0, 0.0) == math.inf
    assert mde.events_needed(1.0, 0.001, n_max=1000) == math.inf


def test_mde_is_the_effect_at_which_the_power_is_reached():
    for n in (2, 5, 10, 40, 300):
        d = mde.mde(1.0, n)
        assert abs(mde.power(d, 1.0, n) - config.MDE_POWER) < 1e-9
        assert abs(mde.mde(0.0114, n) / 0.0114 - d) < 1e-9                    # proportional to the spread
        assert mde.events_needed(1.0, d * 1.0001) == n
    assert mde.mde(1.0, 40) < mde.mde(1.0, 10) < mde.mde(1.0, 5)
    assert abs(mde.mde(1.0, 80) / mde.mde(1.0, 20) - 0.5) < 0.01              # four times the events: half
    assert mde.mde(1.0, 20, target=0.9) > mde.mde(1.0, 20) > mde.mde(1.0, 20, alpha=0.10)
    assert math.isnan(mde.mde(1.0, 1)) and math.isnan(mde.mde(math.nan, 10))
    for n in (5, 10, 40, 300):
        assert abs(mde.mde_approx(1.0, n) / mde.mde(1.0, n) - 1.0) < 0.002


def test_a_simulated_trial_finds_the_mde_about_four_times_in_five():
    """The calculation against the test it describes: draw both arms 20000 times with a
    true difference equal to the MDE and count how often the t-test rejects."""
    rng = np.random.default_rng(12)
    n, sd = 10, 0.0114
    d = mde.mde(sd, n)
    a = rng.normal(d, sd, (20000, n))
    b = rng.normal(0.0, sd, (20000, n))
    found = (stats.ttest_ind(a, b, axis=1).pvalue < config.MDE_ALPHA).mean()
    assert abs(found - config.MDE_POWER) < 0.01
    none = (stats.ttest_ind(rng.normal(0, sd, (20000, n)), b, axis=1).pvalue < config.MDE_ALPHA).mean()
    assert abs(none - config.MDE_ALPHA) < 0.006


def _events(rng, n, shift=0.0, sd=0.01):
    assign = np.tile([0, 1], n // 2)
    return pd.DataFrame({"assign": assign, "valid": True, "heldout": False,
                         "gain_rel": rng.normal(0, sd, n) + shift * assign,
                         "yield_W_per_kWm2": rng.normal(90, 2.5, n),
                         "dT_after_spray_C": rng.normal(0.5, 0.4, n)})


def test_spread_is_within_the_arms_so_a_real_effect_does_not_inflate_it():
    rng = np.random.default_rng(3)
    e = _events(rng, 400)
    sd, used = mde.spread(e)["gain_rel"]
    assert used == 400 and abs(sd - 0.01) < 0.001
    shifted = e.assign(gain_rel=e["gain_rel"] + 0.05 * e["assign"])           # a large spray effect
    assert abs(mde.spread(shifted)["gain_rel"][0] - sd) < 1e-12
    assert shifted["gain_rel"].std() > 2 * sd                                 # the plain sd would be fooled
    assert set(mde.spread(e)) == set(mde.QUANTITIES)


def test_spread_leaves_out_events_that_are_not_valid_not_assigned_or_held_out():
    rng = np.random.default_rng(4)
    e = _events(rng, 60)
    wild = e.copy()
    wild.loc[0:9, "gain_rel"] = 5.0
    for column, value in (("valid", False), ("heldout", True), ("assign", -1)):
        marked = wild.copy()
        marked.loc[0:9, column] = value
        sd, used = mde.spread(marked)["gain_rel"]
        assert used == 50 and sd < 0.02, column
    assert mde.spread(wild)["gain_rel"][0] > 1.0                              # they do count if left in
    assert math.isnan(mde.spread(e.iloc[:1])["gain_rel"][0])                  # one event: no spread


def test_table_gives_the_effect_in_the_units_a_reader_needs():
    t = mde.table(0.0114, [10, 20, 40], "gain_rel", reference_wh=30.0)
    assert list(t["events per arm"]) == [10, 20, 40] and list(t["events in all"]) == [20, 40, 80]
    d = t["MDE, fraction"].to_numpy()
    assert (np.diff(d) < 0).all()
    assert np.allclose(t["MDE, % of the window's energy"], 100 * d)
    assert np.allclose(t["MDE, Wh per event"], 30.0 * d)
    assert np.allclose(t["as mean cooling over the window, degC [EST]"], d / abs(config.GAMMA_P))
    assert np.allclose(t["MDE / sd"], d / 0.0114)
    other = mde.table(0.42, [10], "dT_after_spray_C")
    assert "MDE, degC" in other and "MDE, Wh per event" not in other


def test_synthetic_dry_run_campaign_gives_a_spread_and_no_spray_at_all():
    events, n_days = mde.synthetic_events(n_days=6, seed=5)
    assert n_days == 6 and len(events) >= 12
    assert events["dry_run"].all() and (events["spike_id"] < 0).all()         # nothing sprayed
    assert (events["litres"] == 0).all() and (events["spray_s"] == 0).all()
    assert set(events["assign"]) == {0, 1} and not events["heldout"].any()
    for q, (sd, used) in mde.spread(events).items():
        assert sd > 0 and used == int(events["valid"].sum()), q


def test_the_script_runs_from_a_typed_spread_and_from_a_results_folder(tmp_path):
    def run(*args):
        r = subprocess.run([sys.executable, "-m", "analysis.mde", *args], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-800:]
        return r.stdout
    out = run("--sd", "0.0114", "--per-arm", "10", "20", "--effect", "0.004")
    assert "typed in" in out and "SYNTHETIC" not in out
    assert f"{mde.events_needed(0.0114, 0.004)} events per arm" in out
    rows = [line.split() for line in out.splitlines() if line.strip().startswith(("10 ", "20 "))]
    assert [int(r[0]) for r in rows] == [10, 20] and abs(float(rows[1][2]) - mde.mde(0.0114, 20)) < 1e-5
    _events(np.random.default_rng(1), 80).assign(
        yield0_W_per_kWm2=90.0, H_kWh_m2=0.4).to_csv(tmp_path / "phase_b_events.csv", index=False)
    out = run("--results", str(tmp_path), "--quantity", "dT_after_spray_C", "--per-arm", "15")
    assert "from 80 events" in out and "MDE, degC" in out
