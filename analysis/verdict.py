"""Verdict on the detector claim against the criteria fixed in advance (docs/paper/03
sec. 10; the paper's "Acceptance criteria fixed in advance").

The claim: NASIM-GBT combines an early alarm, high recall and almost no false alarms.
Tested on the TEST days at the primary false-alarm budget, every detector at the setting
chosen on the validation days (analysis/detection.py, `matched`). This module only reads
that table and the spike counts: it decides nothing by itself and has no tunable value;
the limits are the PREREG_* constants of config.py.

  precondition  >= PREREG_MIN_TRAIN_SPIKES training spikes, >= PREREG_MIN_TEST_SPIKES test
                spikes, >= PREREG_MIN_TEST_NON_IRR of them not irradiance-driven
  C1  NASIM false alarms per day <= PREREG_MAX_FA_PER_DAY
  C2  NASIM recall >= PREREG_MIN_RECALL
  C3a median lead over the 3 degC crossing > 0
  C3b median share of the rise remaining at the alarm >= PREREG_MIN_REMAINING
  C3c NASIM minus threshold rule, median delay: 95 % interval entirely below 0
  holds        C1, C2, C3a, C3b, C3c
  distinctive  holds, and no baseline "matches": meets C1 and C2 with a delay that is not
               detectably longer than NASIM's (interval of NASIM minus baseline reaches 0)
The four sentences are the only wordings the paper may use.
"""
import math

import config

NASIM, THRESHOLD = "NASIM-GBT", "Threshold (tuned)"
NAMES = {THRESHOLD: "the threshold rule", "CUSUM": "CUSUM", "Irradiance step": "the irradiance-step rule"}
SENTENCES = {
    "holds_distinctive": "At {budget:g} false alarms per day the tree model was the only one of "
                         "the four detectors that met all three conditions set in advance.",
    "holds_not_distinctive": "The tree model met the three conditions set in advance, and so did "
                             "{baselines}; for this module the simpler detector is sufficient.",
    "does_not_hold": "The tree model did not meet the conditions set in advance ({failed}); "
                     "{best} is the better choice here.",
    "not_tested": "The number of spikes was too small to test the claim.",
}


def _ok(value, test):
    """A condition with a missing number is not met."""
    return bool(value is not None and not (isinstance(value, float) and math.isnan(value))
                and test(value))


def _join(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def evaluate(matched, train_spikes, budget=None):
    """matched: the table of detection.run (one row per budget and detector).
    train_spikes: number of valid spikes on the training days.
    Returns a dict: outcome, sentence, conditions [(name, passed, text)], baselines
    [(detector, matches, text)], precondition (passed, text)."""
    budget = config.PREREG_BUDGET_FA_PER_DAY if budget is None else budget
    rows = matched[(matched["budget_FA_per_day"] - budget).abs() < 1e-9].set_index("detector")
    if NASIM not in rows.index:
        raise ValueError(f"the primary budget {budget} is not in config.MATCH_FA_PER_DAY")
    get = lambda name, col: rows.at[name, col] if col in rows.columns else float("nan")  # noqa: E731
    has_setting = lambda name: isinstance(get(name, "setting"), str)  # noqa: E731

    out = {"budget": budget, "conditions": [], "baselines": []}
    n_test = get(NASIM, "test_spikes") if has_setting(NASIM) else float("nan")
    n_non = (get(NASIM, "test_n_non_irradiance") + get(NASIM, "test_n_mixed")
             if has_setting(NASIM) else float("nan"))
    pre = (train_spikes >= config.PREREG_MIN_TRAIN_SPIKES
           and _ok(n_test, lambda v: v >= config.PREREG_MIN_TEST_SPIKES)
           and _ok(n_non, lambda v: v >= config.PREREG_MIN_TEST_NON_IRR))
    out["precondition"] = (bool(pre), (
        f"training spikes {train_spikes} (need >= {config.PREREG_MIN_TRAIN_SPIKES}); test spikes "
        f"{n_test:.0f} (need >= {config.PREREG_MIN_TEST_SPIKES}); of them not irradiance-driven "
        f"{n_non:.0f} (need >= {config.PREREG_MIN_TEST_NON_IRR})"
        if has_setting(NASIM) else
        f"training spikes {train_spikes}; no NASIM setting qualified on the validation days"))
    if not pre:
        out.update(outcome="not_tested", sentence=SENTENCES["not_tested"])
        return out

    fa, rec = get(NASIM, "test_FA_per_day"), get(NASIM, "test_recall")
    lead, left = get(NASIM, "test_lead_median_min"), get(NASIM, "test_remaining_median")
    hi = get(THRESHOLD, "nasim_minus_delay_hi") if has_setting(THRESHOLD) else float("nan")
    d, lo = get(THRESHOLD, "nasim_minus_delay"), get(THRESHOLD, "nasim_minus_delay_lo")
    out["conditions"] = [
        ("C1 false alarms", _ok(fa, lambda v: v <= config.PREREG_MAX_FA_PER_DAY + 1e-12),
         f"{fa:.2f} per day (limit <= {config.PREREG_MAX_FA_PER_DAY:g})"),
        ("C2 recall", _ok(rec, lambda v: v >= config.PREREG_MIN_RECALL - 1e-12),
         f"{rec:.2f} (limit >= {config.PREREG_MIN_RECALL:g})"),
        ("C3a lead over the 3 degC crossing", _ok(lead, lambda v: v > 0),
         f"{lead:+.1f} min (limit > 0)"),
        ("C3b rise remaining at the alarm", _ok(left, lambda v: v >= config.PREREG_MIN_REMAINING - 1e-12),
         f"{left:.0%} (limit >= {config.PREREG_MIN_REMAINING:.0%})"),
        ("C3c earlier than the threshold rule", _ok(hi, lambda v: v < 0),
         f"NASIM minus threshold {d:+.1f} min ({lo:+.1f} to {hi:+.1f}); the interval must lie below 0"
         if has_setting(THRESHOLD) else "the threshold rule has no setting at this budget"),
    ]
    holds = all(p for _, p, _ in out["conditions"])

    score = {}
    for name in [n for n in rows.index if n != NASIM]:
        if not has_setting(name):
            out["baselines"].append((name, False, "no setting qualified on the validation days"))
            score[name] = -1
            continue
        b_fa, b_rec = get(name, "test_FA_per_day"), get(name, "test_recall")
        b_lead, b_left = get(name, "test_lead_median_min"), get(name, "test_remaining_median")
        b_hi = get(name, "nasim_minus_delay_hi")
        c1 = _ok(b_fa, lambda v: v <= config.PREREG_MAX_FA_PER_DAY + 1e-12)
        c2 = _ok(b_rec, lambda v: v >= config.PREREG_MIN_RECALL - 1e-12)
        as_early = _ok(b_hi, lambda v: v >= 0)
        score[name] = (c1 + c2 + _ok(b_lead, lambda v: v > 0)
                       + _ok(b_left, lambda v: v >= config.PREREG_MIN_REMAINING - 1e-12))
        out["baselines"].append((name, bool(c1 and c2 and as_early), (
            f"false alarms {b_fa:.2f} per day ({'ok' if c1 else 'fails C1'}); recall {b_rec:.2f} "
            f"({'ok' if c2 else 'fails C2'}); NASIM minus this, delay "
            f"{get(name, 'nasim_minus_delay'):+.1f} min ({get(name, 'nasim_minus_delay_lo'):+.1f} to "
            f"{b_hi:+.1f}) -> {'as early as NASIM' if as_early else 'detectably later'}; "
            f"recall {get(name, 'nasim_minus_recall'):+.2f} "
            f"({get(name, 'nasim_minus_recall_lo'):+.2f} to {get(name, 'nasim_minus_recall_hi'):+.2f})")))
    matching = [NAMES.get(n, n) for n, m, _ in out["baselines"] if m]
    if holds and not matching:
        out.update(outcome="holds_distinctive",
                   sentence=SENTENCES["holds_distinctive"].format(budget=budget))
    elif holds:
        out.update(outcome="holds_not_distinctive",
                   sentence=SENTENCES["holds_not_distinctive"].format(baselines=_join(matching)))
    else:
        failed = _join(n.split(" ")[0] for n, p, _ in out["conditions"] if not p)
        top = max(score.values())
        best = _join(NAMES.get(n, n) for n, s in score.items() if s == top)
        out.update(outcome="does_not_hold",
                   sentence=SENTENCES["does_not_hold"].format(failed=failed, best=best))
    return out


def markdown(v, synthetic=False):
    """The verdict as lines for summary.md."""
    lines = ["## Verdict on the detector claim (criteria fixed in advance, docs/paper/03 sec. 10)", ""]
    if synthetic:
        lines += ["SYNTHETIC DATA: this shows that the verdict is computed, not that the claim is true.", ""]
    lines += [f"Primary budget: {v['budget']:g} false alarms per day; settings chosen on the "
              f"validation days, numbers from the test days.", "",
              f"- Precondition: **{'met' if v['precondition'][0] else 'NOT met'}** - {v['precondition'][1]}"]
    lines += [f"- {name}: **{'PASS' if ok else 'FAIL'}** - {text}" for name, ok, text in v["conditions"]]
    if v["baselines"]:
        lines += ["", "Baselines at the same budget (a baseline matches when it meets C1 and C2 "
                  "and is not detectably later than NASIM):", ""]
        lines += [f"- {name}: **{'matches' if m else 'does not match'}** - {text}"
                  for name, m, text in v["baselines"]]
    lines += ["", f"Outcome: **{v['outcome'].replace('_', ' ')}**.", "",
              f"Sentence for the paper: \"{v['sentence']}\"", ""]
    return lines
