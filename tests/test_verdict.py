"""The verdict on the detector claim (analysis/verdict.py, criteria of docs/paper/03
sec. 10) and the campaign-length estimate (analysis/campaign_days.py). Hand-made numbers."""
import math

import pandas as pd
import pytest

import config
from analysis import campaign_days, verdict

B = config.PREREG_BUDGET_FA_PER_DAY


def row(detector, fa, recall, lead, left, d=None, r=(0.0, -0.1, 0.1), setting="x",
        spikes=30, non=6, mixed=4):
    out = {"budget_FA_per_day": B, "detector": detector, "setting": setting,
           "test_FA_per_day": fa, "test_recall": recall, "test_lead_median_min": lead,
           "test_remaining_median": left, "test_spikes": spikes, "test_n_irradiance": spikes - non - mixed,
           "test_n_non_irradiance": non, "test_n_mixed": mixed}
    if d is not None:
        out.update(dict(zip(["nasim_minus_delay", "nasim_minus_delay_lo", "nasim_minus_delay_hi"], d)))
        out.update(dict(zip(["nasim_minus_recall", "nasim_minus_recall_lo", "nasim_minus_recall_hi"], r)))
    return out


def table(nasim, cusum=None, step=None):
    rows = [row("Threshold (tuned)", 0.18, 1.00, 0.3, 0.56, d=(-4.1, -5.2, -2.9)),
            cusum or row("CUSUM", 0.07, 0.87, 2.3, 0.84, d=(-0.1, -1.0, 0.8)),
            step or {"budget_FA_per_day": B, "detector": "Irradiance step", "setting": float("nan")},
            nasim,
            row("NASIM-GBT", 0.9, 0.5, -1.0, 0.1) | {"budget_FA_per_day": 0.5}]   # another budget: ignored
    return pd.DataFrame(rows)


GOOD = row("NASIM-GBT", 0.07, 1.00, 3.8, 0.92)


def test_holds_and_distinctive_like_the_synthetic_run():
    v = verdict.evaluate(table(GOOD), train_spikes=193)
    assert v["outcome"] == "holds_distinctive" and v["precondition"][0]
    assert [ok for _, ok, _ in v["conditions"]] == [True] * 5
    assert {n: m for n, m, _ in v["baselines"]} == {
        "Threshold (tuned)": False, "CUSUM": False, "Irradiance step": False}
    assert v["sentence"].startswith(f"At {B:g} false alarms per day the tree model was the only one")
    text = "\n".join(verdict.markdown(v, synthetic=True))
    assert text.count("**PASS**") == 5 and "SYNTHETIC DATA" in text and "fails C2" in text


def test_a_baseline_that_is_as_early_and_meets_c1_c2_makes_it_not_distinctive():
    cusum = row("CUSUM", 0.07, 0.93, 2.3, 0.84, d=(-0.1, -1.0, 0.8))
    v = verdict.evaluate(table(GOOD, cusum=cusum), train_spikes=60)
    assert v["outcome"] == "holds_not_distinctive"
    assert "and so did CUSUM; for this module the simpler detector is sufficient" in v["sentence"]
    later = row("CUSUM", 0.07, 0.93, 2.3, 0.84, d=(-1.5, -2.4, -0.6))     # detectably later
    assert verdict.evaluate(table(GOOD, cusum=later), 60)["outcome"] == "holds_distinctive"


@pytest.mark.parametrize("change, failed", [
    (dict(fa=0.25), "C1"), (dict(recall=0.89), "C2"), (dict(lead=-0.2), "C3a"), (dict(left=0.45), "C3b")])
def test_each_condition_can_fail(change, failed):
    base = dict(fa=0.07, recall=1.00, lead=3.8, left=0.92) | change
    v = verdict.evaluate(table(row("NASIM-GBT", **base)), train_spikes=60)
    assert v["outcome"] == "does_not_hold"
    assert [n.split()[0] for n, ok, _ in v["conditions"] if not ok] == [failed]
    assert f"({failed})" in v["sentence"] and "the threshold rule is the better choice" in v["sentence"]


def test_c3c_needs_the_whole_interval_below_zero():
    t = table(GOOD)
    t.loc[t["detector"] == "Threshold (tuned)", "nasim_minus_delay_hi"] = 0.2
    v = verdict.evaluate(t, train_spikes=60)
    assert v["outcome"] == "does_not_hold" and "(C3c)" in v["sentence"]


@pytest.mark.parametrize("nasim, train", [
    (row("NASIM-GBT", 0.07, 1.0, 3.8, 0.92, spikes=19), 60),            # too few test spikes
    (row("NASIM-GBT", 0.07, 1.0, 3.8, 0.92, non=4, mixed=3), 60),       # 7 not irradiance-driven
    (GOOD, 49),                                                         # too few training spikes
    ({"budget_FA_per_day": B, "detector": "NASIM-GBT", "setting": float("nan")}, 60)])
def test_precondition_not_met_means_not_tested(nasim, train):
    v = verdict.evaluate(table(nasim), train_spikes=train)
    assert v["outcome"] == "not_tested" and not v["precondition"][0] and v["conditions"] == []
    assert v["sentence"] == "The number of spikes was too small to test the claim."


def test_primary_budget_is_one_of_the_compared_budgets():
    assert B in config.MATCH_FA_PER_DAY
    with pytest.raises(ValueError):
        verdict.evaluate(table(GOOD), 60, budget=0.33)


# --- campaign length ------------------------------------------------------------------------

def test_poisson_rate_interval():
    r, lo, hi = campaign_days.rate_interval(10, 5)
    assert r == 2.0 and lo == pytest.approx(4.795 / 5, abs=1e-3) and hi == pytest.approx(18.39 / 5, abs=1e-2)
    assert campaign_days.rate_interval(0, 5)[:2] == (0.0, 0.0)


def test_days_needed_orders_and_limits():
    assert campaign_days.days_expected(2.0, 20, 0.2) == 50          # 2 /day on a fifth of the days
    assert math.isinf(campaign_days.days_expected(0.0, 8, 0.2))
    d80 = campaign_days.days_with_chance(2.0, 20, 0.2)
    assert d80 > 50                                                 # the expected count is a coin toss
    n = round(0.2 * d80)
    assert campaign_days.stats.poisson.sf(19, 2.0 * n) >= 0.8
    assert campaign_days.stats.poisson.sf(19, 2.0 * round(0.2 * (d80 - 5))) < 0.8


def test_estimate_takes_the_hardest_requirement(tmp_path):
    table, total, rates = campaign_days.estimate(days=10, spikes=24, non_irradiance=3)
    assert list(table["requirement"]) == [
        ">= 20 test spikes", ">= 8 test spikes, not irradiance-driven", ">= 50 training spikes"]
    assert total["expected"] == table["expected"].max() == math.ceil(8 / (0.3 * 0.2))   # 134: the rare kind decides
    assert total["expected"] <= total["80 % chance"] <= total["cautious"]
    assert math.isinf(campaign_days.estimate(10, 5, 0)[1]["expected"])        # none seen: no estimate

    pd.DataFrame({"class": ["spike"] * 4 + ["minor"], "valid": [True, True, True, False, True],
                  "cause": ["irradiance", "mixed", "non_irradiance", "mixed", "mixed"],
                  "origin": ["natural", "natural", "ctl_mesh", "natural", "natural"]}
                 ).to_csv(tmp_path / "events.csv", index=False)
    pd.DataFrame({"date": ["2026-12-01", "2026-12-02", "2026-12-03"]}).to_csv(
        tmp_path / "day_quality.csv", index=False)
    assert campaign_days.from_results(tmp_path) == (3, 2, 1, 1)     # days, natural, non-irr, controlled
