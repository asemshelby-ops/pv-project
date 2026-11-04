"""Event-level scoring of alarm sequences.

Rules (agreed for the oral defence):
  * spike:           the FIRST alarm inside [start, end] is a true positive and
                     sets the detection delay (alarm - start); later alarms in
                     the same spike are ignored. A spike without one is missed.
  * irradiance_rise: every alarm inside is a false alarm (also counted apart).
  * outside events:  every alarm is a false alarm.
  * minor_lull, mixed, natural_minor, ambient_rise: neutral -- alarms inside are ignored
                     (minor lulls with an alarm are still counted, for reporting).
  * natural_spike:   scored separately (detected / missed / alarms) and kept
                     out of the main precision / recall.
If windows overlap, the class with the highest priority decides:
spike > natural_spike > neutral > irradiance_rise.
"""
import numpy as np
import pandas as pd

NEUTRAL = {"minor_lull", "mixed", "natural_minor", "ambient_rise"}
PRIORITY = {"spike": 0, "natural_spike": 1, "minor_lull": 2, "mixed": 2,
            "natural_minor": 2, "ambient_rise": 2, "irradiance_rise": 3}


def score(ts, alarms, events, n_days):
    """ts: timestamps per sample; alarms: bool per sample; events: table."""
    ts = pd.to_datetime(pd.Series(ts)).to_numpy()
    t_al = ts[np.asarray(alarms, dtype=bool)]
    ev = events.assign(start=pd.to_datetime(events["start"]),
                       end=pd.to_datetime(events["end"])).reset_index(drop=True)
    first = {}                   # event index -> first alarm time
    fp = fp_irr = 0
    nat_alarms = 0
    for t in t_al:
        inside = ev[(ev["start"] <= t) & (ev["end"] >= t)]
        if inside.empty:
            fp += 1
            continue
        best = min(inside["class"], key=PRIORITY.__getitem__)
        if best in ("spike", "natural_spike"):
            idx = inside.index[inside["class"] == best][0]
            first.setdefault(idx, t)
            nat_alarms += best == "natural_spike"
        elif best == "irradiance_rise":
            fp += 1
            fp_irr += 1
        else:                    # neutral: not scored, but remembered for reporting
            first.setdefault(inside.index[inside["class"] == best][0], t)

    spikes = ev.index[ev["class"] == "spike"]
    tp_idx = [i for i in spikes if i in first]
    delays = [(first[i] - ev.at[i, "start"].to_datetime64()) / np.timedelta64(1, "m")
              for i in tp_idx]
    tp, n_sp = len(tp_idx), len(spikes)
    precision = tp / (tp + fp) if tp + fp else float("nan")
    recall = tp / n_sp if n_sp else float("nan")
    f1 = (2 * precision * recall / (precision + recall)
          if tp and precision + recall else 0.0)
    nat = ev.index[ev["class"] == "natural_spike"]
    nat_det = sum(i in first for i in nat)
    return {
        "spikes": n_sp, "TP": tp, "FN": n_sp - tp, "FP": fp,
        "precision": precision, "recall": recall, "F1": f1,
        "delay_median_min": float(np.median(delays)) if delays else float("nan"),
        "delay_mean_min": float(np.mean(delays)) if delays else float("nan"),
        "FA_per_day": fp / n_days,
        "FA_irradiance_rise": fp_irr,
        "irradiance_rises": int((ev["class"] == "irradiance_rise").sum()),
        "mixed_excluded": int((ev["class"] == "mixed").sum()),
        "natural_spikes": len(nat), "natural_detected": nat_det,
        "natural_missed": len(nat) - nat_det, "natural_alarms": nat_alarms,
        "minor_lulls": int((ev["class"] == "minor_lull").sum()),
        "minor_lulls_alarmed": sum(i in first for i in ev.index[ev["class"] == "minor_lull"]),
    }
