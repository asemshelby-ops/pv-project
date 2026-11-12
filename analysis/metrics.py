"""Event-level scoring of a detector against the reference spikes (docs/paper/03 sec. 6).

  * alarm inside [onset, peak + grace] of a valid spike -> the first one is a TP and sets
    the delay; later ones in the same spike are ignored;
  * alarm inside a minor rise, an invalid spike or an excluded window -> neutral;
  * any other alarm -> false alarm;
  * a valid spike with no alarm -> missed.
Per cause c: recall = TP_c / N_c; precision_c = TP_c / (TP_c + FP) with ALL false alarms,
because a false alarm has no cause. Intervals: bootstrap over whole local days.

Rates use the VALID observation time (docs/paper/03 sec. 4): per scored day, the hours in
which the detector had data and no excluded window was open (`obs_h`). "False alarms per
day" is per DAY_H valid hours (the 12 h day window), so a day that lost 40 min to the
cleaning counts as 0.94 day.
"""
import warnings

import numpy as np
import pandas as pd

import config

CAUSES = ["irradiance", "non_irradiance", "mixed"]
DAY_H = (pd.Timedelta(config.DAY_END) - pd.Timedelta(config.DAY_START)).total_seconds() / 3600
GRACE = np.timedelta64(config.MATCH_GRACE_MIN * 60, "s")


def _f1(tp, fn, fp):
    p = tp / (tp + fp) if tp + fp else np.nan
    r = tp / (tp + fn) if tp + fn else np.nan
    f = 2 * p * r / (p + r) if tp else 0.0
    return p, r, f


def score(ts, alarms, events, excluded=None, temp=None):
    """ts: local timestamps of the scored rows; alarms: bool per row; temp: optional
    smoothed module temperature per row (for the share of the rise left at the alarm).
    Returns (matches, false_alarm_times, neutral_alarm_count)."""
    ts = pd.to_datetime(pd.Series(ts)).to_numpy()
    t_al = ts[np.asarray(alarms, dtype=bool)]
    spike = (events["class"] == "spike") & events["valid"]
    sp = events[spike].reset_index(drop=True)
    s0 = pd.to_datetime(sp["start"]).to_numpy()
    s1 = pd.to_datetime(sp["end"]).to_numpy()
    other = events[~spike]
    n0 = list(pd.to_datetime(other["start"]).to_numpy())
    n1 = list(pd.to_datetime(other["end"]).to_numpy())
    if excluded is not None and len(excluded):
        n0 += list(pd.to_datetime(excluded["start"]).to_numpy())
        n1 += list(pd.to_datetime(excluded["end"]).to_numpy())
    n0, n1 = np.array(n0, dtype="datetime64[ns]"), np.array(n1, dtype="datetime64[ns]")

    first, false_alarms, neutral = {}, [], 0
    for t in t_al:
        k = np.flatnonzero((s0 <= t) & (t <= s1 + GRACE))
        if len(k):
            first.setdefault(int(k[0]), t)
        elif len(n0) and np.any((n0 <= t) & (t <= n1)):
            neutral += 1
        else:
            false_alarms.append(t)

    m = sp[["event_id", "date_local", "cause", "origin", "size_C"]].copy()
    m["detected"] = [k in first for k in range(len(sp))]
    t_first = np.array([first.get(k, np.datetime64("NaT")) for k in range(len(sp))],
                       dtype="datetime64[ns]")
    sec = np.timedelta64(1, "s")
    m["delay_s"] = (t_first - s0) / sec
    m["lead_s"] = (pd.to_datetime(sp["t_theta"]).to_numpy() - t_first) / sec
    m["length_s"] = (s1 - s0) / sec
    m["remaining"] = np.nan
    if temp is not None and len(sp):
        temp = np.asarray(temp, dtype=float)
        i_pk = np.clip(np.searchsorted(ts, s1), 0, len(ts) - 1)
        for k, t in first.items():
            i_al = min(int(np.searchsorted(ts, t)), len(ts) - 1)
            m.loc[k, "remaining"] = (temp[i_pk[k]] - temp[i_al]) / sp.at[k, "size_C"]
    return m, np.array(false_alarms, dtype="datetime64[ns]"), neutral


def observation_hours(ts, excluded=None):
    """Valid observation time per local day, in hours: scored rows that are not inside an
    excluded window, times the sampling period. Rows the detector never saw (missing
    sensor, gap) are not in `ts`, so they are left out as well. Series indexed by date."""
    ts = pd.to_datetime(pd.Series(ts)).reset_index(drop=True)
    keep = np.ones(len(ts), dtype=bool)
    if excluded is not None and len(excluded):
        t = ts.to_numpy()
        for a, b in zip(pd.to_datetime(excluded["start"]).to_numpy(),
                        pd.to_datetime(excluded["end"]).to_numpy()):
            keep &= ~((t >= a) & (t <= b))
    return (ts[keep].dt.strftime("%Y-%m-%d").value_counts() * config.DT_S / 3600.0).sort_index()


def per_day(matches, false_alarms, days, obs_h=None):
    """One row per scored local day: spikes and detections per cause, false alarms, and
    the valid observation hours (`obs_h` from observation_hours; the whole day window if
    not given)."""
    d = pd.DataFrame(0, index=pd.Index(days, name="date"),
                     columns=[f"{k}_{c}" for c in CAUSES for k in ("n", "tp")] + ["fp"])
    d["obs_h"] = DAY_H if obs_h is None else obs_h.reindex(d.index, fill_value=0.0).to_numpy()
    for c in CAUSES:
        mc = matches[matches["cause"] == c]
        d[f"n_{c}"] = mc.groupby("date_local").size().reindex(d.index, fill_value=0)
        d[f"tp_{c}"] = mc[mc["detected"]].groupby("date_local").size().reindex(d.index, fill_value=0)
    fa = pd.Series(pd.to_datetime(false_alarms).strftime("%Y-%m-%d")).value_counts()
    d["fp"] = fa.reindex(d.index, fill_value=0).to_numpy()
    return d


def summarise(day_table, matches):
    """All reported numbers from the per-day table and the per-spike matches."""
    hours = float(day_table["obs_h"].sum())
    n = sum(day_table[f"n_{c}"].sum() for c in CAUSES)
    tp = sum(day_table[f"tp_{c}"].sum() for c in CAUSES)
    fp = int(day_table["fp"].sum())
    p, r, f = _f1(tp, n - tp, fp)
    det = matches[matches["detected"]]
    out = {"spikes": int(n), "TP": int(tp), "FN": int(n - tp), "FP": fp,
           "precision": p, "recall": r, "F1": f, "obs_hours": hours,
           "FA_per_day": fp / (hours / DAY_H) if hours else np.nan,
           "FA_per_100h": 100.0 * fp / hours if hours else np.nan}
    for c in CAUSES:
        nc, tc = int(day_table[f"n_{c}"].sum()), int(day_table[f"tp_{c}"].sum())
        _, rc, fc = _f1(tc, nc - tc, fp)
        dc = det[det["cause"] == c]
        out.update({f"n_{c}": nc, f"recall_{c}": rc, f"F1_{c}": fc,
                    f"delay_median_min_{c}": dc["delay_s"].median() / 60 if len(dc) else np.nan,
                    f"lead_median_min_{c}": dc["lead_s"].median() / 60 if len(dc) else np.nan})
    q = det["delay_s"].quantile([0.25, 0.5, 0.75, 0.9]) / 60 if len(det) else [np.nan] * 4
    out.update(dict(zip(["delay_q25_min", "delay_median_min", "delay_q75_min", "delay_p90_min"], q)))
    out["lead_median_min"] = det["lead_s"].median() / 60 if len(det) else np.nan
    out["early_share"] = float((det["lead_s"] > 0).mean()) if len(det) else np.nan
    out["remaining_median"] = det["remaining"].median() if len(det) else np.nan
    rate = (tp + fp) / (hours * 3600.0) if hours else np.nan          # alarms per second
    out["chance_recall"] = float(np.mean(1 - np.exp(-rate * matches["length_s"]))) if len(matches) else np.nan
    return out


def _resample(day_table, matches, idx):
    days = day_table.index[idx]
    by_day = {d: g for d, g in matches.groupby("date_local")}
    parts = [by_day[d] for d in days if d in by_day]
    m = pd.concat(parts) if parts else matches.iloc[:0]
    return summarise(day_table.iloc[idx], m)


def bootstrap(day_table, matches, keys=("F1", "recall", "precision", "FA_per_day",
                                        "delay_median_min"), n=2000, seed=0):
    """95 % percentile intervals from resampling whole days. Returns {key: (lo, hi)}."""
    rng = np.random.default_rng(seed)
    draws = {k: [] for k in keys}
    for _ in range(n):
        s = _resample(day_table, matches, rng.integers(0, len(day_table), len(day_table)))
        for k in keys:
            draws[k].append(s[k])
    return {k: tuple(np.nanpercentile(v, [2.5, 97.5])) for k, v in draws.items()}


def bootstrap_difference(a, b, keys=("F1", "delay_median_min", "FA_per_day"), n=2000, seed=0):
    """Paired: detectors a and b, each (day_table, matches), on the SAME resampled days.
    Returns {key: (mean difference a - b, lo, hi)}."""
    rng = np.random.default_rng(seed)
    diff = {k: [] for k in keys}
    for _ in range(n):
        idx = rng.integers(0, len(a[0]), len(a[0]))
        sa, sb = _resample(*a, idx), _resample(*b, idx)
        for k in keys:
            diff[k].append(sa[k] - sb[k])
    with warnings.catch_warnings():      # a key with no data in any resample gives NaN
        warnings.simplefilter("ignore", RuntimeWarning)
        return {k: (float(np.nanmean(v)), *np.nanpercentile(v, [2.5, 97.5]))
                for k, v in diff.items()}
