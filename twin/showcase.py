"""Presentation data of the twin: ONE simulated day, used twice on the SCADA page /rooftop.
SIMULATION, placeholder parameters.

    python -m twin.showcase                          the scenario day -> out/twin/showcase/
    python -m twin.showcase --data <SD copy>         weather of a LOGGED day instead
    python -m twin.showcase --data <SD copy> --date 2027-01-12
    python -m twin.showcase --story-detector nasim   the story with NASIM-GBT (see below)

It writes three files (out/ is not in git):
  compare.json   the five controllers on the same day, the same rooftop and the same sensor
                 noise: one value a minute (the controller comparison of the page)
  story.json     a window of that day around its deepest cloud, every 10 s step, with every
                 module temperature: a run of the spike-triggered controller, cut into the
                 chapters of the story mode (cloud, cooling, sun back, spike, alarm, spray)
  compare.png    the comparison as a figure for slides, labelled SIMULATION

Nothing is invented for the story: it is a cut-out of a run of the spike-triggered
controller on the comparison's day, rooftop and sensor noise. Its detector is the
THRESHOLD RULE by default (rise >= 2.5 degC in 10 min, the paper's baseline a), because
that rule waits for the rise: the picture shows the modules climb, then the alarm, then
the spray. NASIM-GBT (--story-detector nasim) raises its alarm about 30 s after the sun is
back, before a rise can be seen; that story has the alarm and a shorter burst right at the
sun's return. The comparison always uses NASIM-GBT for the spike-triggered strategy when
its model is on this computer. The captions name the detector that ran.

The twin applies ONE irradiance to all modules (one
pyranometer; a cloud edge crosses a roof of this size in less than one 10 s sample), so
after a cloud EVERY string re-heats and the alarms of the strings follow each other within
seconds. The page draws the shadow sliding over the roof during the logged irradiance
ramp; that slide is drawing, the irradiance series is the model's.

The scenario day [SCENARIO] is built from the blocks of data/synth.py (clear sky over
Makkah on the module plane, cloud ramps of 60 s, the same ambient and wind models) with
three clouds placed by hand, so that the story has one clean cloud near noon. It is a
choice for a demonstration, not a typical or a measured day. With --data the weather
(irradiance, ambient temperature, humidity) comes from a day the rig logged; the rooftop,
the spray and the controllers stay simulated.

Chapter boundaries, camera views and durations are data (story.json "chapters"); the
caption TEXT is in scada/web/story_captions.json, the one place to edit or translate it.
"""
import argparse
import datetime
import json
import pathlib

import numpy as np
import pandas as pd

import config
from twin import SIM_NOTICE, VIEW_LABEL, compare, controllers, params, rooftop

OUT = pathlib.Path("out/twin/showcase")
SCENARIO_DATE = "2026-11-21"                    # [SCENARIO] a day of the synthetic test season
SCENARIO_SEED = 7                               # wind noise of the scenario day
SCENARIO_CLEARNESS = 0.95                       # [SCENARIO] haze factor of the day
# [SCENARIO] clouds: (local hour of the onset, minutes, share of the irradiance removed)
SCENARIO_CLOUDS = ((9.7, 8, 0.55), (11.9, 10, 0.70), (14.3, 6, 0.50))
SCENARIO_TA_MEAN = 26.0                         # [SCENARIO] degC, middle of config.TA_DAY_MEAN
DIP_LEVEL = 0.80                                # a cloud: irradiance below 80 % of the day's clear level
DIP_MIN_S = 180.0                               # ... for at least 3 minutes
DIP_HOURS = (8.5, 15.5)                         # local hours searched for the story's cloud
LEAD_S, TAIL_S, TAIL_MAX_S = 240.0, 720.0, 1500.0   # story window before / after the cloud
ALARM_SHOW_S = 60.0                             # an alarm stays on the picture at least this long
KEYS = ("none", "continuous", "fixed", "spike", "predictive")


# --- the day ----------------------------------------------------------------------------------

def scenario_day(date=SCENARIO_DATE, seed=SCENARIO_SEED, clouds=SCENARIO_CLOUDS):
    """The demonstration day as a weather table in the logger's schema (local time)."""
    from data import synth
    dt = config.DT_S
    times = pd.date_range(f"{date} {config.DAY_START}", f"{date} {config.DAY_END}",
                          freq=f"{int(dt)}s", tz=config.TZ)
    n = len(times)
    hour = (times.hour + times.minute / 60 + times.second / 3600).to_numpy()
    clearness = np.full(n, SCENARIO_CLEARNESS)
    for start_h, minutes, depth in clouds:
        s = int(np.searchsorted(hour, start_h))
        clearness *= 1.0 - depth * synth.ramp_window(n, s, s + int(minutes * 60 / dt),
                                                     config.CLOUD_RAMP_S / dt)
    phase = np.sin(2 * np.pi * (hour - 9.0) / 24.0)
    rng = np.random.default_rng(seed)
    wind = np.maximum(config.WIND_MEAN + config.WIND_DIURNAL_AMP * np.sin(np.pi * (hour - 6.0) / 12.0)
                      + synth.ou_process(rng, n, config.WIND_OU_SIGMA, config.WIND_OU_TAU_S),
                      config.WIND_MIN)
    table = pd.DataFrame({
        "date": times.strftime("%Y-%m-%d"), "time": times.strftime("%H:%M:%S"),
        "irradiance": np.round(synth.clear_sky_poa(times) * clearness),
        "T_ambient": np.round(SCENARIO_TA_MEAN + config.TA_AMP * phase, 2),
        "humidity": np.round(config.RH_MEAN - config.RH_AMP * phase, 1),
        "wind": np.round(wind, 1)})
    note = (f"scenario day [SCENARIO] {date}: clear sky over Makkah (data/synth.py), "
            f"{len(clouds)} clouds placed by hand; not a measured day")
    return table, note


def logged_day(folder, date=None):
    """Weather of one day the rig logged, on a regular 10 s grid: (table, note). Without
    `date`, the day with the deepest cloud in the middle of the day is taken."""
    from analysis import ingest
    f = ingest.read_logger(folder)
    f = f[f["segment"] >= 0]
    if date is None:
        best = (-1.0, None)
        for d, day in f.groupby("date"):
            w = rooftop.weather_days(_regular(day)[0])[0]
            dip = find_cloud(w, clear_sky(w))
            score = 0.0 if dip is None else dip["depth"] * (dip["k1"] - dip["k0"])
            if score > best[0]:
                best = (score, d)
        date = best[1]
    day = f[f["date"] == date]
    if not len(day):
        raise SystemExit(f"no usable logger rows for {date} in {folder}")
    table, filled = _regular(day)
    note = (f"weather LOGGED by the rig on {date} ({folder}): irradiance, ambient temperature"
            f"{', humidity' if 'humidity' in day else ''}; "
            f"{'wind as logged' if table['wind'].notna().any() else 'no wind logged'}"
            + (f"; {filled} missing 10 s rows interpolated" if filled else "")
            + ". The rooftop, the spray and the controllers are simulated")
    return table, note


def _regular(day):
    """A logged day on the regular 10 s grid the plant needs. Returns (table, rows filled)."""
    t = pd.to_datetime(day["t_local"]).dt.round(f"{int(config.DT_S)}s")
    cols = [c for c in ("irradiance", "T_ambient", "humidity", "wind") if c in day]
    d = day[cols].astype(float).set_index(t)
    d = d[~d.index.duplicated()]
    grid = pd.date_range(d.index[0], d.index[-1], freq=f"{int(config.DT_S)}s")
    full = d.reindex(grid)
    filled = int(full["irradiance"].isna().sum())
    wind = full["wind"] if "wind" in full else pd.Series(np.nan, index=grid)
    full = full.interpolate(limit_direction="both")
    return pd.DataFrame({"date": grid.strftime("%Y-%m-%d"), "time": grid.strftime("%H:%M:%S"),
                         "irradiance": full["irradiance"].to_numpy(),
                         "T_ambient": full["T_ambient"].to_numpy(),
                         "humidity": (full["humidity"].to_numpy() if "humidity" in full
                                      else np.full(len(grid), np.nan)),
                         "wind": wind.to_numpy()}), filled


def clear_sky(w):
    """Clear-sky irradiance on the module plane for every sample of a Weather, W/m2."""
    from data import synth
    return synth.clear_sky_poa(pd.DatetimeIndex(w.t_local).tz_localize(config.TZ))


def utc_seconds(w):
    t = pd.DatetimeIndex(w.t_local).tz_localize(config.TZ)
    return np.asarray((t - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta("1s"), dtype=int)


def find_cloud(w, g_clear):
    """The deepest cloud of the middle of the day: where the irradiance stays below
    DIP_LEVEL of the day's clear level for at least DIP_MIN_S. Returns the sample indices
    {start, dark, sun, back, k0, k1, depth} or None. start = last clear sample before the
    cloud, dark = shade complete, sun = light starts to return, back = clear again."""
    hour = np.asarray(w.minute) / 60.0
    lit = (g_clear > 200.0) & (hour >= DIP_HOURS[0]) & (hour <= DIP_HOURS[1])
    if lit.sum() < 100:
        return None
    r = np.where(g_clear > 50.0, w.g / np.maximum(g_clear, 1.0), np.nan)
    base = float(np.nanpercentile(r[lit], 90))
    low = lit & (r < DIP_LEVEL * base)
    best, k, n = None, 0, len(low)
    min_len = int(DIP_MIN_S / config.DT_S)
    while k < n:
        if not low[k]:
            k += 1
            continue
        e = k
        while e + 1 < n and low[e + 1]:
            e += 1
        depth = 1.0 - float(np.nanmin(r[k:e + 1])) / base
        if e - k + 1 >= min_len and (best is None or depth * (e - k) > best["depth"] * (best["k1"] - best["k0"])):
            best = {"k0": k, "k1": e, "depth": depth}
        k = e + 1
    if best is None:
        return None
    k0, k1 = best["k0"], best["k1"]
    floor = float(np.nanmin(r[k0:k1 + 1])) / base
    dark_level = (floor + 0.1 * (1.0 - floor)) * base       # 90 % of the way down
    clear_level = 0.97 * base
    start = k0
    while start > 0 and r[start] < clear_level:
        start -= 1
    back = k1
    while back < n - 1 and r[back] < clear_level:
        back += 1
    dark = next((i for i in range(k0, k1 + 1) if r[i] <= dark_level), k0)
    sun = next((i for i in range(k1, k0 - 1, -1) if r[i] <= dark_level), k1)
    return {**best, "start": int(start), "dark": int(dark), "sun": int(sun), "back": int(back),
            "clear_level": base}


# --- the controllers --------------------------------------------------------------------------

def spike_controller(detector="auto", nasim_run=controllers.NASIM_RUN):
    """(controller, what its detector is). auto = NASIM-GBT when the paper pipeline's run
    is on this computer, else the threshold rule with the team's fixed rise."""
    if detector in ("auto", "nasim"):
        try:
            return (controllers.SpikeTriggered(controllers.nasim_factory(nasim_run),
                                               "Spike-triggered (NASIM-GBT)"),
                    "NASIM-GBT, trained by the paper pipeline on SYNTHETIC data")
        except FileNotFoundError:
            if detector == "nasim":
                raise
    return (controllers.SpikeTriggered(controllers.threshold_factory(config.THR_RISE_C),
                                       "Spike-triggered (threshold rule)"),
            f"threshold rule, rise >= {config.THR_RISE_C:g} degC in "
            f"{config.DET_WINDOW_MIN:g} min (NASIM-GBT's model is not on this computer: "
            "python -m analysis.demo_synth)")


def strategies(p, detector="auto", nasim_run=controllers.NASIM_RUN):
    """[(key, controller, note)] in the order of twin/compare.py."""
    spike, det = spike_controller(detector, nasim_run)
    return [("none", controllers.NoCooling(), "the reference: nothing is sprayed"),
            ("continuous", controllers.Continuous(), "every valve open while there is daylight"),
            ("fixed", controllers.FixedThreshold(p.fixed_on_c, p.fixed_off_c),
             f"per zone: on at {p.fixed_on_c:g} degC, off at {p.fixed_off_c:g} degC"),
            ("spike", spike, f"per zone: a burst after a detector alarm, if the net-power rule "
                             f"allows it. Detector: {det}"),
            ("predictive", controllers.Predictive(),
             "per zone: spray while the expected power gain exceeds the pump power")]


def run_day(p, w, detector="auto", roof_seed=compare.ROOF_SEED, noise_seed=compare.NOISE_SEED,
            log=print):
    """Every strategy over the day, on the same rooftop and with the same sensor noise.
    Returns (roof, {key: (controller, note, table, summary)})."""
    roof = rooftop.build(p, roof_seed)
    runs = {}
    for key, ctrl, note in strategies(p, detector):
        table, summary = rooftop.simulate_day(roof, w, ctrl, noise_seed)
        runs[key] = (ctrl, note, table, summary)
        log(f"  {ctrl.name:34s} net {summary['e_net_wh'] / 1000:7.2f} kWh, "
            f"pump {summary['e_pump_wh'] / 1000:5.2f} kWh, water {summary['water_used_l']:6.0f} L")
    return roof, runs


def run_story(roof, w, detector, keep, noise_seed=compare.NOISE_SEED):
    """The spike-triggered controller once more for the story, keeping every module
    temperature of the samples keep[0]..keep[1]. Returns (controller, detector note,
    table, {sample: temperatures})."""
    ctrl, det = spike_controller(detector)
    modules = {}

    def hook(k, plant, rec):
        if keep[0] <= k <= keep[1]:
            modules[k] = rec["t_modules"]
    table, _ = rooftop.simulate_day(roof, w, ctrl, noise_seed, on_step=hook)
    return ctrl, det, table, modules


# --- the comparison ---------------------------------------------------------------------------

def _header(p, w, weather_note, kind):
    return {"simulation": True, "label": VIEW_LABEL, "notice": SIM_NOTICE,
            "generated": datetime.datetime.now().isoformat(timespec="seconds"),
            "date": w.date, "weather": weather_note, "weather_kind": kind,
            "parameter_tags": p.tags(), "dt_s": float(p.dt_s),
            "n_strings": int(p.n_strings), "modules_per_string": int(p.modules_per_string)}


def comparison(p, w, runs, weather_note, kind, window=None, bucket_s=60):
    """compare.json: per strategy one value a minute and the day's totals."""
    s, h = int(p.n_strings), p.dt_s / 3600.0
    per = int(bucket_s / p.dt_s)
    t_utc = utc_seconds(w)
    n = (len(w) // per) * per
    mean = lambda x: np.asarray(x, dtype=float)[:n].reshape(-1, per).mean(axis=1)   # noqa: E731
    last = lambda x: np.asarray(x, dtype=float)[:n].reshape(-1, per)[:, -1]         # noqa: E731
    r = lambda x, d=2: [round(float(v), d) for v in x]                              # noqa: E731
    ref = runs["none"][2]
    p_ref = ref["p_dc"].to_numpy()
    base = runs["none"][3]["e_net_wh"]
    out = {**_header(p, w, weather_note, kind), "bucket_s": bucket_s,
           "t_utc": [int(v) for v in t_utc[:n].reshape(-1, per)[:, 0]],
           "g": r(mean(w.g), 0), "ta": r(mean(w.ta), 1),
           "window_utc": None if window is None else [int(t_utc[window[0]]), int(t_utc[window[1]])],
           "controllers": []}
    for key in KEYS:
        ctrl, note, t, summ = runs[key]
        p_net = (t["p_dc"] - t["p_pump"] - t["p_valves"]).to_numpy()
        temp = t[[f"t_string_{z + 1}" for z in range(s)]].to_numpy().mean(axis=1)
        valves = t[[f"valve_{z + 1}" for z in range(s)]].to_numpy().sum(axis=1)
        totals = {k: round(float(v), 3) for k, v in summ.items()}
        totals["gain_wh"] = round(summ["e_net_wh"] - base, 3)
        totals["gain_rel"] = round(summ["e_net_wh"] / base - 1.0, 6) if base > 0 else None
        out["controllers"].append({
            "key": key, "name": ctrl.name, "note": note,
            "t_mean": r(mean(temp)), "p_net": r(mean(p_net), 1),
            "gain_wh": r(last(np.cumsum(p_net - p_ref) * h), 2),
            "pump_wh": r(last(np.cumsum(t["p_pump"].to_numpy() + t["p_valves"].to_numpy()) * h), 2),
            "water_l": r(last(np.cumsum(t["water_used_l"].to_numpy())), 2),
            "zones_open": r(mean(valves), 2), "totals": totals})
    return out


# --- the story --------------------------------------------------------------------------------

def story(p, w, roof, run, ref, dip, window, g_clear, weather_note, kind):
    """story.json: the window of the spike-triggered run `run` (run_story) with chapters
    and caption facts; `ref` is the table of the same day without cooling."""
    k0, k1 = window
    s, dt = int(p.n_strings), float(p.dt_s)
    ctrl, detector_note, t, modules = run
    sl = slice(k0, k1 + 1)
    col = lambda name: t[[f"{name}_{z + 1}" for z in range(s)]].to_numpy()[sl]      # noqa: E731
    r = lambda x, d=2: np.round(np.asarray(x, dtype=float), d).tolist()             # noqa: E731
    valve, alarm = col("valve") > 0, col("alarm") > 0
    n = k1 - k0 + 1
    shown = np.zeros((n, s), dtype=bool)        # an alarm stays visible while its burst runs
    hold = int(ALARM_SHOW_S / dt)
    for z in range(s):
        for i in np.flatnonzero(alarm[:, z]):
            e = i + hold
            while e < n - 1 and valve[min(e, n - 1), z]:
                e += 1
            shown[i:min(e, n - 1) + 1, z] = True
    p_dc, p_pump, p_valves = (t[c].to_numpy()[sl] for c in ("p_dc", "p_pump", "p_valves"))
    p_ref = ref["p_dc"].to_numpy()[sl]
    h = dt / 3600.0
    mods = np.stack([modules[k] for k in range(k0, k1 + 1)])
    t_ref = ref[[f"t_string_{z + 1}" for z in range(s)]].to_numpy()[sl].mean(axis=1)
    out = {
        **_header(p, w, weather_note, kind),
        "controller": ctrl.name, "alarm_detector": detector_note,
        "sensor_idx": roof.sensor_idx.tolist(), "soiling": np.round(roof.soiling, 4).tolist(),
        "wind_factor": np.round(roof.wind_factor, 3).tolist(),
        "window": [int(k0), int(k1)],
        "t_utc": [int(v) for v in utc_seconds(w)[sl]],
        "g": r(w.g[sl], 0), "g_clear": r(g_clear[sl], 0), "ta": r(w.ta[sl], 1),
        "rh": r(w.rh[sl], 0), "wind": [None if not np.isfinite(v) else round(float(v), 1) for v in w.wind[sl]],
        "modules": np.round(mods, 1).tolist(),
        "t_mean_ref": r(t_ref),
        "strings": {"p": r(col("p_string"), 1), "v": r(col("v_string"), 1), "i": r(col("i_string"), 3),
                    "t_zone": r(col("t_zone")), "t_max": r(col("t_string_max")),
                    "bypassed": col("bypassed").astype(int).tolist(),
                    "valve": valve.astype(int).tolist(), "alarm": alarm.astype(int).tolist(),
                    "alarm_shown": shown.astype(int).tolist()},
        "p_dc": r(p_dc, 1), "p_ref": r(p_ref, 1), "p_pump": r(p_pump, 1), "p_valves": r(p_valves, 1),
        "duty": r(t["duty"].to_numpy()[sl]), "flow_lpm": r(t["flow_lpm"].to_numpy()[sl], 1),
        "t_tank": r(t["t_tank"].to_numpy()[sl]),
        # sums since the first sample of the window (the story's own counters)
        "water_used_l": r(np.cumsum(t["water_used_l"].to_numpy()[sl]), 3),
        "water_circulated_l": r(np.cumsum(t["water_circulated_l"].to_numpy()[sl]), 2),
        "e_pump_wh": r(np.cumsum(p_pump + p_valves) * h, 3),
        "e_net_wh": r(np.cumsum(p_dc - p_pump - p_valves) * h, 2),
        "e_ref_wh": r(np.cumsum(p_ref) * h, 2),
    }
    out["chapters"], out["facts"] = chapters(out, dip, k0, mods, valve, alarm, dt)
    out["facts"]["detector"] = ("NASIM-GBT" if "NASIM" in ctrl.name else
                                f"threshold rule (rise \u2265 {config.THR_RISE_C:g} \u00b0C in "
                                f"{config.DET_WINDOW_MIN:g} min)")
    out["facts"]["detector_ar"] = ("NASIM-GBT" if "NASIM" in ctrl.name else "threshold rule")
    out["seconds"] = round(sum(c["seconds"] for c in out["chapters"]), 1)
    return out


def chapters(st, dip, k0, mods, valve, alarm, dt):
    """Chapters [{id, i0, i1, seconds, view}] (i = sample index inside the window) and the
    numbers the captions quote. A chapter exists only when its event happened."""
    n = len(st["t_utc"])
    per = lambda seconds: int(round(seconds / dt))                                   # noqa: E731
    start, dark, sun, back = (dip[k] - k0 for k in ("start", "dark", "sun", "back"))
    g, tm = np.asarray(st["g"]), mods.mean(axis=(1, 2))
    al_i = np.flatnonzero(alarm.any(axis=1))
    al_i = al_i[al_i >= sun] if len(al_i) else al_i
    first = int(al_i[0]) if len(al_i) else None
    warm = back if first is None else max(min(back, first), sun)     # before any spray
    facts = {"n_modules": int(mods.shape[1] * mods.shape[2]), "n_strings": int(mods.shape[1]),
             "g_before": round(float(g[:max(start, 1)].mean())), "g_cloud": round(float(g[dark:sun + 1].min())),
             "t_before": round(float(tm[start]), 1), "t_cool": round(float(tm[sun:warm + 1].min()), 1),
             "cloud_min": round((sun - start) * dt / 60.0, 1), "g_back": round(float(g[min(back + 3, n - 1)]))}
    facts["cool_c"] = round(facts["t_before"] - facts["t_cool"], 1)
    cuts = [("intro", max(start - per(30), 1), "overview", 7.0),
            ("cloud", min(dark + per(60), sun), "wide", 9.0),
            ("cool", max(sun - per(30), dark + per(60)), "overview", 10.0),
            ("sun", min(back + per(30), n - 1) if first is None else
             min(back + per(30), max(first - 1, sun + 1)), "wide", 8.0)]
    if first is None:
        end_rise = max(min(back + per(480), n - 1 - per(60)), back)    # room for the last chapter
        cuts += [("spike", end_rise, "overview", 10.0), ("noalarm", n - 1, "presentation", 9.0)]
        facts.update(rise_c=round(float(tm[end_rise] - facts["t_cool"]), 1),
                     rise_min=round((end_rise - sun) * dt / 60.0, 1))
    else:
        group = al_i[al_i <= first + per(120)]                 # the alarms that belong together
        order = [int(z) + 1 for i in group for z in np.flatnonzero(alarm[i])]
        order = list(dict.fromkeys(order))
        z1 = order[0]
        facts.update(first_string=z1, alarm_strings=order, n_alarm=len(order),
                     alarm_span_s=int((group[-1] - group[0]) * dt),
                     rise_c=round(float(st["strings"]["t_zone"][first][z1 - 1]
                                        - min(row[z1 - 1] for row in st["strings"]["t_zone"][sun:first + 1])), 1),
                     rise_min=round((first - sun) * dt / 60.0, 1))
        cuts.append(("spike", first - 1, "overview", 10.0))    # dropped when the alarm is early
        on = np.flatnonzero(valve[first:].any(axis=1)) + first
        on = on[on <= first + per(600)]
        if len(on) and on[0] <= group[-1] + per(60):
            open_i = int(on[0])
            close_i = open_i
            while close_i < n - 1 and valve[close_i + 1].any():
                close_i += 1
            sprayed = np.flatnonzero(valve[open_i:close_i + 1].any(axis=0))
            facts.update(
                spray_strings=[int(z) + 1 for z in sprayed], n_spray=len(sprayed),
                burst_s=int(round(float(valve[open_i:close_i + 1][:, sprayed].sum(axis=0).mean()) * dt)),
                spray_drop_c=round(float(mods[open_i][sprayed].mean()
                                         - mods[min(close_i + 1, n - 1)][sprayed].mean()), 1),
                pump_w=round(float(max(a + b for a, b in zip(st["p_pump"], st["p_valves"])))),
                water_used_l=round(st["water_used_l"][-1], 1),
                water_circulated_l=round(st["water_circulated_l"][-1]),
                e_pump_wh=round(st["e_pump_wh"][-1], 1),
                net_vs_ref_wh=round(st["e_net_wh"][-1] - st["e_ref_wh"][-1], 1),
                after_min=round((n - 1 - close_i) * dt / 60.0))
            # alarm: the first alarm and the first valves; spray: the burst and 30 s after it
            cuts += [("alarm", min(first + 2, close_i), f"string:{z1}", 9.0),
                     ("spray", min(close_i + per(30), n - 1), f"string:{z1}", 12.0),
                     ("result", n - 1, "presentation", 10.0)]
        else:                                                  # the net-power rule refused the burst
            cuts += [("alarm", min(int(group[-1]) + per(60), n - 1), f"string:{z1}", 9.0),
                     ("refused", n - 1, "presentation", 9.0)]
    out, i0 = [], 0
    for cid, i1, view, seconds in cuts:
        i1 = int(min(i1, n - 1))
        if i1 - i0 < 2 and cid not in ("alarm", "spray"):      # nothing to show: its samples
            continue                                           # go to the next chapter
        i1 = max(i1, i0 + 1)
        # look: the cloud's shadow shows on the lit glass of the realistic look; on the heat
        # map colour means temperature and is never darkened
        out.append({"id": cid, "i0": i0, "i1": i1, "seconds": seconds, "view": view,
                    "look": "realistic" if cid in ("cloud", "sun") else "heat"})
        i0 = i1
        if i0 >= n - 1:
            break
    out[-1]["i1"] = n - 1
    return out, facts


def story_window(w, dip, table, p):
    """Samples (k0, k1) of the story: from LEAD_S before the cloud to TAIL_S after the sun is
    back, longer when a burst runs late (at most TAIL_MAX_S)."""
    per = lambda seconds: int(seconds / p.dt_s)                                      # noqa: E731
    s = int(p.n_strings)
    k0 = max(dip["start"] - per(LEAD_S), 0)
    k1 = dip["back"] + per(TAIL_S)
    valve = table[[f"valve_{z + 1}" for z in range(s)]].to_numpy().any(axis=1)
    late = np.flatnonzero(valve[dip["sun"]:dip["back"] + per(TAIL_MAX_S)])
    if len(late):
        k1 = max(k1, dip["sun"] + int(late[-1]) + per(360))
    return k0, min(k1, dip["back"] + per(TAIL_MAX_S), len(w) - 1)


# --- figure for slides ------------------------------------------------------------------------

COLOURS = {"none": "#52514e", "continuous": "#2a78d6", "fixed": "#eb6834", "spike": "#1baf7a",
           "predictive": "#eda100"}        # the page's series colours (scada/web/style.css)


def figure(cmp, path):
    """The comparison as small multiples with one time axis. Every panel says SIMULATION."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    t = pd.to_datetime(np.asarray(cmp["t_utc"]), unit="s", utc=True).tz_convert(config.TZ).tz_localize(None)
    panels = [("t_mean", "mean module temperature, degC"), ("gain_wh", "net energy minus no cooling, Wh"),
              ("pump_wh", "pump + valve energy, Wh"), ("water_l", "water used, L")]
    fig, ax = plt.subplots(len(panels), 1, figsize=(11, 8.6), sharex=True)
    for a, (key, title) in zip(ax, panels):
        for c in cmp["controllers"]:
            if c["key"] == "none" and key != "t_mean":
                continue
            a.plot(t, c[key], color=COLOURS[c["key"]], lw=1.6 if c["key"] != "none" else 1.2,
                   ls="--" if c["key"] == "none" else "-", label=c["name"])
        if key == "gain_wh":
            a.axhline(0.0, color="#52514e", lw=1.0, ls="--")
        a.set_ylabel(title, fontsize=8.5)
        a.grid(alpha=0.3)
        a.spines[["top", "right"]].set_visible(False)
        a.text(0.995, 0.93, "simulated", transform=a.transAxes, ha="right", va="top", fontsize=8,
               color="#52514e")
    ax[0].legend(frameon=False, fontsize=8.5, ncol=5, loc="lower center", bbox_to_anchor=(0.5, 1.0))
    ax[-1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax[-1].set_xlabel(f"local time, {cmp['date']} ({cmp['weather_kind']} weather)")
    fig.suptitle(f"{VIEW_LABEL}   |   five controllers, same day, same rooftop", fontsize=10.5,
                 fontweight="bold", x=0.01, ha="left")
    fig.text(0.01, 0.005, "Rooftop twin (twin/), parameters " + ", ".join(
        f"{n} {tag}" for tag, n in sorted(cmp["parameter_tags"].items())) +
        ". No number here says what cooling gains on a real roof.", fontsize=7.5, color="#52514e")
    fig.tight_layout(rect=(0, 0.02, 1, 0.97))
    fig.savefig(path, dpi=170)
    plt.close(fig)


# --- the command ------------------------------------------------------------------------------

def _json(obj):
    """JSON a browser can read: a value that is not a number (NaN, inf) is written as null."""
    def clean(x):
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [clean(v) for v in x]
        if isinstance(x, float) and not np.isfinite(x):
            return None
        return x
    return json.dumps(clean(obj), separators=(",", ":"), allow_nan=False)


def build(out=OUT, data=None, date=None, detector="auto", story_detector="threshold",
          params_file=None, log=print):
    """Run the day, write the files. Returns {"story": dict | None, "compare": dict}."""
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    p = params.load(params_file, None) if params_file else params.load()
    if data:
        table, note = logged_day(data, date)
        kind = "logged"
    else:
        table, note = scenario_day(date or SCENARIO_DATE)
        kind = "scenario"
    w = rooftop.weather_days(table)[0]
    g_clear = clear_sky(w)
    dip = find_cloud(w, g_clear)
    log(f"SIMULATION: {w.date}, {note}")
    roof, runs = run_day(p, w, detector, log=log)
    window, st = None, None
    if dip is None:
        log("no cloud of at least 3 minutes between 08:30 and 15:30: no story for this day")
        (out / "story.json").unlink(missing_ok=True)
    else:
        keep = (max(dip["start"] - int(LEAD_S / p.dt_s), 0),
                min(dip["back"] + int(TAIL_MAX_S / p.dt_s), len(w) - 1))
        run = run_story(roof, w, story_detector, keep)
        window = story_window(w, dip, run[2], p)
        st = story(p, w, roof, run, runs["none"][2], dip, window, g_clear, note, kind)
        (out / "story.json").write_text(_json(st), encoding="utf-8")
        log(f"story ({run[0].name}): {st['seconds']:.0f} s, chapters "
            + " > ".join(c["id"] for c in st["chapters"]))
    cmp = comparison(p, w, runs, note, kind, window)
    (out / "compare.json").write_text(_json(cmp), encoding="utf-8")
    figure(cmp, out / "compare.png")
    log(f"wrote {out / 'compare.json'}, {out / 'compare.png'}"
        + (f", {out / 'story.json'}" if st else ""))
    log(SIM_NOTICE)
    return {"story": st, "compare": cmp}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", help="folder with the logger's files: use a LOGGED day's weather")
    ap.add_argument("--date", help="the day (YYYY-MM-DD); default: the scenario day, or with "
                                   "--data the logged day with the deepest midday cloud")
    ap.add_argument("--detector", choices=("auto", "nasim", "threshold"), default="auto",
                    help="detector of the spike-triggered strategy in the COMPARISON "
                         "(auto: NASIM-GBT when its model is here)")
    ap.add_argument("--story-detector", choices=("auto", "nasim", "threshold"),
                    default="threshold", help="detector of the STORY's run (default: the "
                    "threshold rule, which shows the rise before the alarm)")
    ap.add_argument("--params", help="overlay file (default: twin/params_measured.json if any)")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    build(a.out, a.data, a.date, a.detector, a.story_detector, a.params)


if __name__ == "__main__":
    main()
