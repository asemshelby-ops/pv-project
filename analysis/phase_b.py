"""Phase B: the randomized spray trial, from the logger's rows to the comparison of the
two arms. NOT ADOPTED: prepared 2026-11-17, before any pump exists, and tested on
SYNTHETIC data only (analysis/fake_phase_b.py). Design and limits: docs/phase-b/02.

What the logger gives (firmware built with PHASE_B_ENABLED 1; data/schema.py):
  spike_id, spray_assign                      which rows belong to which event and arm
  pump_duty, flow_Lpm, water_L_cum, P_pump_W  10 s means / the running litre count
  /spray_log.csv                              boot lines (seed, counters, dry run), every
                                              event, every alarm that opened none

What this module computes:
  allocation()       the arm of event n, the same function as the firmware's spray_alloc()
  event_table()      one row per event: arm, water, pump energy, spray time, module energy
                     over the spike window per unit of irradiation, the same over the event's
                     own baseline, validity
  arm_comparison()   spray against no-spray, side by side; difference with its interval and a
                     permutation test; gain, pump cost, net, litres per kWh
  cleaning_check()   I_sc / G before and after each event from the nearest sweeps, per arm:
                     the part of a power change that is cleaner glass, not a cooler module
  audit()            every logged arm against the seed

Why the comparison is between ARMS and not "before and after a spray". A spray happens
during a spike: the module would have kept heating, the sun may be changing, and a
re-heating follows every cooling. The no-spray arm is the same kind of event left alone,
chosen by chance, so the difference between the arms is the effect of spraying and of
nothing else. Every quantity is therefore computed in the same way for both arms.
"""
import json
import math
import pathlib
import re

import numpy as np
import pandas as pd

import config

MASK = 0xFFFFFFFF


# --- allocation: the firmware's spray_alloc(), bit for bit --------------------------------

def _mix32(x):
    """Integer hash "lowbias32" (pv_spray_logic.c, mix32)."""
    x &= MASK
    x ^= x >> 16
    x = (x * 0x7FEB352D) & MASK
    x ^= x >> 15
    x = (x * 0x846CA68B) & MASK
    x ^= x >> 16
    return x


def allocation(seed, n, block=config.PHASE_B_BLOCK_SIZE):
    """Arm (1 = spray, 0 = no-spray) of event number n (0, 1, 2 ...) of the sequence `seed`.
    Block n // block holds block/2 ones and block/2 zeros in an order drawn by a
    Fisher-Yates shuffle whose random numbers depend only on (seed, block number)."""
    if block < 2 or block % 2:
        raise ValueError("the block size must be even and at least 2")
    b, k = divmod(int(n), block)
    arm = [1 if i < block // 2 else 0 for i in range(block)]
    s = _mix32((seed & MASK) ^ _mix32((b + 0x9E3779B9) & MASK))
    for i in range(block - 1, 0, -1):
        bound = i + 1
        reject = (2 ** 32 - bound) % bound          # these r would bias j
        while True:
            s = (s + 0x9E3779B9) & MASK
            r = _mix32(s)
            if r >= reject:
                break
        j = r % bound
        arm[i], arm[j] = arm[j], arm[i]
    return arm[k]


def dry_seed(seed):
    """Seed of the dry-run sequence (pv_spray_logic.c, dry_seed)."""
    return _mix32((seed & MASK) ^ 0xD5A1D5A1)


def expected_assign(spike_id, seed, block=config.PHASE_B_BLOCK_SIZE):
    """Arm the firmware must have drawn for a logged event. Wet events are numbered
    1, 2, 3 ...; dry-run events -1, -2, -3 ... from their own sequence."""
    spike_id = int(spike_id)
    if spike_id == 0:
        raise ValueError("spike_id 0 means no event")
    n = abs(spike_id) - 1
    return allocation(dry_seed(seed) if spike_id < 0 else seed, n, block)


# --- per-event table --------------------------------------------------------------------------

DT_H = config.DT_S / 3600.0
WINDOW_ROWS = int(config.PHASE_B_EVENT_WINDOW_MIN * 60 / config.DT_S)
BASELINE_ROWS = int(config.PHASE_B_BASELINE_MIN * 60 / config.DT_S)
EVENT_COLUMNS = [
    "spike_id", "date_local", "t_alarm", "assign", "dry_run", "assign_expected", "assign_ok",
    "n_rows", "rows_frac", "baseline_frac", "G_mean", "T_alarm_C", "dT_after_spray_C",
    "dT_mean_C", "spray_s", "litres", "pump_Wh", "pump_rows_missing", "E_module_Wh",
    "H_kWh_m2", "yield_W_per_kWm2", "yield0_W_per_kWm2", "gain_rel", "delivered", "valid",
    "heldout", "note"]


def in_heldout_week(dates):
    """True for the local dates ("YYYY-MM-DD") inside the one week that is never used for
    tuning (config.PHASE_B_HELDOUT_WEEK; all False while no week is named)."""
    dates = pd.Series(dates, dtype="object")
    if config.PHASE_B_HELDOUT_WEEK is None:
        return pd.Series(False, index=dates.index)
    first, last = config.PHASE_B_HELDOUT_WEEK
    for day in (first, last):                    # the dates are compared as text
        if pd.Timestamp(day).strftime("%Y-%m-%d") != day:
            raise ValueError(f"config.PHASE_B_HELDOUT_WEEK: {day!r} is not written as YYYY-MM-DD")
    if first > last:
        raise ValueError("config.PHASE_B_HELDOUT_WEEK: the first day lies after the last")
    return (dates >= first) & (dates <= last)


def has_columns(df):
    """True if the table comes from a logger built with PHASE_B_ENABLED 1."""
    from data.schema import PHASE_B_COLUMNS
    return all(c in df.columns for c in PHASE_B_COLUMNS)


def _usable(rows):
    """Rows whose power may be compared with the irradiance: both present, enough light,
    and the load holding the module (not off, tripped or sweeping). Without a load
    (`load_mode` none) the module is at open circuit and has no power to compare."""
    ok = rows["power"].notna() & rows["irradiance"].notna() & (rows["irradiance"] >= config.G_FLOOR)
    if "load_mode" in rows:
        ok &= rows["load_mode"] == "hold"
    return ok


def _yield(rows):
    """(module energy Wh, irradiation kWh/m2, their ratio in Wh per kWh/m2 = W per kW/m2)."""
    r = rows[_usable(rows)]
    e, h = float(r["power"].sum() * DT_H), float(r["irradiance"].sum() * DT_H / 1000.0)
    return e, h, (e / h if h > 0 else np.nan)


def _litres(cum):
    """Water between the first and the last row from the running count. The count starts
    again at 0 when the board restarts: a fall is read as "that much since the restart"."""
    w = np.asarray(cum, dtype=float)
    w = w[~np.isnan(w)]
    if len(w) < 2:
        return 0.0
    d = np.diff(w)
    return float(np.where(d < 0, w[1:], d).sum())


def boot_settings(log):
    """{boot number: settings of that boot line} from read_spray_log()."""
    out = {}
    for _, r in log[log["event"] == "boot"].iterrows():
        d = r["detail"]
        out[int(r["boot"])] = {k: float(d[k]) for k in d if re.fullmatch(r"-?[\d.]+", str(d[k]))}
    return out


def event_table(df, log=None, excluded=None, seed=None, block=None):
    """One row per spike event. df: analysis.ingest.read_logger() of a Phase B logger.
    log: ingest.read_spray_log() (optional: gives the seed, block and pump duty the board
    really used; without it config.py's values are taken). excluded: ingest.excluded_windows().

    Every quantity is computed in the same way for both arms, from the rows alone:
      window    the rows that carry the event's spike_id (the firmware's own window)
      baseline  the PHASE_B_BASELINE_MIN minutes before the alarm row
      litres    from the running litre count; pump_Wh and spray_s from the 10 s means
      yield     module energy / irradiation over the window, Wh per kWh/m2;
                gain_rel = yield / yield of the baseline - 1
    """
    if not has_columns(df):
        return pd.DataFrame(columns=EVENT_COLUMNS)
    settings = boot_settings(log) if log is not None and len(log) else {}
    last = settings[max(settings)] if settings else {}
    seed = int(last.get("seed", config.PHASE_B_SEED)) if seed is None else seed
    block = int(last.get("block", config.PHASE_B_BLOCK_SIZE)) if block is None else block
    duty_cmd = float(last.get("duty", np.nan))
    from data.schema import BACK_SENSORS
    d = df.sort_values("t_utc").reset_index(drop=True)
    t = d["t_local"].to_numpy()
    t_mod = d[BACK_SENSORS].mean(axis=1).to_numpy(dtype=float)
    sid = d["spike_id"].fillna(0).astype("int64").to_numpy()
    after = int(round(config.SPRAY_MAX_S / config.DT_S)) + 3     # 30 s after the longest spray
    rows = []
    for k in [k for k in pd.unique(sid) if k != 0]:
        idx = np.flatnonzero(sid == k)
        i0, w = int(idx[0]), d.iloc[idx]
        base = d.iloc[max(i0 - BASELINE_ROWS, 0):i0]
        base = base[base["t_local"] >= d.at[i0, "t_local"] - pd.Timedelta(minutes=config.PHASE_B_BASELINE_MIN)]
        arms = w["spray_assign"].dropna().unique()
        assign = int(arms[0]) if len(arms) == 1 else -1
        e, h, y = _yield(w)
        _, _, y0 = _yield(base)
        duty = duty_cmd if duty_cmd == duty_cmd else float(w["pump_duty"].max())
        note = []
        if len(arms) != 1 or assign not in (0, 1):
            note.append("arm not constant over the window")
        expected = expected_assign(k, seed, block)
        if assign in (0, 1) and assign != expected:
            note.append("arm differs from the allocation sequence")
        t0 = pd.Timestamp(t[i0])
        if excluded is not None and len(excluded):
            hit = excluded[(excluded["start"] <= pd.Timestamp(t[idx[-1]])) &
                           (excluded["end"] >= t0 - pd.Timedelta(minutes=config.PHASE_B_BASELINE_MIN))]
            note += [f"excluded: {r}" for r in hit["reason"].unique()]
        litres = _litres(w["water_L_cum"])
        row = {"spike_id": int(k), "date_local": t0.strftime("%Y-%m-%d"), "t_alarm": t0,
               "assign": assign, "dry_run": bool(k < 0), "assign_expected": expected,
               "assign_ok": assign == expected, "n_rows": len(w),
               "rows_frac": float(_usable(w).sum() / WINDOW_ROWS),
               "baseline_frac": float(_usable(base).sum() / BASELINE_ROWS),
               "G_mean": float(w["irradiance"].mean()), "T_alarm_C": float(t_mod[i0]),
               "dT_after_spray_C": (float(t_mod[i0 + after] - t_mod[i0])
                                    if i0 + after <= idx[-1] else np.nan),
               "dT_mean_C": float(np.nanmean(t_mod[idx]) - np.nanmean(t_mod[base.index]))
               if len(base) else np.nan,
               "spray_s": float(w["pump_duty"].sum() * config.DT_S / duty) if duty > 0 else 0.0,
               "litres": litres, "pump_Wh": float(w["P_pump_W"].sum() * DT_H),
               "pump_rows_missing": int(w["P_pump_W"].isna().sum()), "E_module_Wh": e,
               "H_kWh_m2": h, "yield_W_per_kWm2": y, "yield0_W_per_kWm2": y0,
               "gain_rel": y / y0 - 1.0 if y0 and y0 == y0 and y == y else np.nan}
        # the arm was followed: water in the spray arm of a wet run, none otherwise
        row["delivered"] = bool(litres > 0.0) if assign == 1 and k > 0 else bool(litres < 0.01)
        complete = (row["rows_frac"] >= config.PHASE_B_MIN_ROWS_FRAC
                    and row["baseline_frac"] >= config.PHASE_B_MIN_ROWS_FRAC)
        if not complete:
            note.append("window or baseline incomplete (rows, light or load)")
        row["valid"] = bool(complete and not note)
        row["note"] = "; ".join(note)
        rows.append(row)
    events = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    events["heldout"] = in_heldout_week(events["date_local"]).to_numpy(dtype=bool)
    return events


def spray_windows(events):
    """Windows in which a wet spray changed the module's temperature: for the paper's
    spike statistics and detector scoring they are excluded periods, like a cleaning
    (docs/paper/03 sec. 4): the re-heating after a spray is not a spike of interest."""
    e = events[(events["assign"] == 1) & ~events["dry_run"] & (events["litres"] > 0)]
    end = e["t_alarm"] + pd.Timedelta(minutes=config.PHASE_B_EVENT_WINDOW_MIN + config.PHASE_B_LOCKOUT_MIN)
    return pd.DataFrame({"start": e["t_alarm"].to_numpy(), "end": end.to_numpy(),
                         "reason": "spray event"})


# --- comparison of the two arms -----------------------------------------------------------------

QUANTITIES = [   # (column, unit, what it is)
    ("yield_W_per_kWm2", "Wh per kWh/m2", "module energy over the window per unit of irradiation"),
    ("gain_rel", "1", "the same, relative to the event's own baseline"),
    ("dT_after_spray_C", "degC", "module temperature 30 s after the longest spray minus at the alarm"),
    ("dT_mean_C", "degC", "mean module temperature of the window minus of the baseline"),
    ("spray_s", "s", "pump-on time"),
    ("litres", "L", "water"),
    ("pump_Wh", "Wh", "pump energy over the window"),
    ("G_mean", "W/m2", "mean irradiance of the window (should not differ: a balance check)"),
    ("T_alarm_C", "degC", "module temperature at the alarm (should not differ: a balance check)"),
]


def welch(a, b, alpha=config.MDE_ALPHA):
    """Difference of two means (a - b) with its Welch interval. Returns (difference,
    standard error, degrees of freedom, low, high); NaN where an arm has fewer than 2."""
    from scipy import stats
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if len(a) < 2 or len(b) < 2:
        return (float(np.mean(a) - np.mean(b)) if len(a) and len(b) else np.nan,
                np.nan, np.nan, np.nan, np.nan)
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    se = math.sqrt(va + vb)
    diff = float(a.mean() - b.mean())
    if se == 0:
        return diff, 0.0, np.nan, diff, diff
    dof = (va + vb) ** 2 / (va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1))
    half = float(stats.t.ppf(1 - alpha / 2, dof)) * se
    return diff, se, float(dof), diff - half, diff + half


def permutation_p(values, assign, blocks, n=20000, seed=0):
    """Two-sided p-value of the difference of the arm means under the trial's own
    randomization: the arms are re-drawn inside each block, as the logger drew them, so no
    distribution is assumed. NaN when an arm is empty."""
    v, a, b = (np.asarray(x) for x in (values, assign, blocks))
    keep = ~np.isnan(v.astype(float))
    v, a, b = v[keep].astype(float), a[keep].astype(int), b[keep]
    if a.sum() == 0 or a.sum() == len(a):
        return np.nan
    observed = abs(v[a == 1].mean() - v[a == 0].mean())
    rng = np.random.default_rng(seed)
    arms = np.empty((n, len(a)), dtype=float)
    for k in np.unique(b):                       # every block shuffled on its own, n times
        g = np.flatnonzero(b == k)
        arms[:, g] = rng.permuted(np.tile(a[g], (n, 1)), axis=1)
    n1 = arms.sum(axis=1)
    s1 = arms @ v
    diff = np.abs(s1 / n1 - (v.sum() - s1) / (len(v) - n1))
    return float(((diff >= observed - 1e-12).sum() + 1) / (n + 1))


def analysis_set(events, which="as assigned", part="tuning"):
    """The events that enter the comparison. Wet runs only (a dry run is reported apart).
      "as assigned"  every valid event, in the arm the logger drew for it (the main result)
      "as delivered" the same without the events whose arm was not followed (a spray that
                     gave no water, water in a no-spray event)
    part: "tuning" (default) leaves out the held-out week, so nothing that is looked at
    while settings are still being chosen contains it; "heldout" is that week alone and
    raises PermissionError while config.PHASE_B_HELDOUT_UNLOCKED is False; "all" is both
    and is locked in the same way once a week is named."""
    e = events[events["valid"] & ~events["dry_run"] & events["assign"].isin([0, 1])]
    if which == "as delivered":
        e = e[e["delivered"]]
    held = e["heldout"].astype(bool) if "heldout" in e else pd.Series(False, index=e.index)
    if part == "tuning":
        return e[~held]
    if config.PHASE_B_HELDOUT_WEEK is not None and not config.PHASE_B_HELDOUT_UNLOCKED:
        raise PermissionError("the held-out week is locked: set config.PHASE_B_HELDOUT_UNLOCKED "
                              "only when every setting and the analysis code are frozen")
    return e[held] if part == "heldout" else e


def arm_comparison(events, which="as assigned", n_perm=20000, seed=0, block=config.PHASE_B_BLOCK_SIZE,
                   part="tuning"):
    """Spray against no-spray, one row per quantity: mean and spread per arm, the
    difference (spray minus no-spray) with its 95 % interval and the permutation p-value."""
    e = analysis_set(events, which, part)
    s, c = e[e["assign"] == 1], e[e["assign"] == 0]
    blocks = ((e["spike_id"].abs() - 1) // block).to_numpy()
    rows = []
    for col, unit, what in QUANTITIES:
        diff, se, dof, lo, hi = welch(s[col], c[col])
        rows.append({"quantity": col, "unit": unit, "what": what,
                     "no_spray_n": int(c[col].notna().sum()), "no_spray_mean": c[col].mean(),
                     "no_spray_sd": c[col].std(ddof=1), "spray_n": int(s[col].notna().sum()),
                     "spray_mean": s[col].mean(), "spray_sd": s[col].std(ddof=1),
                     "difference": diff, "lo": lo, "hi": hi,
                     "p_permutation": permutation_p(e[col], e["assign"], blocks, n_perm, seed)})
    return pd.DataFrame(rows)


def _balance(s, c, adjusted):
    """Gain, pump cost, net (Wh per spray event) and litres from two sets of events."""
    if adjusted:      # effect on the baseline-relative yield x what the window would have given
        gain = (s["gain_rel"].mean() - c["gain_rel"].mean()) * float(
            (s["yield0_W_per_kWm2"] * s["H_kWh_m2"]).mean())
    else:             # difference of the normalized energies x the irradiation of a window
        gain = (s["yield_W_per_kWm2"].mean() - c["yield_W_per_kWm2"].mean()) * s["H_kWh_m2"].mean()
    pump = s["pump_Wh"].mean() - c["pump_Wh"].mean()     # the no-spray arm takes out any offset
    return float(gain), float(pump), float(gain - pump), float(s["litres"].mean() - c["litres"].mean())


def energy_balance(events, which="as assigned", n_boot=2000, seed=0, part="tuning"):
    """Net energy of a spray event and water per kWh, twice: from the normalized energy
    itself and from its baseline-relative form (same expectation, less spread, because
    each event is compared with its own minutes before the alarm). Intervals: bootstrap
    over events inside each arm, 95 % percentile. L/kWh is given only where the net (or
    gross) energy is above zero: below zero there is nothing to divide by."""
    e = analysis_set(events, which, part)
    s, c = e[e["assign"] == 1], e[e["assign"] == 0]
    rows, rng = [], np.random.default_rng(seed)
    for adjusted in (False, True):
        row = {"estimate": "relative to each event's baseline" if adjusted else "normalized energy",
               "spray_events": len(s), "no_spray_events": len(c)}
        if len(s) < 2 or len(c) < 2:
            rows.append({**row, "note": "fewer than 2 valid events in an arm"})
            continue
        gain, pump, net, litres = _balance(s, c, adjusted)
        draws = np.array([_balance(s.iloc[rng.integers(0, len(s), len(s))],
                                   c.iloc[rng.integers(0, len(c), len(c))], adjusted)[:3]
                          for _ in range(n_boot)])
        lo, hi = np.nanpercentile(draws, [2.5, 97.5], axis=0)
        row.update({"gain_Wh": gain, "gain_lo": lo[0], "gain_hi": hi[0], "pump_Wh": pump,
                    "net_Wh": net, "net_lo": lo[2], "net_hi": hi[2], "litres": litres,
                    "L_per_kWh_gross": 1000.0 * litres / gain if gain > 0 else np.nan,
                    "L_per_kWh_net": 1000.0 * litres / net if net > 0 else np.nan,
                    "note": "" if net > 0 else "net energy not above zero: no L/kWh (net)"})
        rows.append(row)
    return pd.DataFrame(rows)


# --- cleaning or cooling? -------------------------------------------------------------------------

CLEAN_COLUMNS = ["spike_id", "assign", "t_alarm", "sweep_before", "sweep_after", "sweep_late",
                 "isc_g_before", "isc_g_after", "isc_g_late", "d_isc_g_after", "d_isc_g_late",
                 "T_before_C", "T_after_C", "T_late_C", "note"]


def cleaning_check(events, sweeps):
    """I_sc / G before and after every event, from the nearest usable I-V sweeps.

    Why I_sc / G: the short-circuit current follows the light that reaches the cells and
    hardly moves with temperature, while the power moves with both. A spray that only
    cools leaves I_sc / G where it was; one that also washes dust off raises it. So the
    change of I_sc / G across an event is the part that is NOT cooling.
      before  the nearest usable sweep at or before the alarm
      after   the nearest one at or after alarm + SPRAY_MAX_S + CLEAN_AFTER_DELAY_MIN
              (default 0: the first sweep once the longest spray has ended; the glass may
              still be wet, and the module is at its coldest)
      late    the last one before the window closes: the module is back near its
              temperature, the glass is dry -- the cleanest comparison with `before`
    each within CLEAN_SWEEP_WINDOW_MIN of its target time. The times are fixed by the alarm
    and by config.py, never by the arm, and the no-spray arm gets the same treatment: what
    the sun's angle and the pyranometer do to I_sc / G over half an hour shows there, and
    the difference between the arms is what the water did.
    sweeps: analysis.gamma.sweep_parameters(); only gamma.usable_sweeps() are taken."""
    from analysis import gamma
    cols = ["t_local", "I_sc", "G", "T_module", "sweep_id"]
    u = gamma.usable_sweeps(sweeps)[cols].dropna(subset=["I_sc", "G"]).sort_values("t_local") \
        if len(sweeps) else pd.DataFrame(columns=cols)
    ts = u["t_local"].to_numpy()
    span = np.timedelta64(int(config.CLEAN_SWEEP_WINDOW_MIN * 60), "s")
    rows = []
    for _, ev in events.iterrows():
        t0 = np.datetime64(ev["t_alarm"])
        t_after = t0 + np.timedelta64(int(config.SPRAY_MAX_S + config.CLEAN_AFTER_DELAY_MIN * 60), "s")
        t_end = t0 + np.timedelta64(int(config.PHASE_B_EVENT_WINDOW_MIN * 60), "s")
        ib = np.searchsorted(ts, t0, side="right") - 1          # last sweep at or before the alarm
        ia = np.searchsorted(ts, t_after, side="left")          # first at or after the spray's end
        il = np.searchsorted(ts, t_end, side="left") - 1        # last before the window closes
        pick = {"before": ib if ib >= 0 and t0 - ts[ib] <= span else None,
                "after": ia if ia < len(ts) and ts[ia] - t_after <= span and ts[ia] < t_end else None,
                "late": il if il >= 0 and t_end - ts[il] <= span and ts[il] > t_after else None}
        row = {"spike_id": int(ev["spike_id"]), "assign": int(ev["assign"]), "t_alarm": ev["t_alarm"]}
        for name, k in pick.items():
            r = u.iloc[k] if k is not None else None
            row[f"sweep_{name}"] = int(r["sweep_id"]) if r is not None else pd.NA
            row[f"isc_g_{name}"] = float(r["I_sc"] / r["G"] * 1000.0) if r is not None else np.nan
            row[f"T_{name}_C"] = float(r["T_module"]) if r is not None else np.nan
        row["d_isc_g_after"] = row["isc_g_after"] / row["isc_g_before"] - 1.0
        row["d_isc_g_late"] = row["isc_g_late"] / row["isc_g_before"] - 1.0
        missing = [n for n, k in pick.items() if k is None]
        row["note"] = "no usable sweep: " + ", ".join(missing) if missing else ""
        rows.append(row)
    return pd.DataFrame(rows, columns=CLEAN_COLUMNS)


def cleaning_summary(clean, events, which="as assigned"):
    """The change of I_sc / G per arm and the difference between the arms (spray minus
    no-spray): the cleaning effect of the water, apart from its cooling."""
    ids = set(analysis_set(events, which)["spike_id"])
    c = clean[clean["spike_id"].isin(ids)]
    rows = []
    for col, what in (("d_isc_g_after", "first sweep after the longest spray"),
                      ("d_isc_g_late", "last sweep of the window (module re-heated, glass dry)")):
        a, b = c[c["assign"] == 1][col], c[c["assign"] == 0][col]
        diff, _, _, lo, hi = welch(a, b)
        rows.append({"change of I_sc/G": what, "no_spray_n": int(b.notna().sum()),
                     "no_spray_mean": b.mean(), "spray_n": int(a.notna().sum()),
                     "spray_mean": a.mean(), "difference": diff, "lo": lo, "hi": hi})
    return pd.DataFrame(rows)


# --- audit and the whole step ----------------------------------------------------------------------

def audit(events, block=config.PHASE_B_BLOCK_SIZE):
    """What a reader must be able to check before believing the comparison."""
    wet = events[~events["dry_run"]]
    ids = sorted(wet["spike_id"])
    missing = sorted(set(range(1, max(ids) + 1)) - set(ids)) if ids else []
    v = analysis_set(events)
    held = events["heldout"].astype(bool) if "heldout" in events else pd.Series(False, index=events.index)
    return {"events": len(events), "wet": len(wet), "dry_run": int(events["dry_run"].sum()),
            "heldout_week": list(config.PHASE_B_HELDOUT_WEEK) if config.PHASE_B_HELDOUT_WEEK else None,
            "heldout_events": int(held.sum()),
            "heldout_locked": bool(config.PHASE_B_HELDOUT_WEEK is not None
                                   and not config.PHASE_B_HELDOUT_UNLOCKED),
            "arms_wet": {"spray": int((wet["assign"] == 1).sum()), "no_spray": int((wet["assign"] == 0).sum())},
            "valid": {"spray": int((v["assign"] == 1).sum()), "no_spray": int((v["assign"] == 0).sum())},
            "not_valid": int((~wet["valid"]).sum()),
            "arm_differs_from_sequence": [int(k) for k in events[~events["assign_ok"]]["spike_id"]],
            "numbers_never_logged": missing,
            "not_delivered": [int(k) for k in wet[~wet["delivered"]]["spike_id"]],
            "block": block}


def _fmt(v, f=".3g"):
    return "-" if v is None or (isinstance(v, float) and math.isnan(v)) else format(v, f)


def _arm_lines(arms):
    lines = ["| quantity | unit | no-spray: n, mean (sd) | spray: n, mean (sd) | spray minus no-spray (95 %) | p (permutation) |",
             "|---|---|---|---|---|---|"]
    for _, r in arms.iterrows():
        lines.append(f"| {r['what']} | {r['unit']} | {r['no_spray_n']}, {_fmt(r['no_spray_mean'], '.4g')} "
                     f"({_fmt(r['no_spray_sd'], '.3g')}) | {r['spray_n']}, {_fmt(r['spray_mean'], '.4g')} "
                     f"({_fmt(r['spray_sd'], '.3g')}) | {_fmt(r['difference'], '+.3g')} "
                     f"({_fmt(r['lo'], '+.3g')} to {_fmt(r['hi'], '+.3g')}) | {_fmt(r['p_permutation'], '.3f')} |")
    return lines


def markdown(result, synthetic=False):
    """The Phase B section of summary.md."""
    a, arms, bal, cs = result["audit"], result["arms"], result["balance"], result["cleaning_summary"]
    lines = ["## Phase B - randomized spray trial (NOT ADOPTED: a preparation)", ""]
    if synthetic:
        lines += ["**SYNTHETIC DATA: the spray's cooling, the pump's power, the flow and the "
                  "cleaning step are invented numbers put in by the generator. This shows that "
                  "the analysis runs and finds them again; it says nothing about a real spray.**", ""]
    if a.get("heldout_week"):
        lines += [f"Held-out week {a['heldout_week'][0]} to {a['heldout_week'][1]}: {a['heldout_events']} "
                  "events. " + ("**LOCKED: they are in none of the tables below** and are looked at "
                                "once, when every setting and the analysis code are frozen."
                                if a["heldout_locked"] else
                                "UNLOCKED: the first table below is the other weeks, the second "
                                "the held-out week alone."), ""]
    lines += [f"Events: {a['events']} ({a['wet']} wet, {a['dry_run']} dry run). Wet arms as drawn: "
              f"{a['arms_wet']['spray']} spray, {a['arms_wet']['no_spray']} no-spray; valid for the "
              f"comparison: {a['valid']['spray']} and {a['valid']['no_spray']}.",
              f"Checks: arm differs from the seeded sequence in {len(a['arm_differs_from_sequence'])} "
              f"events; event numbers never logged: {a['numbers_never_logged'] or 'none'}; arm not "
              f"followed (no water in a spray event, or water in a no-spray event): "
              f"{a['not_delivered'] or 'none'}.", "",
              "### Arms side by side (every valid event in the arm it was drawn for)", ""]
    lines += _arm_lines(arms)
    if "arms_heldout" in result:
        lines += ["", "### The held-out week alone (settings and code frozen before it began)", ""]
        lines += _arm_lines(result["arms_heldout"])
    lines += ["", "### Energy of one spray event: gain, pump, net; water per kWh", "",
              "| estimate | gain Wh (95 %) | pump Wh | net Wh (95 %) | litres | L/kWh gross | L/kWh net |",
              "|---|---|---|---|---|---|---|"]
    for _, r in bal.iterrows():
        if "gain_Wh" not in r or pd.isna(r.get("gain_Wh")):
            lines.append(f"| {r['estimate']} | {r.get('note', '')} | | | | | |")
            continue
        lines.append(f"| {r['estimate']} | {r['gain_Wh']:+.3f} ({r['gain_lo']:+.3f} to {r['gain_hi']:+.3f}) "
                     f"| {r['pump_Wh']:.3f} | {r['net_Wh']:+.3f} ({r['net_lo']:+.3f} to {r['net_hi']:+.3f}) "
                     f"| {r['litres']:.2f} | {_fmt(r['L_per_kWh_gross'], '.0f')} | {_fmt(r['L_per_kWh_net'], '.0f')} |")
    lines += ["", "Gain = difference between the arms, times what a window delivers; pump and litres "
              "= spray arm minus no-spray arm (so a sensor offset cancels); net = gain - pump.", "",
              "### Cleaning or cooling: change of I_sc / G across an event", "",
              "| sweep compared with the one before the alarm | no-spray: n, mean | spray: n, mean | spray minus no-spray (95 %) |",
              "|---|---|---|---|"]
    for _, r in cs.iterrows():
        lines.append(f"| {r['change of I_sc/G']} | {r['no_spray_n']}, {_fmt(100 * r['no_spray_mean'], '+.2f')} % "
                     f"| {r['spray_n']}, {_fmt(100 * r['spray_mean'], '+.2f')} % | {_fmt(100 * r['difference'], '+.2f')} % "
                     f"({_fmt(100 * r['lo'], '+.2f')} to {_fmt(100 * r['hi'], '+.2f')}) |")
    lines += ["", "I_sc / G hardly moves with temperature, so a difference between the arms here is "
              "light reaching the cells (cleaner or wetter glass), not cooling.", ""]
    return lines


def run(df, out=None, log=None, sweeps=None, excluded=None, n_boot=2000, n_perm=20000, seed=0):
    """The whole Phase B step on an ingested table. Writes phase_b_*.csv to `out`.

    Held-out week (config.PHASE_B_HELDOUT_WEEK): while it is locked, its events are counted
    in the audit and appear in no table at all, neither in the event table nor in the
    cleaning table. Once unlocked, `arms` and `balance` stay what they were (the other
    weeks) and the week gets its own two tables, arms_heldout and balance_heldout."""
    events = event_table(df, log, excluded)
    info = audit(events)
    if info["heldout_locked"]:
        events = events[~events["heldout"]].reset_index(drop=True)
    clean = cleaning_check(events, sweeps if sweeps is not None else pd.DataFrame())
    result = {"events": events, "arms": arm_comparison(events, n_perm=n_perm, seed=seed),
              "arms_delivered": arm_comparison(events, "as delivered", n_perm=n_perm, seed=seed),
              "balance": energy_balance(events, n_boot=n_boot, seed=seed),
              "cleaning": clean, "cleaning_summary": cleaning_summary(clean, events),
              "audit": info}
    names = ["events", "arms", "arms_delivered", "balance", "cleaning", "cleaning_summary"]
    if config.PHASE_B_HELDOUT_WEEK is not None and config.PHASE_B_HELDOUT_UNLOCKED:
        result["arms_heldout"] = arm_comparison(events, n_perm=n_perm, seed=seed, part="heldout")
        result["balance_heldout"] = energy_balance(events, n_boot=n_boot, seed=seed, part="heldout")
        names += ["arms_heldout", "balance_heldout"]
    if out is not None:
        out = pathlib.Path(out)
        out.mkdir(parents=True, exist_ok=True)
        for name in names:
            result[name].to_csv(out / f"phase_b_{name}.csv", index=False)
        (out / "phase_b_audit.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return result


def main():
    """python -m analysis.phase_b --data <copy of the card> --out <folder>"""
    import argparse
    from analysis import gamma, ingest
    ap = argparse.ArgumentParser(description="Phase B: spray trial tables from a card copy")
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    data = pathlib.Path(a.data)
    df = ingest.read_logger(data)
    points, summary = gamma.read_sweeps(data)
    sweeps = gamma.sweep_parameters(points, summary) if len(points) else None
    excluded = None
    if (data / "markers.csv").exists():
        m, _ = ingest.read_markers(data / "markers.csv")
        excluded = ingest.excluded_windows(ingest.join_logsheets(m, data / "logsheets")[0])
    result = run(df, a.out, ingest.read_spray_log(data / "spray_log.csv"), sweeps, excluded)
    print("\n".join(markdown(result)))


if __name__ == "__main__":
    main()
