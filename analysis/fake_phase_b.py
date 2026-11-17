"""Write SYNTHETIC Phase B data in the logger's file formats, so that analysis/phase_b.py
and analysis/ect.py are exercised on files, as they will be on a real card.

  /YYYYMMDD.csv      10 s rows with the six Phase B columns after `status`
  /spray_log.csv     boot line, events, refusals (firmware/logger, sprayLog)
  /SWYYYYMMDD.csv, /SSYYYYMMDD.csv   one I-V sweep a minute through the day
  /markers.csv, logsheets/*.csv      the morning cleaning (fake_logger.write_markers)

EVERY VALUE IS SYNTHETIC AND MOST ARE INVENTED. No pump, nozzle, flow sensor or tank
exists; nothing here says how a real spray cools the module. What the generator does is
put effects of a KNOWN size into the data, so that a test can check that the analysis
finds them again:
  * a spray cools the module at a fixed rate while the pump runs and the module then
    returns to its uncooled path with the thermal time constant;
  * the cooler module gives more power through the same illustrative gamma as data/synth.py;
  * the first spray of a day leaves the glass a little cleaner (a step in I_sc / G);
  * the cells lead the back sheet by a first-order lag, which the V_oc of the sweeps shows.
The alarm that opens an event is a plain temperature-rise rule standing in for NASIM. The
events, arms, windows and columns follow the firmware's rules (pv_spray_logic.c), and the
arm of every event comes from analysis.phase_b.allocation(), the firmware's own function.
"""
import math
import pathlib

import numpy as np
import pandas as pd
import pvlib

import config
from analysis import fake_logger, phase_b
from data.schema import BACK_SENSORS, PHASE_B_COLUMNS
from detect.stream import Sample
from detect.threshold import ThresholdDetector

# All [ASSUMPTION, synthetic only] unless a source is named.
SYN_ALARM_RISE_C = 2.0       # the stand-in alarm: rise >= 2 degC in 10 min, no irradiance gate
SYN_COOL_C_PER_S = 0.0805    # 0.70 kW / 8.7 kJ/K: CLAUDE.md key physics, no evaporation [EST]
SYN_TAU_S = 378.0            # 6.3 min [LIT, Herteleer et al. 2023: fixed-mount systems]
SYN_T_WATER_C = 30.0         # the simulation's labelled assumption (CLAUDE.md)
SYN_FLOW_LPM = 1.5
SYN_PUMP_W = 20.0
SYN_PUMP_OFFSET_W = 0.02     # what an INA226 offset would add to every row
SYN_CLEAN_GAIN = 0.006       # I_sc / G step after the first spray of a day
SYN_CELL_LEAD_S = 45.0       # the cells lead the back sheet by this first-order lag
R_HOLD = 1.5                 # ohm, branch A (firmware pv_config.h)
PER_MIN = int(60 / config.DT_S)


def simulate(days, seed=config.PHASE_B_SEED, dry_run=False, block=config.PHASE_B_BLOCK_SIZE,
             cool_c_per_s=SYN_COOL_C_PER_S, clean_gain=SYN_CLEAN_GAIN,
             cell_lead_s=SYN_CELL_LEAD_S, first_id=1, noise_seed=0):
    """days: schema table in LOCAL time (data/synth.py). Returns (rows, events, log):
    rows   the days with the sprays' effect in T1..T5, voltage, current and power, the
           six Phase B columns, `alarm`, and helper columns for the sweeps (cell
           temperature, cleaning factor); events  the generator's own record of every event;
    log    the lines of /spray_log.csv."""
    rng = np.random.default_rng(noise_seed)
    window = config.PHASE_B_EVENT_WINDOW_MIN * PER_MIN
    lockout = config.PHASE_B_LOCKOUT_MIN * PER_MIN
    spray_rows = int(round(config.SPRAY_MAX_S / config.DT_S))
    dt, decay = config.DT_S, math.exp(-config.DT_S / SYN_TAU_S)
    det = ThresholdDetector(rise_c=SYN_ALARM_RISE_C, gate=None)
    out, events, n_event, litres = [], [], first_id - 1, 0.0
    boot = (f"seed={seed} block={block} n_wet={0 if dry_run else first_id - 1} "
            f"n_dry={first_id - 1 if dry_run else 0} dry_run={int(dry_run)} armed=1 "
            f"duty={config.PUMP_DUTY:.3f} max_ms={config.SPRAY_MAX_S * 1000} "
            f"window_ms={config.PHASE_B_EVENT_WINDOW_MIN * 60000} "
            f"lockout_ms={config.PHASE_B_LOCKOUT_MIN * 60000} "
            f"pulses_per_l={config.FLOW_PULSES_PER_L:.2f}")
    log = [("", "", 5000, 0, "boot", boot)]

    for date, d in days.groupby("date", sort=True):
        d = d.reset_index(drop=True)
        n = len(d)
        t = pd.to_datetime(d["date"] + " " + d["time"])
        doy = int(t.dt.day_of_year.iloc[0])
        minute = (t.dt.hour * 60 + t.dt.minute + t.dt.second / 60).to_numpy()
        tb0 = d[BACK_SENSORS].to_numpy(dtype=float)
        g, ta = d["irradiance"].to_numpy(dtype=float), d["T_ambient"].to_numpy(dtype=float)
        cool = np.zeros(n)                       # degC by which the sprays have cooled the module
        clean = np.zeros(n)                      # relative gain of I_sc / G from cleaner glass
        on = np.zeros(n, dtype=bool)             # pump on between row i and row i + 1
        spike_id, assign = np.zeros(n, dtype=int), np.full(n, -1)
        alarm = np.zeros(n, dtype=int)
        det.reset()
        open_until, idle_from, c, cleaned = -1, 0, 0.0, 0.0
        for i in range(n):
            if i:
                c *= decay
                if on[i - 1]:                    # never colder than the water
                    c = min(c + cool_c_per_s * dt, max(tb0[i].mean() - SYN_T_WATER_C - 1.0, 0.0))
            cool[i], clean[i] = c, cleaned
            fired = det.update(Sample(tuple(tb0[i] - c), ta[i], g[i], math.nan, doy,
                                      float(minute[i])))
            alarm[i] = int(fired)
            if i < open_until:
                spike_id[i], assign[i] = spike_id[i - 1], assign[i - 1]
                if i == open_until - 1:
                    ev = events[-1]
                    hhmmss = t.iloc[min(i + 1, n - 1)]
                    log.append((hhmmss, None, None, ev["spike_id"], "closed",
                                f"assign={ev['assign']} sprayed={int(ev['sprayed'])} "
                                f"stop={'max time' if ev['sprayed'] else 'none'} "
                                f"on_ms={ev['spray_s'] * 1000:.0f} litres={ev['litres']:.4f} "
                                f"energy_j={ev['pump_Wh'] * 3600:.1f}"))
                if fired:
                    log.append((t.iloc[i], None, None, 0, "refused", "reason=event open"))
                continue
            if not fired:
                continue
            reason = ("lock-out" if i < idle_from else
                      "low light" if not g[i] >= config.SPRAY_MIN_G_WM2 else
                      "too late in the day" if i + window > n - 1 else None)
            if reason:
                log.append((t.iloc[i], None, None, 0, "refused", f"reason={reason}"))
                continue
            n_event += 1
            arm = phase_b.allocation(phase_b.dry_seed(seed) if dry_run else seed, n_event - 1, block)
            sid = -n_event if dry_run else n_event
            spike_id[i], assign[i] = sid, arm
            open_until, idle_from = i + window, i + window + lockout
            wet = bool(arm) and not dry_run
            if wet:
                on[i:i + spray_rows] = True
                cleaned = clean_gain             # the glass is cleaner from the end of this spray
            events.append({"spike_id": sid, "assign": arm, "date_local": date,
                           "t_alarm": t.iloc[i], "sprayed": bool(arm), "wet": wet,
                           "spray_s": config.SPRAY_MAX_S if arm else 0.0,
                           "litres": SYN_FLOW_LPM * config.SPRAY_MAX_S / 60 if wet else 0.0,
                           "pump_Wh": SYN_PUMP_W * config.SPRAY_MAX_S / 3600 if wet else 0.0,
                           "cool_peak_C": 0.0})
            log.append((t.iloc[i], None, None, sid, "opened",
                        f"assign={arm} t_module={tb0[i].mean() - c:.2f} t_water={SYN_T_WATER_C:.2f}"))
            if arm:
                log.append((t.iloc[min(i + spray_rows, n - 1)], None, None, sid, "stopped",
                            f"reason=max time on_ms={config.SPRAY_MAX_S * 1000} "
                            f"litres={events[-1]['litres']:.4f} "
                            f"energy_j={events[-1]['pump_Wh'] * 3600:.1f}"))
        for ev in events:                        # the largest cooling each event reached
            if ev["date_local"] == date and ev["wet"]:
                k = int(np.flatnonzero(spike_id == ev["spike_id"])[0])
                ev["cool_peak_C"] = float(cool[k:k + window].max())

        # the sprays' effect on what the logger records
        d[BACK_SENSORS] = np.round((tb0 - cool[:, None]) / config.DS18B20_LSB_C) * config.DS18B20_LSB_C
        t_cell0 = tb0.mean(axis=1) + config.CELL_MINUS_BACK_C * g / 1000.0 - 25.0
        p = d["power"].to_numpy(dtype=float) * (1 + clean) * (
            (1 + config.GAMMA_P * (t_cell0 - cool)) / (1 + config.GAMMA_P * t_cell0))
        v = d["voltage"].to_numpy(dtype=float) * (
            (1 + config.BETA_VMP * (t_cell0 - cool)) / (1 + config.BETA_VMP * t_cell0))
        p0 = d["power"].to_numpy(dtype=float)
        d["voltage"] = np.round(v, 3)
        d["current"] = np.round(np.divide(p, v, out=np.zeros(n), where=v > 0), 4)
        d["power"] = np.round(d["voltage"] * d["current"], 3)
        for ev in events:                        # what the spray really added to the window
            if ev["date_local"] == date:
                k = int(np.flatnonzero(spike_id == ev["spike_id"])[0])
                ev["true_gain_Wh"] = float((d["power"].to_numpy()[k:k + window] - p0[k:k + window]).sum()
                                           * dt / 3600.0)

        # the six columns: means over the 10 s that END at the row (pv_spray_logic.c)
        ran = np.concatenate([[False], on[:-1]])
        flow = np.where(ran, SYN_FLOW_LPM * (1 + rng.normal(0, 0.01, n)), 0.0)
        litres_day = litres + np.cumsum(flow * dt / 60.0)
        litres = float(litres_day[-1])
        d["spike_id"], d["spray_assign"] = spike_id, assign
        d["pump_duty"] = np.where(ran, config.PUMP_DUTY, 0.0)
        d["flow_Lpm"], d["water_L_cum"] = flow, litres_day
        d["P_pump_W"] = np.where(ran, SYN_PUMP_W * (1 + rng.normal(0, 0.01, n)), 0.0) + SYN_PUMP_OFFSET_W
        d["alarm"] = alarm
        # for the sweeps: the cells lead the back sheet (T_cell = T_back + lead * dT_back/dt + ...)
        smooth = pd.Series(tb0.mean(axis=1) - cool).rolling(PER_MIN + 1, center=True, min_periods=1).mean()
        d["_t_cell"] = (smooth + cell_lead_s * np.gradient(smooth.to_numpy(), dt)
                        + config.CELL_MINUS_BACK_C * g / 1000.0)
        d["_clean"] = clean
        out.append(d)
    return pd.concat(out, ignore_index=True), pd.DataFrame(events), log


# --- I-V sweeps -----------------------------------------------------------------------------

def sweep_curve(model, g, t_cell, rng, n_coarse=24, n_fine=24, r=fake_logger.R_SWEEP):
    """One sweep as the load firmware takes it (docs/paper/07 sec. 4): V_oc, a logarithmic
    coarse pass over the duty, a linear fine pass around the best coarse point. The
    operating point of each duty is where the module curve meets the load line V = I R / D,
    found on a table of the curve. Returns (D, V, I, V_oc)."""
    params = fake_logger.curve(model, g, t_cell)
    v_hi = float(pvlib.pvsystem.v_from_i(0.0, *params)) * 1.001
    v_tab = np.linspace(0.0, v_hi, 1500)
    i_tab = np.asarray(pvlib.pvsystem.i_from_v(v_tab, *params), dtype=float)
    v_oc = float(np.interp(0.0, -i_tab, v_tab))            # i_tab falls with V

    def points(duty):
        diff = i_tab[None, :] - np.outer(duty / r, v_tab)   # > 0 below the operating point
        k = np.clip((diff > 0).sum(axis=1) - 1, 0, len(v_tab) - 2)
        rows = np.arange(len(duty))
        d0, d1 = diff[rows, k], diff[rows, k + 1]
        v = v_tab[k] + (v_tab[k + 1] - v_tab[k]) * d0 / (d0 - d1)
        return v, v * duty / r

    coarse = np.geomspace(0.02, 1.0, n_coarse)
    v, i = points(coarse)
    m = int(np.argmax(v * i))
    fine = np.linspace(coarse[max(m - 1, 0)], coarse[min(m + 1, n_coarse - 1)], n_fine)
    vf, i_f = points(fine)
    duty = np.concatenate([coarse, fine])
    v = np.concatenate([v, vf]) + rng.normal(0, 0.005, len(duty))
    i = np.concatenate([i, i_f]) + rng.normal(0, 0.002, len(duty))
    return np.round(duty, 5), np.round(v, 4), np.round(i, 4), v_oc + rng.normal(0, 0.005)


def write_sweeps(rows, out, seed=0, hours=("07:00:00", "17:00:00"), every_min=1):
    """One sweep per `every_min` minutes, just after the row at hh:mm:00, while the module
    has light. Uses the generator's cell temperature and cleaning factor of that row."""
    out, rng, model = pathlib.Path(out), np.random.default_rng(seed), fake_logger.module_model()
    pick = ((rows["time"] >= hours[0]) & (rows["time"] < hours[1])
            & rows["time"].str.endswith(":00") & (rows["irradiance"] >= 50.0))
    if every_min > 1:
        pick &= rows["time"].str[3:5].astype(int) % every_min == 0
    sel = rows[pick]
    t_utc = fake_logger._utc(pd.to_datetime(sel["date"] + " " + sel["time"]))
    points, summary, ids = {}, {}, {}
    for (idx, r), t in zip(sel.iterrows(), t_utc):
        g = float(r["irradiance"])
        duty, v, i, v_oc = sweep_curve(model, g * (1 + r["_clean"]), float(r["_t_cell"]), rng)
        sid, key = int(t.timestamp()), t.strftime("%Y%m%d")
        best = int(np.argmax(v * i))
        points.setdefault(key, []).append(pd.DataFrame(
            {"sweep_id": sid, "k": np.arange(len(duty)), "D": duty, "V": v, "I": i}))
        summary.setdefault(key, []).append(
            [sid, f"{t:%Y-%m-%d}", f"{t:%H:%M:%S}", 0, *[r[c] for c in BACK_SENSORS],
             round(g + rng.normal(0, 1)), round(g + rng.normal(0, 1)), round(v_oc, 4),
             v[best], i[best], duty[best], 0])
        ids[idx] = sid
    cols = ["sweep_id", "date", "time", "uptime_ms", *BACK_SENSORS, "G_before", "G_after",
            "V_oc", "V_best", "I_best", "D_best", "flags"]
    for key in points:
        pd.concat(points[key]).to_csv(out / f"SW{key}.csv", index=False)
        pd.DataFrame(summary[key], columns=cols).to_csv(out / f"SS{key}.csv", index=False)
    return ids


# --- files ------------------------------------------------------------------------------------

def write_rows(rows, out, sweep_ids=None):
    """The 27-column day files of a Phase B logger, UTC, numbers written as the firmware
    writes them (pv_spray_logic.c, spray_row_fields)."""
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    t = fake_logger._utc(pd.to_datetime(rows["date"] + " " + rows["time"]))
    last_sweep = pd.Series(0, index=rows.index, dtype="int64")
    if sweep_ids:
        last_sweep = pd.Series(sweep_ids).reindex(rows.index).ffill().fillna(0).astype("int64")
    lit = rows["irradiance"] >= 50.0
    duty = (R_HOLD * rows["current"] / rows["voltage"].where(rows["voltage"] > 0)).clip(0, 1)
    d = rows.assign(
        date=t.strftime("%Y-%m-%d"), time=t.strftime("%H:%M:%S"), wind="NaN", p="",
        D=np.where(lit, duty.fillna(0).map("{:.4f}".format), "0.0000"),
        load_mode=np.where(lit, "hold", "off"), sweep_id=last_sweep,
        uptime_ms=((t - t[0]).total_seconds() * 1000 + 5000).astype("int64"), status=0,
        pump_duty=rows["pump_duty"].map("{:.3f}".format),
        flow_Lpm=rows["flow_Lpm"].map("{:.3f}".format),
        water_L_cum=rows["water_L_cum"].map("{:.4f}".format),
        P_pump_W=rows["P_pump_W"].map("{:.3f}".format))
    cols = fake_logger.HEADER + fake_logger.EXTRA + PHASE_B_COLUMNS
    for key, g in d.groupby(d["date"].str.replace("-", "")):
        g[cols].to_csv(out / f"{key}.csv", index=False)


def write_spray_log(log, rows, out):
    """/spray_log.csv in the firmware's format. Times are the local row times of the
    generator, written in UTC; the boot line has no time, as on the board."""
    lines = ["date,time,uptime_ms,spike_id,event,detail"]
    t0 = pd.to_datetime(rows["date"].iloc[0] + " " + rows["time"].iloc[0])
    for when, _, _, sid, event, detail in log:
        if isinstance(when, str):
            lines.append(f",,5000,{sid},{event},{detail}")
            continue
        u = fake_logger._utc([when])[0]
        up = int((when - t0).total_seconds() * 1000 + 5000)
        lines.append(f"{u:%Y-%m-%d},{u:%H:%M:%S},{up},{sid},{event},{detail}")
    (pathlib.Path(out) / "spray_log.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build(days, out, seed=config.PHASE_B_SEED, dry_run=False, sweeps=True, sweep_every_min=1,
          **effects):
    """Everything a Phase B campaign leaves on the card, from synthetic days. Returns
    (events, rows): the generator's own record of what it injected into every event, and
    its rows with the true cell temperature (`_t_cell`), for tests to compare with."""
    out = pathlib.Path(out)
    rows, events, log = simulate(days, seed=seed, dry_run=dry_run, **effects)
    out.mkdir(parents=True, exist_ok=True)
    ids = write_sweeps(rows, out, seed=seed, every_min=sweep_every_min) if sweeps else None
    write_rows(rows, out, ids)
    write_spray_log(log, rows, out)
    dates = sorted(rows["date"].unique())
    fake_logger.write_markers({d: [("06:10:00", "cleaning start", "synthetic"),
                                   ("06:20:00", "cleaning end", "synthetic")] for d in dates}, out)
    return events, rows
