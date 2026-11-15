"""Power BI-ready CSV tables from the logger files.

    python -m analysis.powerbi --data <folder>            (writes out/powerbi/)
    python -m analysis.powerbi --data <folder> --out <folder>
    python -m analysis.powerbi --demo                     (SYNTHETIC files, to try Power BI)

<folder> is what comes off the SD card (same layout as analysis/pipeline.py): /YYYYMMDD.csv,
optional /markers.csv + logsheets/, optional /SWYYYYMMDD.csv + /SSYYYYMMDD.csv.

Writes four tables, all in LOCAL Makkah time (UTC+3), ISO dates, '.' as decimal point:
  daily_summary.csv  one row per local day: energy, max module temperature, irradiance,
                     spikes, on-board alarms
  spikes.csv         one row per labelled rise (class spike or minor): start, size, cause,
                     first on-board alarm of each detector and its delay
  sweeps.csv         one row per I-V sweep: P_mp, V_oc, I_sc, FF, irradiance, temperature
  alarms.csv         one row per on-board alarm onset (rising edge), per detector
The `date` column links every table to daily_summary (docs/powerbi.md).

Nothing is computed here that the paper pipeline does not compute: the spikes come from
analysis.events.label (offline reference labels, NOT what the board saw), the sweep
parameters from analysis.gamma.sweep_parameters. The alarm times are the ones the ESP32
LOGGED in its `alarm` column; nothing is re-run.
"""
import argparse
import pathlib

import numpy as np
import pandas as pd

import config
from analysis import events, gamma, ingest
from data.schema import ALARM_BITS, BACK_SENSORS, has_alarm

DETECTORS = list(ALARM_BITS)               # NASIM, THRESHOLD, CUSUM
ALARM_MATCH_BEFORE_MIN = 2                 # an alarm this long before the onset still counts
ALARM_MATCH_AFTER_PEAK_MIN = 10            # ... and up to this long after the peak
NO_ENERGY_MODES = {"sweep", "trip"}        # rows left out of the energy totals
FMT = "%Y-%m-%d %H:%M:%S"


def _row_dt_h(df):
    """Hours each row stands for: the gap to the next row, capped at one logging period
    (a gap in the file adds no energy)."""
    dt = df["t_utc"].diff().shift(-1).dt.total_seconds().fillna(config.DT_S)
    return dt.clip(upper=config.DT_S) / 3600.0


def alarm_onsets(df):
    """One row per detector alarm onset (alarm bit 0 -> 1 between consecutive rows)."""
    if "alarm" not in df or df["alarm"].isna().all():
        return pd.DataFrame(columns=["date", "time_local", "detector"])
    rows = []
    for name in DETECTORS:
        on = has_alarm(df["alarm"], name)
        first = on & ~on.shift(1, fill_value=False)
        for t in df.loc[first, "t_local"]:
            rows.append({"date": t.strftime("%Y-%m-%d"), "time_local": t.strftime(FMT),
                         "detector": name})
    return pd.DataFrame(rows, columns=["date", "time_local", "detector"]).sort_values(
        "time_local", kind="stable").reset_index(drop=True)


def daily_summary(df, ev, al, sw):
    tm = df[BACK_SENSORS].mean(axis=1)
    mode = df["load_mode"].fillna("none").astype(str)
    use = ~mode.isin(NO_ENERGY_MODES)
    dt_h = _row_dt_h(df)
    d = pd.DataFrame({"date": df["date"],
                      "e": (df["power"] * dt_h).where(use),
                      "h": (df["irradiance"] * dt_h),
                      "tm": tm, "tmax": df[BACK_SENSORS].max(axis=1),
                      "g": df["irradiance"], "p": df["power"].where(use),
                      "ta": df["T_ambient"]})
    g = d.groupby("date")
    out = pd.DataFrame({
        "rows": g.size(),
        "energy_Wh": g["e"].sum(min_count=1).round(2),
        "insolation_kWh_m2": (g["h"].sum(min_count=1) / 1000).round(3),
        "peak_power_W": g["p"].max().round(2),
        "max_T_module_C": g["tm"].max().round(2),
        "max_T_sensor_C": g["tmax"].max().round(2),
        "mean_irradiance_Wm2": g["g"].mean().round(1),
        "max_irradiance_Wm2": g["g"].max().round(1),
        "max_T_ambient_C": g["ta"].max().round(2)}).reset_index()
    q = ingest.day_quality(df)[["date", "completeness"]].round(3)
    out = out.merge(q, on="date", how="left")
    out["performance_ratio"] = (out["energy_Wh"] / (out["insolation_kWh_m2"]
                                * config.P_STC_W)).round(3)
    sp = ev[(ev["class"] == "spike") & ev["valid"]] if len(ev) else ev
    out["spikes"] = out["date"].map(sp.groupby("date_local").size() if len(sp) else {}).fillna(0).astype(int)
    for cause in ("non_irradiance", "irradiance", "mixed"):
        n = sp[sp["cause"] == cause].groupby("date_local").size() if len(sp) else {}
        out[f"spikes_{cause}"] = out["date"].map(n).fillna(0).astype(int)
    out["minor_rises"] = out["date"].map(
        ev[ev["class"] == "minor"].groupby("date_local").size() if len(ev) else {}).fillna(0).astype(int)
    for name in DETECTORS:
        n = al[al["detector"] == name].groupby("date").size() if len(al) else {}
        out[f"alarms_{name}"] = out["date"].map(n).fillna(0).astype(int)
    out["alarms_total"] = out[[f"alarms_{n}" for n in DETECTORS]].sum(axis=1)
    out["sweeps"] = out["date"].map(sw.groupby("date").size() if len(sw) else {}).fillna(0).astype(int)
    return out


def spikes_table(ev, al):
    cols = ["event_id", "date", "start", "peak", "class", "size_C", "duration_min",
            "max_rate_C_per_min", "dG_Wm2", "f_G", "cause", "origin", "valid", "note"]
    if not len(ev):
        return pd.DataFrame(columns=cols + [f"first_alarm_{n}" for n in DETECTORS]
                            + [f"delay_min_{n}" for n in DETECTORS])
    out = pd.DataFrame({"event_id": ev["event_id"], "date": ev["date_local"],
                        "start": ev["start"], "peak": ev["end"], "class": ev["class"],
                        "size_C": ev["size_C"], "duration_min": (ev["duration_s"] / 60).round(2),
                        "max_rate_C_per_min": ev["max_rate_C_per_min"], "dG_Wm2": ev["dG"],
                        "f_G": ev["f_G"], "cause": ev["cause"], "origin": ev["origin"],
                        "valid": ev["valid"], "note": ev["note"]})
    t_on, t_pk = pd.to_datetime(out["start"]), pd.to_datetime(out["peak"])
    t_al = pd.to_datetime(al["time_local"]) if len(al) else pd.Series(dtype="datetime64[ns]")
    for name in DETECTORS:
        ts = np.sort(t_al[al["detector"] == name].to_numpy()) if len(al) else np.array([], "datetime64[ns]")
        first, delay = [], []
        for a, b in zip(t_on, t_pk):
            lo = a - pd.Timedelta(minutes=ALARM_MATCH_BEFORE_MIN)
            hi = b + pd.Timedelta(minutes=ALARM_MATCH_AFTER_PEAK_MIN)
            k = np.searchsorted(ts, np.datetime64(lo))
            if k < len(ts) and ts[k] <= np.datetime64(hi):
                t = pd.Timestamp(ts[k])
                first.append(t.strftime(FMT))
                delay.append(round((t - a).total_seconds() / 60, 2))
            else:
                first.append("")
                delay.append(np.nan)
        out[f"first_alarm_{name}"] = first
        out[f"delay_min_{name}"] = delay
    return out


def sweeps_table(data):
    cols = ["sweep_id", "date", "time_local", "P_mp_W", "V_mp_V", "V_oc_V", "I_sc_A", "FF",
            "G_Wm2", "G_stable", "T_module_C", "T_cell_C", "flags", "usable"]
    points, summary = gamma.read_sweeps(data)
    if not len(points):
        return pd.DataFrame(columns=cols)
    s = gamma.sweep_parameters(points, summary)
    usable = s["sweep_id"].isin(gamma.usable_sweeps(s)["sweep_id"])
    out = pd.DataFrame({"sweep_id": s["sweep_id"], "date": s["t_local"].dt.strftime("%Y-%m-%d"),
                        "time_local": s["t_local"].dt.strftime(FMT),
                        "P_mp_W": s["P_mp"].round(3), "V_mp_V": s["V_mp"].round(3),
                        "V_oc_V": s["V_oc"].round(3), "I_sc_A": s["I_sc"].round(4),
                        "FF": s["FF"].round(4), "G_Wm2": s["G"].round(1),
                        "G_stable": s["G_stable"], "T_module_C": s["T_module"].round(2),
                        "T_cell_C": s["T_cell"].round(2), "flags": s["flags"], "usable": usable})
    return out.sort_values("time_local").reset_index(drop=True)


def run(data, out="out/powerbi", source="logger", log=print):
    data, out = pathlib.Path(data), pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    df = ingest.read_logger(data)
    frame = ingest.detector_frame(df)
    excluded = tests = None
    if (data / "markers.csv").exists():
        m, _ = ingest.read_markers(data / "markers.csv")
        joined, _ = ingest.join_logsheets(m, data / "logsheets")
        excluded, tests = ingest.excluded_windows(joined), ingest.controlled_tests(joined)
    ev = events.label(frame, excluded, tests)
    al = alarm_onsets(df)
    sw = sweeps_table(data)
    tables = {"daily_summary": daily_summary(df, ev, al, sw), "spikes": spikes_table(ev, al),
              "sweeps": sw, "alarms": al}
    for t in tables.values():
        t.insert(0, "source", source)    # "synthetic" for --demo: never quote those numbers
    for name, t in tables.items():
        t.to_csv(out / f"{name}.csv", index=False, encoding="utf-8")
        log(f"{name + '.csv':20s} {len(t):6d} rows")
    log(f"-> {out}")
    return tables


def demo(out="out/powerbi", n_days=20, seed=5, log=print):
    """SYNTHETIC files (analysis/demo_synth.py) exported, to set up the report before the
    rig logs real days. Every number in it is synthetic."""
    from analysis import demo_synth
    root = pathlib.Path(out).parent / "powerbi_demo_files"
    demo_synth.build(seed, n_days, root)
    return run(root / "raw", out, source="synthetic", log=log)


def main():
    ap = argparse.ArgumentParser(description="Export Power BI CSV tables from logger files")
    ap.add_argument("--data", help="folder with the SD-card files")
    ap.add_argument("--out", default="out/powerbi")
    ap.add_argument("--demo", action="store_true", help="use SYNTHETIC demo files")
    a = ap.parse_args()
    if a.demo:
        demo(a.out)
    elif a.data:
        run(a.data, a.out)
    else:
        ap.error("give --data <folder> or --demo")


if __name__ == "__main__":
    main()
