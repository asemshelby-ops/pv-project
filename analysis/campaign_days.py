"""How many campaign days are needed before the detector claim can be tested?

The precondition fixed in advance (docs/paper/03 sec. 10): on the TEST days at least
PREREG_MIN_TEST_SPIKES valid spikes, of which at least PREREG_MIN_TEST_NON_IRR are not
irradiance-driven, and at least PREREG_MIN_TRAIN_SPIKES spikes on the training days. The
split is chronological by whole days (config.PAPER_SPLIT: 60 / 20 / 20 %), so only a
fifth of the campaign counts towards the test numbers.

Input: the spike rate seen on pilot days. Either a pipeline result folder

    .venv/Scripts/python -m analysis.campaign_days --results out/pilot/results

(reads events.csv and day_quality.csv), or the counts typed by hand

    .venv/Scripts/python -m analysis.campaign_days --days 7 --spikes 12 --non-irradiance 4

Model: spikes arrive as a Poisson process with a constant rate per day [ASSUMPTION].
Three answers are printed, from optimistic to cautious:
  expected     the campaign length at which the EXPECTED counts reach the limits
  80 % chance  the length at which each limit is reached with 80 % probability
  cautious     the same, using the lower end of the 95 % interval of the pilot rates
What this cannot know: weather comes in runs (clear weeks have few cloud-return spikes),
so real counts scatter more than Poisson; the season changes the rate; controlled tests
(mesh, windbreak) add spikes on the days they are run and can be scheduled to fill a
shortfall, but they are reported separately from natural spikes. Treat the result as a
planning number and repeat it as pilot days accumulate.
"""
import argparse
import math
import pathlib

import pandas as pd
from scipy import stats

import config

CHANCE = 0.8          # [ASSUMPTION] "reached with 80 % probability"
MAX_DAYS = 2000       # the search stops here and says "not reachable"


def rate_interval(count, days, level=0.95):
    """Exact (Garwood) interval of a Poisson rate per day. Returns (rate, low, high)."""
    a = (1 - level) / 2
    low = stats.chi2.ppf(a, 2 * count) / 2 if count > 0 else 0.0
    high = stats.chi2.ppf(1 - a, 2 * count + 2) / 2
    return count / days, low / days, high / days


def days_expected(rate, need, share):
    """Campaign days at which the expected count on `share` of the days reaches `need`."""
    return math.inf if rate <= 0 else math.ceil(need / (rate * share))


def days_with_chance(rate, need, share, chance=CHANCE):
    """Smallest campaign length with P(Poisson(rate * whole days in the part) >= need) >=
    chance. The part gets round(share * days) whole days, as the split does."""
    if rate <= 0:
        return math.inf
    for days in range(1, MAX_DAYS + 1):
        if stats.poisson.sf(need - 1, rate * round(share * days)) >= chance:
            return days
    return math.inf


def requirements():
    """(label, which rate, count needed, share of the campaign days)."""
    train, _, test = config.PAPER_SPLIT
    return [("test spikes", "all", config.PREREG_MIN_TEST_SPIKES, test),
            ("test spikes, not irradiance-driven", "non", config.PREREG_MIN_TEST_NON_IRR, test),
            ("training spikes", "all", config.PREREG_MIN_TRAIN_SPIKES, train)]


def estimate(days, spikes, non_irradiance):
    """Table (one row per requirement) and the three campaign lengths (their maxima)."""
    rates = {"all": rate_interval(spikes, days), "non": rate_interval(non_irradiance, days)}
    rows = []
    for label, key, need, share in requirements():
        r, lo, _ = rates[key]
        rows.append({"requirement": f">= {need} {label}", "pilot rate /day": round(r, 3),
                     "rate, lower 95 %": round(lo, 3), "share of days": share,
                     "expected": days_expected(r, need, share),
                     "80 % chance": days_with_chance(r, need, share),
                     "cautious": days_with_chance(lo, need, share)})
    table = pd.DataFrame(rows)
    return table, {k: table[k].max() for k in ("expected", "80 % chance", "cautious")}, rates


def from_results(folder):
    """(days, valid spikes, valid spikes that are not irradiance-driven) from a pipeline
    result folder. Days = local days with data in the day window (day_quality.csv)."""
    folder = pathlib.Path(folder)
    ev = pd.read_csv(folder / "events.csv")
    sp = ev[(ev["class"] == "spike") & ev["valid"].astype(bool)]
    natural = sp[sp["origin"] == "natural"] if "origin" in sp else sp
    days = len(pd.read_csv(folder / "day_quality.csv"))
    return days, len(natural), int((natural["cause"] != "irradiance").sum()), len(sp) - len(natural)


def _fmt(v):
    return "not reachable at this rate" if math.isinf(v) else f"{int(v)} days"


def main():
    ap = argparse.ArgumentParser(description="Campaign days needed to test the detector claim")
    ap.add_argument("--results", help="pipeline result folder of the pilot days")
    ap.add_argument("--days", type=float, help="pilot days observed")
    ap.add_argument("--spikes", type=int, help="valid natural spikes on those days")
    ap.add_argument("--non-irradiance", type=int, help="of them, not irradiance-driven (non_irradiance + mixed)")
    a = ap.parse_args()
    if a.results:
        days, spikes, non, controlled = from_results(a.results)
        print(f"from {a.results}: {days} days, {spikes} natural valid spikes, {non} of them not "
              f"irradiance-driven ({controlled} controlled-test spikes left out of the rate)")
    elif None not in (a.days, a.spikes, a.non_irradiance):
        days, spikes, non = a.days, a.spikes, a.non_irradiance
    else:
        ap.error("give --results, or all of --days, --spikes and --non-irradiance")
    if days <= 0 or non > spikes:
        ap.error("days must be positive and --non-irradiance cannot exceed --spikes")
    table, total, _ = estimate(days, spikes, non)
    show = table.copy()
    for c in ("expected", "80 % chance", "cautious"):
        show[c] = show[c].map(_fmt)
    print(show.to_string(index=False))
    print(f"\ncampaign length (the largest of the three requirements):\n"
          f"  expected counts reach the limits : {_fmt(total['expected'])}\n"
          f"  each limit reached with 80 % chance: {_fmt(total['80 % chance'])}\n"
          f"  cautious (lower 95 % pilot rates)  : {_fmt(total['cautious'])}")
    if days < 7:
        print(f"\nnote: only {days:g} pilot days. The rates are very uncertain; repeat after more days.")
    print("Poisson model with a constant rate [ASSUMPTION]: weather runs and the season make the "
          "real scatter larger. Controlled tests are not in the rate and can fill a shortfall.")


if __name__ == "__main__":
    main()
