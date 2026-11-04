"""Synthetic days for the spike-detection pipeline. EVERY VALUE IS SYNTHETIC.

One day = 06:00-18:00 local at 10 s. For each day:
  1. In-plane clear-sky irradiance (pvlib Ineichen, Makkah, tilt 21 deg, south)
     x a daily haze factor x cloud transients.
  2. Ambient temperature and humidity: smooth daily curves.
  3. Wind: diurnal mean + slow random variation (Ornstein-Uhlenbeck) + injected
     lulls (the spike driver).
  4. True module temperature from sim/thermal.py.
  5. Sensor readings: noise, offsets, quantisation; PV power from a simple
     temperature-corrected model (gamma is illustrative, not our module's).

Events (events.csv):
  spike           lull whose noise-free steady-state effect >= SPIKE_EFFECT_C
  minor_lull      a weaker lull: neutral in scoring
  irradiance_rise the RISE_WINDOW_MIN after a cloud ends: alarms here are false
  mixed           a lull overlapping a cloud or its rise window: excluded
  natural_spike   the background wind alone (no injected lull) gives a
                  steady-state effect >= SPIKE_EFFECT_C vs its slow diurnal
                  baseline, averaged over NATURAL_WINDOW_MIN; scored separately
  natural_minor   same, between NATURAL_MINOR_C and SPIKE_EFFECT_C: neutral
  A natural episode overlapping any other event turns that event "mixed".

The labels use only the generator's own ground truth (wind with and without
the lull), never any detector's threshold, so they do not favour a detector.

Run:  python -m data.synth --seed 0      -> out/synth/seed0/{days,events}.csv
All parameters and their source tags are in config.py.
"""
import argparse
import pathlib

import numpy as np
import pandas as pd
import pvlib

import config
from data.schema import BACK_SENSORS, COLUMNS, EVENT_COLUMNS
from sim import thermal

DT = config.DT_S


# --- building blocks -------------------------------------------------------

def clear_sky_poa(times):
    """Clear-sky irradiance in the plane of the module, W/m2."""
    loc = pvlib.location.Location(config.LAT, config.LON, tz=config.TZ,
                                  altitude=config.ALTITUDE_M)
    cs = loc.get_clearsky(times, model="ineichen")
    sp = loc.get_solarposition(times)
    poa = pvlib.irradiance.get_total_irradiance(
        config.TILT_DEG, config.AZIMUTH_DEG, sp.apparent_zenith, sp.azimuth,
        cs.dni, cs.ghi, cs.dhi)
    return poa.poa_global.fillna(0.0).clip(lower=0.0).to_numpy()


def ramp_window(n, start, end, ramp):
    """0 outside [start, end], 1 inside, ramping linearly over `ramp` samples
    just after `start` and just before `end` (so start = onset)."""
    k = np.arange(n)
    up = np.clip((k - start) / ramp, 0.0, 1.0)
    down = np.clip((end - k) / ramp, 0.0, 1.0)
    return np.minimum(up, down)


def ou_process(rng, n, sigma, tau_s):
    """Ornstein-Uhlenbeck noise: zero mean, std sigma, correlation time tau."""
    a = np.exp(-DT / tau_s)
    x = np.empty(n)
    x[0] = rng.normal(0.0, sigma)
    w = rng.normal(0.0, sigma * np.sqrt(1.0 - a * a), n)
    for k in range(1, n):
        x[k] = a * x[k - 1] + w[k]
    return x


def place_intervals(rng, count, durations, lo, hi, gap):
    """Random non-overlapping [start, end) sample intervals inside [lo, hi)."""
    placed = []
    for dur in durations[:count]:
        for _ in range(200):
            s = int(rng.integers(lo, hi - dur))
            if all(s >= e + gap or s + dur + gap <= s0 for s0, e in placed):
                placed.append((s, s + dur))
                break
    return sorted(placed)


def overlaps(a0, a1, b0, b1):
    return a0 <= b1 and b0 <= a1


def label_events(lulls, clouds):
    """Turn ground-truth lulls and clouds into labelled events.

    lulls:  list of dicts {start, end, effect}
    clouds: list of dicts {start, end, rise_end}   (sample indices)
    A lull that overlaps a cloud OR its rise window becomes "mixed" together
    with that cloud: the cloud's temperature effect would otherwise be
    credited to (or hide) the lull.
    """
    events, used = [], set()
    for lull in lulls:
        hit = [i for i, c in enumerate(clouds)
               if overlaps(lull["start"], lull["end"], c["start"], c["rise_end"])]
        if hit:
            used.update(hit)
            s = min([lull["start"]] + [clouds[i]["start"] for i in hit])
            e = max([lull["end"]] + [clouds[i]["rise_end"] for i in hit])
            events.append(dict(start=s, end=e, cls="mixed", effect=lull["effect"]))
        else:
            cls = "spike" if lull["effect"] >= config.SPIKE_EFFECT_C else "minor_lull"
            events.append(dict(start=lull["start"], end=lull["end"], cls=cls,
                               effect=lull["effect"]))
    for i, c in enumerate(clouds):
        if i not in used:
            events.append(dict(start=c["end"], end=c["rise_end"],
                               cls="irradiance_rise", effect=np.nan))
    return sorted(events, key=lambda ev: ev["start"])


def natural_episodes(effect, window):
    """Episodes where the background wind alone heats the module.

    effect: per-sample noise-free steady-state effect of the background wind
            vs its slow baseline, degC.
    A window of `window` samples qualifies when its mean effect is at least
    NATURAL_MINOR_C. Overlapping qualifying windows form one episode; its
    effect is that of its strongest window, which sets the class.
    """
    if len(effect) < window:
        return []
    c = np.concatenate([[0.0], np.cumsum(effect)])
    mean = (c[window:] - c[:-window]) / window      # mean[k] = effect[k:k+window]
    ok = mean >= config.NATURAL_MINOR_C
    episodes, k = [], 0
    while k < len(ok):
        if not ok[k]:
            k += 1
            continue
        k0 = k
        while k < len(ok) and ok[k]:
            k += 1
        best = float(mean[k0:k].max())
        cls = "natural_spike" if best >= config.SPIKE_EFFECT_C else "natural_minor"
        episodes.append(dict(start=k0, end=k - 1 + window - 1, cls=cls, effect=best))
    return episodes


def add_natural(events, natural):
    """Add natural episodes; one overlapping another event makes it "mixed"."""
    events = [dict(e) for e in events]
    for nat in natural:
        hit = [e for e in events if overlaps(nat["start"], nat["end"], e["start"], e["end"])]
        if not hit:
            events.append(dict(nat))
            continue
        for e in hit:
            e["start"], e["end"] = min(e["start"], nat["start"]), max(e["end"], nat["end"])
            e["cls"] = "mixed"
    return sorted(events, key=lambda ev: ev["start"])


def quantise(x, lsb):
    return np.round(x / lsb) * lsb


# --- one day -----------------------------------------------------------------

def simulate_day(date, rng, sensor_offsets):
    times = pd.date_range(f"{date} {config.DAY_START}", f"{date} {config.DAY_END}",
                          freq=f"{int(DT)}s", tz=config.TZ)
    n = len(times)
    hour = (times.hour + times.minute / 60 + times.second / 3600).to_numpy()
    per_min = int(60 / DT)
    lo = int(np.searchsorted(times.strftime("%H:%M:%S"), config.EVENT_HOURS[0]))
    hi = int(np.searchsorted(times.strftime("%H:%M:%S"), config.EVENT_HOURS[1]))

    # 1. irradiance
    clearness = np.full(n, rng.uniform(*config.DAY_CLEARNESS))
    n_cl = rng.poisson(config.CLOUDS_PER_DAY)
    cl_dur = rng.integers(config.CLOUD_DURATION_MIN[0] * per_min,
                          config.CLOUD_DURATION_MIN[1] * per_min + 1, n_cl)
    clouds = []
    for s, e in place_intervals(rng, n_cl, cl_dur, lo, hi, 0):
        depth = rng.uniform(*config.CLOUD_DEPTH)
        clearness *= 1.0 - depth * ramp_window(n, s, e, config.CLOUD_RAMP_S / DT)
        clouds.append(dict(start=s, end=e,
                           rise_end=min(e + config.RISE_WINDOW_MIN * per_min, n - 1)))
    g = clear_sky_poa(times) * clearness

    # 2. ambient air (sinusoid, peak at 15:00)
    phase = np.sin(2 * np.pi * (hour - 9.0) / 24.0)
    ta = rng.uniform(*config.TA_DAY_MEAN) + config.TA_AMP * phase
    rh = config.RH_MEAN - config.RH_AMP * phase

    # 3. wind: base, then lulls on top
    slow = config.WIND_MEAN + config.WIND_DIURNAL_AMP * np.sin(np.pi * (hour - 6.0) / 12.0)
    base = slow + ou_process(rng, n, config.WIND_OU_SIGMA, config.WIND_OU_TAU_S)
    v_no_lull = np.maximum(base, config.WIND_MIN)
    n_lu = int(rng.integers(config.LULLS_PER_DAY[0], config.LULLS_PER_DAY[1] + 1))
    lu_dur = rng.integers(config.LULL_DURATION_MIN[0] * per_min,
                          config.LULL_DURATION_MIN[1] * per_min + 1, n_lu)
    drop = np.zeros(n)
    lulls = []
    for s, e in place_intervals(rng, n_lu, lu_dur, lo, hi,
                                config.EVENT_GAP_MIN * per_min):
        drop += rng.uniform(*config.LULL_DROP) * ramp_window(
            n, s, e, config.LULL_RAMP_S / DT)
        lulls.append(dict(start=s, end=e))
    v = np.maximum(base - drop, config.WIND_MIN)

    # label: noise-free steady-state effect of the lull, averaged over it
    for lull in lulls:
        sl = slice(lull["start"], lull["end"] + 1)
        lull["effect"] = float(np.mean(
            thermal.steady_state(g[sl], ta[sl], v[sl])
            - thermal.steady_state(g[sl], ta[sl], v_no_lull[sl])))
    events = label_events(lulls, clouds)
    natural_effect = (thermal.steady_state(g, ta, v_no_lull)
                      - thermal.steady_state(g, ta, np.maximum(slow, config.WIND_MIN)))
    events = add_natural(events, natural_episodes(
        natural_effect, config.NATURAL_WINDOW_MIN * per_min))

    # 4. true module (back-sheet) temperature
    t_mod = thermal.simulate(ta[0], g, ta, v)

    # 5. sensor readings
    df = pd.DataFrame({"date": times.strftime("%Y-%m-%d"),
                       "time": times.strftime("%H:%M:%S")})
    for name, off in zip(BACK_SENSORS, sensor_offsets):
        df[name] = quantise(t_mod + off + rng.normal(0, config.DS18B20_NOISE_C, n),
                            config.DS18B20_LSB_C)
    df["T_ambient"] = np.round(ta + rng.normal(0, config.TA_NOISE_C, n), 2)
    df["humidity"] = np.round(np.clip(rh + rng.normal(0, config.RH_NOISE, n), 0, 100), 1)
    df["irradiance"] = np.clip(np.round(g + rng.normal(0, config.G_NOISE_W, n)), 0, None)

    t_cell = t_mod + config.CELL_MINUS_BACK_C * g / 1000.0
    dt25 = t_cell - 25.0
    p = np.clip(config.P_STC_W * g / 1000.0 * (1.0 + config.GAMMA_P * dt25), 0, None)
    vmp = config.VMP_STC_V * (1.0 + config.BETA_VMP * dt25) * (
        1.0 + config.VMP_LOG_G * np.log(np.maximum(g, 1.0) / 1000.0))
    vmp = np.where(g >= 1.0, np.clip(vmp, 0.1, None), 0.0)   # dark -> 0 V
    volt = vmp + rng.normal(0, config.V_NOISE, n)
    curr = np.divide(p, vmp, out=np.zeros(n), where=vmp > 0) + rng.normal(0, config.I_NOISE, n)
    df["voltage"] = np.round(volt, 3)
    df["current"] = np.round(curr, 4)
    df["power"] = np.round(df["voltage"] * df["current"], 3)
    df["wind"] = np.clip(np.round(v + rng.normal(0, config.WIND_NOISE, n), 1), 0, None)

    ev = pd.DataFrame([{
        "start": times[e["start"]].strftime("%Y-%m-%d %H:%M:%S"),
        "end": times[e["end"]].strftime("%Y-%m-%d %H:%M:%S"),
        "class": e["cls"],
        "effect_C": round(e["effect"], 2) if np.isfinite(e["effect"]) else np.nan,
        "note": "synthetic",
    } for e in events], columns=EVENT_COLUMNS)
    return df[COLUMNS], ev


def generate(seed, n_days=config.N_DAYS, start_date=config.START_DATE):
    """All days for one seed. Each day has its own child RNG, so adding days
    does not change the days already generated."""
    root = np.random.SeedSequence(seed)
    sensor_rng, *day_seeds = (np.random.default_rng(s) for s in root.spawn(n_days + 1))
    offsets = sensor_rng.normal(0.0, config.DS18B20_OFFSET_C, config.N_BACK_SENSORS)
    dates = pd.date_range(start_date, periods=n_days, freq="D").strftime("%Y-%m-%d")
    days, events = zip(*(simulate_day(d, r, offsets) for d, r in zip(dates, day_seeds)))
    return (pd.concat(days, ignore_index=True),
            pd.concat(events, ignore_index=True))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--days", type=int, default=config.N_DAYS)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = pathlib.Path(a.out or f"out/synth/seed{a.seed}")
    out.mkdir(parents=True, exist_ok=True)
    days, events = generate(a.seed, a.days)
    days.to_csv(out / "days.csv", index=False)
    events.to_csv(out / "events.csv", index=False)
    print(f"{len(days)} rows, {len(events)} events -> {out}")
    print(events["class"].value_counts().to_string())


if __name__ == "__main__":
    main()
