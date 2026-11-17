"""Minimum detectable effect of the spray trial: how small a difference between the
spray arm and the no-spray arm can a given number of events show?

    python -m analysis.mde --per-arm 10 20 40
        spread from a SYNTHETIC dry-run campaign generated on the spot (no spray effect in
        it at all: the two arms differ only by chance, which is exactly the spread wanted)
    python -m analysis.mde --per-arm 10 20 40 --results <folder with phase_b_events.csv>
        spread from a real run, e.g. weeks of dry run on the rig (PHASE_B_DRY_RUN 1)
    python -m analysis.mde --per-arm 20 --sd 0.011 --quantity gain_rel
        spread typed in
    python -m analysis.mde --effect 0.004 --quantity gain_rel
        the other way round: events per arm needed for a given effect

Model: two arms of n events each, the outcome scatters with the same standard deviation
sd in both, two-sided t-test at level config.MDE_ALPHA. The minimum detectable effect is
the true difference that this test finds with probability config.MDE_POWER (non-central
t distribution). A close approximation shows what drives it:

    MDE ~ (t[1 - alpha/2] + t[power]) * sd * sqrt(2 / n)        (2n - 2 degrees of freedom)

so halving it needs four times the events. What this cannot know: the real spread (the
synthetic one is the generator's, not the module's), and whether the outcome scatters the
same in both arms. Repeat it with the spread of real dry-run events before fixing the
length of the trial. NOT ADOPTED: a proposal (docs/paper/03 sec. 12).
"""
import argparse
import math
import pathlib
import tempfile

import numpy as np
import pandas as pd
from scipy import optimize, stats

import config
from analysis import phase_b

QUANTITIES = {   # column of the event table -> (unit, what it is)
    "gain_rel": ("fraction", "module energy per irradiation over the window, relative to the "
                             "event's own baseline"),
    "yield_W_per_kWm2": ("Wh per kWh/m2", "module energy per irradiation over the window"),
    "dT_after_spray_C": ("degC", "module temperature 30 s after the longest spray minus at the alarm"),
}


def power(effect, sd, n, alpha=config.MDE_ALPHA):
    """Chance that a two-sided two-sample t-test with n events per arm finds a true
    difference `effect` when the outcome scatters with standard deviation `sd`.

    The test statistic follows a non-central t distribution with 2n - 2 degrees of freedom
    and non-centrality effect / (sd * sqrt(2/n)); its square follows a non-central F
    distribution, which is the form computed here (scipy's non-central t gives NaN at
    large non-centralities; tests/test_mde.py checks this form against a direct numerical
    integration)."""
    if n < 2 or not sd > 0:
        return math.nan
    dof = 2 * n - 2
    t_crit = stats.t.ppf(1 - alpha / 2, dof)
    ncp = abs(effect) / (sd * math.sqrt(2.0 / n))
    if ncp == 0.0:
        return float(alpha)     # no effect: the test "finds" one at its false-alarm rate
    return float(stats.ncf.sf(t_crit * t_crit, 1, dof, ncp * ncp))


def mde(sd, n, alpha=config.MDE_ALPHA, target=config.MDE_POWER):
    """Smallest true difference between the arms that n events per arm detect with
    probability `target`."""
    if n < 2 or not sd > 0:
        return math.nan
    hi = sd * math.sqrt(2.0 / n)
    while power(hi, sd, n, alpha) < target:     # walk out until the power is reached
        hi *= 2.0
    return float(optimize.brentq(lambda d: power(d, sd, n, alpha) - target, 0.0, hi, xtol=1e-12 * sd))


def mde_approx(sd, n, alpha=config.MDE_ALPHA, target=config.MDE_POWER):
    """The textbook formula of the module's header: within 0.2 % of mde() from 5 events
    per arm on, 5 % too small at 2 per arm."""
    dof = 2 * n - 2
    return float((stats.t.ppf(1 - alpha / 2, dof) + stats.t.ppf(target, dof)) * sd * math.sqrt(2.0 / n))


def events_needed(sd, effect, alpha=config.MDE_ALPHA, target=config.MDE_POWER, n_max=100000):
    """Events per arm at which an effect of the given size is detected with probability
    `target`; math.inf if more than n_max are needed."""
    if not effect:
        return math.inf
    n = 2
    while n <= n_max and power(effect, sd, n, alpha) < target:
        n = max(n + 1, int(n * 1.05))
    if n > n_max:
        return math.inf
    while n > 2 and power(effect, sd, n - 1, alpha) >= target:
        n -= 1
    return n


def spread(events):
    """Standard deviation of each outcome among comparable events: within each arm, around
    that arm's own mean, pooled over the arms (so a real spray effect does not inflate it).
    Uses valid events outside the held-out week; dry-run events count too (their arms are
    drawn but nothing is sprayed). Returns {quantity: (sd, events used)}."""
    e = events[events["valid"] & events["assign"].isin([0, 1])]
    if "heldout" in e:
        e = e[~e["heldout"].astype(bool)]
    out = {}
    for q in QUANTITIES:
        parts = [e.loc[e["assign"] == a, q].dropna() for a in (0, 1)]
        dof = sum(len(p) - 1 for p in parts if len(p) > 1)
        ss = sum(float(((p - p.mean()) ** 2).sum()) for p in parts if len(p) > 1)
        out[q] = (math.sqrt(ss / dof) if dof > 0 else math.nan, int(sum(len(p) for p in parts)))
    return out


def synthetic_events(n_days=40, seed=config.EXPORT_SEED):
    """Event table of a SYNTHETIC dry-run campaign: hard synthetic days (data/synth.py),
    events and arms as the firmware draws them, no spray. Also the days it covers."""
    from analysis import fake_phase_b, ingest
    from data import synth
    days, _ = synth.generate(seed, n_days=n_days, hard=True)
    with tempfile.TemporaryDirectory() as tmp:
        fake_phase_b.build(days, tmp, dry_run=True, sweeps=False)
        df = ingest.read_logger(tmp)
        events = phase_b.event_table(df, ingest.read_spray_log(pathlib.Path(tmp) / "spray_log.csv"))
    return events, n_days


def table(sd, per_arm, quantity, reference_wh=None):
    """One row per number of events per arm: the minimum detectable effect in the
    quantity's own unit and, for the energy outcomes, as energy of one event window."""
    unit = QUANTITIES[quantity][0]
    rows = []
    for n in per_arm:
        d = mde(sd, n)
        row = {"events per arm": n, "events in all": 2 * n, f"MDE, {unit}": d,
               "MDE / sd": d / sd if sd else math.nan, "approx. formula": mde_approx(sd, n)}
        if quantity == "gain_rel":
            row["MDE, % of the window's energy"] = 100.0 * d
            if reference_wh:
                row["MDE, Wh per event"] = d * reference_wh
            # the mean cooling over the window that gives this gain, with the ILLUSTRATIVE
            # gamma of config.py (NOT this module's value)
            row["as mean cooling over the window, degC [EST]"] = d / abs(config.GAMMA_P)
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description="Minimum detectable effect of the spray trial")
    ap.add_argument("--per-arm", type=int, nargs="+", default=[5, 10, 20, 40, 80, 160])
    ap.add_argument("--quantity", choices=list(QUANTITIES), default="gain_rel")
    ap.add_argument("--results", help="folder with phase_b_events.csv of a run")
    ap.add_argument("--sd", type=float, help="spread of the quantity among events, typed in")
    ap.add_argument("--effect", type=float, help="print the events per arm needed for this effect")
    ap.add_argument("--days", type=int, default=40, help="synthetic days, if no --results / --sd")
    ap.add_argument("--seed", type=int, default=config.EXPORT_SEED)
    a = ap.parse_args()
    reference_wh, source, per_day = None, "typed in", None
    if a.sd is not None:
        sd, n_used = a.sd, 0
    else:
        if a.results:
            events = pd.read_csv(pathlib.Path(a.results) / "phase_b_events.csv")
            source = f"events of {a.results}"
        else:
            events, n_days = synthetic_events(a.days, a.seed)
            source = (f"SYNTHETIC dry-run campaign, {n_days} days, seed {a.seed}: the generator's "
                      "spread, not the module's")
            per_day = len(events) / n_days
        sd, n_used = spread(events)[a.quantity]
        ok = events[events["valid"]]
        reference_wh = float((ok["yield0_W_per_kWm2"] * ok["H_kWh_m2"]).mean())
    unit, what = QUANTITIES[a.quantity]
    print(f"quantity: {a.quantity} ({what}), unit: {unit}")
    print(f"spread (sd among comparable events): {sd:.5g} from {n_used} events; source: {source}")
    if per_day is not None:
        print(f"events per day in that campaign: {per_day:.2f} (the generator's rate, not a forecast)")
    print(f"test: two-sided, alpha {config.MDE_ALPHA:g}, power {config.MDE_POWER:g}\n")
    print(table(sd, a.per_arm, a.quantity, reference_wh).to_string(index=False, float_format="%.4g"))
    if a.effect is not None:
        n = events_needed(sd, a.effect)
        print(f"\nan effect of {a.effect:g} {unit} needs "
              + ("more events per arm than can be counted here" if math.isinf(n) else
                 f"{n} events per arm ({2 * n} in all)"))
    if a.sd is None and not a.results:
        print("\nSYNTHETIC: these numbers show how the calculation behaves. Repeat it with the "
              "spread of real dry-run events before fixing the length of the trial.")


if __name__ == "__main__":
    main()
