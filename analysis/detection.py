"""Train, tune and test the three detectors of the paper on one ingested data set.

  (a) Threshold   rise over 10 min >= theta_a, no irradiance gate
  (b) CUSUM       on the temperature increment (detect/cusum_inc.py)
  (c) NASIM-GBT   hybrid, no wind (detect/gbt.py); positives = first 10 min of every spike

Split: chronological, whole local days (config.PAPER_SPLIT). Every setting is chosen on
the validation days only (best event F1, ties -> fewer false alarms per day); the test
days are scored once. The array versions of the alarm rules below are the same logic as
the streaming classes that the C code mirrors (tests/test_analysis.py checks equality).
"""
import itertools

import numpy as np
import pandas as pd

import config
from analysis import events as ev_mod
from analysis import metrics
from data.schema import BACK_SENSORS
from detect import gbt
from detect.features import N10, feature_names

SILENCE_N = gbt.SILENCE_N
THR_GRID = [round(x, 1) for x in np.arange(1.0, 4.01, 0.1)]
KAPPA_GRID = [0.005, 0.01, 0.02, 0.03, 0.05, 0.08]
H_GRID = [0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0]
P_GRID = [round(x, 2) for x in np.arange(0.05, 0.96, 0.05)] + [0.97, 0.98, 0.99, 0.995]
NAMES = feature_names(False, True)


def split_days(dates, fractions=config.PAPER_SPLIT):
    dates = sorted(dates)
    n_tr = int(round(fractions[0] * len(dates)))
    n_va = int(round(fractions[1] * len(dates)))
    return {"train": dates[:n_tr], "val": dates[n_tr:n_tr + n_va], "test": dates[n_tr + n_va:]}


def filtered_temperature(frame):
    """Causal 3-sample median of the mean back-sheet temperature, restarted per segment."""
    t = frame[BACK_SENSORS].mean(axis=1)
    out = t.groupby(frame["segment"]).transform(lambda s: s.rolling(3).median())
    return out.to_numpy()


def alarm_rule(score, thr, seg, persist=1):
    """Alarm when score >= thr for `persist` samples in a row, then 10 min of silence."""
    out = np.zeros(len(score), dtype=bool)
    quiet = run = 0
    for i in range(len(score)):
        if i == 0 or seg[i] != seg[i - 1]:
            quiet = run = 0
        if quiet > 0:
            quiet -= 1
        if score[i] != score[i]:
            run = 0
            continue
        run = run + 1 if score[i] >= thr else 0
        if quiet == 0 and run >= persist:
            out[i], quiet, run = True, SILENCE_N, 0
    return out


def cusum_inc_alarms(t, seg, kappa, h):
    """Array form of detect.cusum_inc.CusumIncDetector."""
    out = np.zeros(len(t), dtype=bool)
    prev, S, n, quiet = None, 0.0, 0, 0
    for i in range(len(t)):
        if i == 0 or seg[i] != seg[i - 1]:
            prev, S, n, quiet = None, 0.0, 0, 0
        if t[i] != t[i]:
            continue
        if prev is None:
            prev = t[i]
            continue
        S = max(0.0, S + (t[i] - prev) - kappa)
        prev = t[i]
        n += 1
        if quiet > 0:
            quiet -= 1
        if n >= N10 and quiet == 0 and S >= h:
            out[i], S, quiet = True, 0.0, SILENCE_N
    return out


def training_events(events, excluded):
    """Event table for detect.gbt.sample_targets: valid spikes are positives; everything
    else that is not plain background is neutral."""
    e = events[["start", "end", "class"]].copy()
    e.loc[~events["valid"], "class"] = "minor"
    if excluded is not None and len(excluded):
        x = pd.DataFrame({"start": excluded["start"].dt.strftime(ev_mod.FMT),
                          "end": excluded["end"].dt.strftime(ev_mod.FMT), "class": "excluded"})
        e = pd.concat([e, x], ignore_index=True)
    return e


class Scorer:
    """Scores alarm arrays on one part (validation or test) of the frame."""

    def __init__(self, frame, events, excluded, dates):
        self.mask = frame["date"].isin(dates).to_numpy()
        self.ts = frame.loc[self.mask, "t_local"].to_numpy()
        self.temp = ev_mod.smooth(frame.loc[self.mask, BACK_SENSORS].mean(axis=1).to_numpy())
        self.events = events[events["date_local"].isin(dates)].reset_index(drop=True)
        self.excluded, self.dates = excluded, sorted(dates)

    def __call__(self, alarms):
        m, fa, neutral = metrics.score(self.ts, alarms[self.mask], self.events,
                                       self.excluded, self.temp)
        table = metrics.per_day(m, fa, self.dates)
        return {**metrics.summarise(table, m), "alarms_neutral": neutral}, table, m


def _best(candidates, scorer):
    """candidates: iterable of (setting, alarms). Best validation F1, then fewest false alarms."""
    best = None
    for setting, alarms in candidates:
        s = scorer(alarms)[0]
        key = (s["F1"], -s["FA_per_day"])
        if best is None or key > best[0]:
            best = (key, setting)
    return best[1]


def run(frame, events, excluded=None, seed=0, n_boot=2000, log=print):
    """Returns a dict: results (one row per detector, test days), tuned settings, alarms,
    per-detector (day table, matches) for the test days, the trained model and the split."""
    parts = split_days(frame["date"].unique())
    seg = frame["segment"].to_numpy()
    val = Scorer(frame, events, excluded, parts["val"])
    test = Scorer(frame, events, excluded, parts["test"])

    log("features ...")
    X = gbt.build_features(frame, use_wind=False, hybrid=True)
    rise = X[:, NAMES.index("rise_10m")]
    t_f = filtered_temperature(frame)
    in_val = val.mask

    alarms, tuned = {}, {}
    alarms["Threshold (fixed 3.0)"] =alarm_rule(rise, config.SPIKE_THETA_C, seg)
    thr = _best(((x, alarm_rule(np.where(in_val, rise, np.nan), x, seg)) for x in THR_GRID), val)
    tuned["threshold_rise_C"] = thr
    alarms["Threshold (tuned)"] = alarm_rule(rise, thr, seg)

    log("CUSUM grid ...")
    t_val = np.where(in_val, t_f, np.nan)
    kappa, h = _best((((k, hh), cusum_inc_alarms(t_val, seg, k, hh))
                      for k, hh in itertools.product(KAPPA_GRID, H_GRID)), val)
    tuned["cusum_kappa_C_per_sample"], tuned["cusum_h_C"] = kappa, h
    alarms["CUSUM"] = cusum_inc_alarms(t_f, seg, kappa, h)

    log("NASIM-GBT training ...")
    train_mask = frame["date"].isin(parts["train"]).to_numpy()
    y, use = gbt.sample_targets(frame[train_mask], training_events(
        events[events["date_local"].isin(parts["train"])], excluded))
    model = gbt.train(X[train_mask], y, use, seed, names=NAMES,
                      weighting=config.GBT_CLASS_WEIGHT)
    ok = ~np.isnan(X).any(axis=1)
    p = np.full(len(frame), np.nan)
    p[ok] = model.booster_.predict(X[ok])
    p_thr = _best(((x, alarm_rule(np.where(in_val, p, np.nan), x, seg, config.GBT_PERSIST))
                   for x in P_GRID), val)
    tuned["nasim_p_threshold"] = p_thr
    alarms["NASIM-GBT"] = alarm_rule(p, p_thr, seg, config.GBT_PERSIST)

    rows, detail = [], {}
    for name, al in alarms.items():
        s, table, m = test(al)
        ci = metrics.bootstrap(table, m, n=n_boot, seed=seed)
        rows.append({"detector": name, **s,
                     **{f"{k}_lo": v[0] for k, v in ci.items()},
                     **{f"{k}_hi": v[1] for k, v in ci.items()}})
        detail[name] = (table, m)
        log(f"  {name:32s} F1={s['F1']:.2f}  FA/day={s['FA_per_day']:.2f}  "
            f"delay={s['delay_median_min']:.1f} min")
    diffs = {b: metrics.bootstrap_difference(detail["NASIM-GBT"], detail[b], n=n_boot, seed=seed)
             for b in ("Threshold (tuned)", "CUSUM")}
    counts = {k: int(((events["class"] == "spike") & events["valid"]
                      & events["date_local"].isin(v)).sum()) for k, v in parts.items()}
    return {"results": pd.DataFrame(rows), "tuned": tuned, "alarms": alarms, "detail": detail,
            "differences": diffs, "model": model, "p": p, "X": X, "parts": parts,
            "spikes_per_part": counts}
