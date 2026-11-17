"""Reference labelling of thermal spikes (docs/paper/03, sections 2-4).

Offline and in hindsight: this code sees the whole day, uses centred smoothing, and is
NEVER run on the ESP32. The detectors are causal and are scored against these labels.

A spike: the smoothed module temperature rises by >= SPIKE_THETA_C within
SPIKE_WINDOW_MIN. For each one: onset, threshold-crossing time, peak, size, duration,
largest 2-min rate, the irradiance step, the fraction of the rise that step explains
(first-order thermal lag) and from it the cause label. Minor rises (>= SPIKE_MINOR_C but
below the threshold) are found the same way and are neutral in detector scoring.
"""
import math

import numpy as np
import pandas as pd

import config
from analysis.metrics import LOAD_OFF
from data.schema import BACK_SENSORS
from detect.features import solar_angles

PER_MIN = int(60 / config.DT_S)
W = config.SPIKE_WINDOW_MIN * PER_MIN
HOLD = config.SPIKE_PEAK_HOLD_MIN * PER_MIN
FMT = "%Y-%m-%d %H:%M:%S"
COLUMNS = ["event_id", "date_local", "start", "t_theta", "end", "class", "size_C",
           "duration_s", "max_rate_C_per_min", "G_pre", "G_ev", "dG", "f_G", "dT_air",
           "cause", "origin", "valid", "note"]


def smooth(x, median=3, mean=PER_MIN + 1):
    """Centred 3-sample median, then centred 1-min mean (7 samples at 10 s)."""
    s = pd.Series(np.asarray(x, dtype=float))
    s = s.rolling(median, center=True, min_periods=1).median()
    return s.rolling(mean, center=True, min_periods=1).mean().to_numpy()


def find_rises(ts, theta):
    """Rises of at least `theta` within W samples in the smoothed series `ts`.
    Returns [(i_on, i_theta, i_pk)]; the search restarts at each peak."""
    out, floor, n, i = [], 0, len(ts), 1
    while i < n:
        lo = max(i - W, floor)
        window = ts[lo:i + 1]
        low = window.min()
        if ts[i] - low < theta:
            i += 1
            continue
        i_on = lo + int(np.flatnonzero(window == low)[-1])      # latest minimum
        j = i
        while j < n - 1:                                         # climb to the peak
            ahead = ts[j + 1:j + 1 + HOLD]
            if ahead.max() <= ts[j]:
                break
            j += 1 + int(np.argmax(ahead > ts[j]))
        out.append((i_on, i, j))
        floor, i = j, j + 1
    return out


def cause_from_fraction(f_g, f_non=config.CAUSE_F_NON, f_irr=config.CAUSE_F_IRRADIANCE):
    """Cause label from the fraction of the rise that the irradiance step explains."""
    return "irradiance" if f_g >= f_irr else "non_irradiance" if f_g <= f_non else "mixed"


def cause_sensitivity(events, pairs=config.CAUSE_SENSITIVITY):
    """How the cause counts move with the two limits on f_G (reviewer check, docs/paper/12
    R6). One row per (f_non, f_irr) pair, valid spikes only: counts per cause, how many
    spikes change label against the configured pair, and - where a cause is known
    (`cause_true` on synthetic data, else controlled tests: mesh = irradiance, windbreak =
    not irradiance) - how many known irradiance / non-irradiance spikes get the right
    label, 'mixed', or the opposite one."""
    sp = events[(events["class"] == "spike") & events["valid"]]
    base = sp["f_G"].map(cause_from_fraction)
    if "cause_true" in sp:
        known = sp["cause_true"]
    else:        # a load-off rise has no irradiance step: a known non-irradiance cause
        known = sp["origin"].map({"ctl_mesh": "irradiance", "ctl_windbreak": "non_irradiance",
                                  "ctl_load": "non_irradiance"})
    has = known.isin(["irradiance", "non_irradiance"])
    rows = []
    for f_non, f_irr in pairs:
        lab = sp["f_G"].map(lambda f: cause_from_fraction(f, f_non, f_irr))
        row = {"f_non": f_non, "f_irr": f_irr,
               "configured": bool(abs(f_non - config.CAUSE_F_NON) < 1e-9
                                  and abs(f_irr - config.CAUSE_F_IRRADIANCE) < 1e-9),
               **{c: int((lab == c).sum()) for c in ("irradiance", "non_irradiance", "mixed")},
               "relabelled": int((lab != base).sum()), "known": int(has.sum())}
        if has.any():
            row.update({"known_right": int((lab[has] == known[has]).sum()),
                        "known_mixed": int((lab[has] == "mixed").sum()),
                        "known_opposite": int(((lab[has] != known[has]) & (lab[has] != "mixed")).sum())})
        rows.append(row)
    return pd.DataFrame(rows)


def _describe(g, ts, g1, i_on, i_th, i_pk):
    t = g["t_local"].to_numpy()
    size = float(ts[i_pk] - ts[i_on])
    dur = float((t[i_pk] - t[i_on]) / np.timedelta64(1, "s"))
    two = 2 * PER_MIN
    seg = ts[i_on:i_pk + 1]
    rate = float(np.max(seg[two:] - seg[:-two]) / 2.0) if len(seg) > two else size / max(dur / 60, 1e-9)
    irr = g["irradiance"].to_numpy(dtype=float)
    g_pre = float(g1[max(i_on - W, 0):i_on + PER_MIN + 1].min())
    g_ev = float(irr[i_on:i_pk + 1].mean())
    d_g = g_ev - g_pre
    explained = d_g / config.CAUSE_U_EFF * (1 - math.exp(-dur / (config.CAUSE_TAU_MIN * 60)))
    f_g = explained / size
    cause = cause_from_fraction(f_g)
    ta = g["T_ambient"].to_numpy(dtype=float)
    ts_on = pd.Timestamp(t[i_on])
    cos_z, _ = solar_angles(ts_on.day_of_year, ts_on.hour * 60 + ts_on.minute + ts_on.second / 60)
    return {"date_local": ts_on.strftime("%Y-%m-%d"), "start": ts_on.strftime(FMT),
            "t_theta": pd.Timestamp(t[i_th]).strftime(FMT),
            "end": pd.Timestamp(t[i_pk]).strftime(FMT), "size_C": round(size, 3),
            "duration_s": dur, "max_rate_C_per_min": round(rate, 3),
            "G_pre": round(g_pre, 1), "G_ev": round(g_ev, 1), "dG": round(d_g, 1),
            "f_G": round(f_g, 3), "dT_air": round(float(ta[i_pk] - ta[i_on]), 2),
            "cause": cause, "valid": bool(cos_z >= config.SPIKE_MIN_COS_ZENITH), "note": ""}


def label(frame, excluded=None, tests=None):
    """frame: analysis.ingest.detector_frame(...). excluded / tests: tables from
    ingest.excluded_windows / ingest.controlled_tests (optional).
    Returns the event table (COLUMNS); `start` = onset, `end` = peak, local time."""
    rows = []
    for _, g in frame.groupby("segment", sort=True):
        if len(g) <= W:
            continue
        ts = smooth(g[BACK_SENSORS].mean(axis=1).to_numpy())
        g1 = smooth(g["irradiance"].to_numpy(), median=1)
        spikes = find_rises(ts, config.SPIKE_THETA_C)
        taken = [(a, c) for a, _, c in spikes]
        for a, b, c in spikes:
            rows.append({**_describe(g, ts, g1, a, b, c), "class": "spike"})
        for a, b, c in find_rises(ts, config.SPIKE_MINOR_C):
            if not any(a <= c0 and a0 <= c for a0, c0 in taken):
                rows.append({**_describe(g, ts, g1, a, b, c), "class": "minor"})
    columns = COLUMNS + (["cause_rule"] if config.LOADOFF_CLASS_ENABLED else [])
    ev = pd.DataFrame(rows)
    if ev.empty:
        return pd.DataFrame(columns=columns)
    ev = ev.sort_values("start").reset_index(drop=True)
    ev["origin"] = "natural"
    t_on, t_pk = pd.to_datetime(ev["start"]), pd.to_datetime(ev["end"])
    if tests is not None:
        early = pd.Timedelta(minutes=2)
        for _, tst in tests.iterrows():
            if tst["origin"] == "ctl_mesh":          # the rise follows the removal of the mesh
                hit = (t_on >= tst["off"] - early) & (t_on <= tst["off"] + pd.Timedelta(minutes=10))
            elif tst["origin"] == "ctl_load":
                # Open-circuit test (only with config.LOADOFF_CLASS_ENABLED): the rise REACHES
                # its threshold while the load is off. The onset is not used here: it is the
                # latest minimum of the 10 min before, and a module that was already warming
                # slowly puts it minutes before the load went off [ASSUMPTION, 2026-11-17].
                t_th = pd.to_datetime(ev["t_theta"])
                hit = (t_th >= tst["on"]) & (t_th <= tst["off"] + early)
            else:                                    # the rise happens while the windbreak is up
                hit = (t_on >= tst["on"] - early) & (t_on <= tst["off"])
            ev.loc[hit, "origin"] = tst["origin"]
    if excluded is not None:
        for _, x in excluded.iterrows():
            hit = (t_on <= x["end"]) & (t_pk >= x["start"])
            ev.loc[hit, "valid"] = False
            ev.loc[hit, "note"] = "excluded: " + x["reason"]
    ev.loc[~ev["valid"] & (ev["note"] == ""), "note"] = "sun too low"
    if config.LOADOFF_CLASS_ENABLED:
        # Load-off spikes as their own class: a rise during a controlled open-circuit test
        # (origin ctl_load) gets the cause `load_off`. What the irradiance rule itself said is
        # kept in `cause_rule` (it should say non_irradiance: the sun did not change).
        ev["cause_rule"] = ev["cause"]
        ev.loc[ev["origin"] == "ctl_load", "cause"] = LOAD_OFF
    ev.insert(0, "event_id", np.arange(1, len(ev) + 1))
    return ev[columns]


def attach_truth(events, drivers):
    """Synthetic data only: `cause_true` from the generator's own record of what it
    injected (data/synth.py event table), by overlap with each labelled event."""
    irr = {"irradiance_rise"}
    non = {"spike", "minor_lull", "natural_spike", "natural_minor", "ambient_rise"}
    d0, d1 = pd.to_datetime(drivers["start"]).to_numpy(), pd.to_datetime(drivers["end"]).to_numpy()
    cls = drivers["class"].to_numpy()
    truth = []
    for a, b in zip(pd.to_datetime(events["start"]).to_numpy(),
                    pd.to_datetime(events["end"]).to_numpy()):
        hit = set(cls[(d0 <= b) & (d1 >= a)])
        has_irr, has_non = bool(hit & irr), bool(hit & non)
        truth.append("mixed" if "mixed" in hit or (has_irr and has_non)
                     else "irradiance" if has_irr else "non_irradiance" if has_non else "none")
    return events.assign(cause_true=truth)
