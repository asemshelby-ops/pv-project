"""Validation hook: replay logger files through the twin and report its error.

    python -m twin.validate --data <copy of the SD card> --real      real rig files
    python -m twin.validate --demo                                   SYNTHETIC files, today

The twin is given only what the weather did -- measured in-plane irradiance, ambient
temperature and wind (the fallback wind when the file has none) -- and predicts the
module temperature and the power. Both are compared with what the logger measured:

  temperature  twin against the mean of T1..T5, every usable daylight row. Each detector
               segment (analysis/ingest.py) starts at its measured temperature.
  power        twin P_mp against the logged power, on rows where the load holds the
               operating point (load_mode hold, or no load information) and the logged
               power is above 1 W. Twice: with the twin's own temperature (the whole
               model) and with the measured temperature (the electrical model alone).
               The logged power is the load's operating point, which is close to but not
               exactly the maximum power point.
  sweeps       when SS* files exist: twin P_mp against the maximum of every usable I-V
               sweep, at the sweep's irradiance and temperature.

--demo writes synthetic rig files (data/synth.py through analysis/fake_logger.py), runs
the validation with the placeholder parameters, fits the parameters to the same files
(twin/fit.py) and validates again. The synthetic generator contains the same thermal
equation as the twin, so the demo proves that the hook and the fit work, NOT that the
model is right for our module.
"""
import argparse
import pathlib

import numpy as np
import pandas as pd

import config
from data.schema import BACK_SENSORS
from twin import SIM_NOTICE, electrical, fit, params

MIN_G = 50.0            # W/m2: rows below are not scored
MIN_POWER_W = 1.0       # logged power below this = no load connected
HOLD_MODES = {"hold", "none"}   # load_mode values whose `power` is compared


def frame_from_days(days):
    """A schema table in local time (data/synth.py) as an ingest-like frame: one segment
    per day. Keeps the wind column, which the logger files do not have yet."""
    t_local = pd.to_datetime(days["date"] + " " + days["time"])
    f = days.copy()
    f["t_local"] = t_local
    f["t_utc"] = t_local.dt.tz_localize(config.TZ).dt.tz_convert("UTC")
    f["segment"] = (f["date"] != f["date"].shift()).cumsum() - 1
    return f


def replay(frame, p, wind_column="wind"):
    """One row per usable logger row: measured and modelled temperature and power.
    Columns: t_local, date, g, t_meas, t_twin, p_meas, p_twin, p_twin_tmeas, scored_t,
    scored_p. Returns (table, whether the files held wind). wind_column: see
    fit.thermal_arrays (the logger's "wind", or Open-Meteo MODEL wind)."""
    arr = fit.thermal_arrays(frame, p, max_days=None, wind_column=wind_column)
    f = arr["frame"]
    t_twin = fit.replay_temperature(p, arr)
    g = arr["g"]
    offset = p.cell_minus_back_c * g / 1000.0
    out = pd.DataFrame({
        "t_local": f["t_local"].to_numpy(), "date": f["date"].to_numpy(), "g": g,
        "t_meas": arr["t_meas"], "t_twin": t_twin,
        "p_meas": f["power"].to_numpy(float) if "power" in f else np.nan,
        "p_twin": electrical.module_mpp(p, g, t_twin + offset)[0],
        "p_twin_tmeas": electrical.module_mpp(p, g, arr["t_meas"] + offset)[0]})
    mode = (f["load_mode"].fillna("none").to_numpy() if "load_mode" in f
            else np.full(len(f), "none"))
    out["scored_t"] = (g >= MIN_G) & ~arr["first"]
    out["scored_p"] = (out["scored_t"] & np.isin(mode, list(HOLD_MODES))
                       & (out["p_meas"] >= MIN_POWER_W))
    return out, arr["has_wind"]


def errors(model, measured):
    """Error statistics of model - measured."""
    e = np.asarray(model, float) - np.asarray(measured, float)
    e = e[np.isfinite(e)]
    if not len(e):
        return {"n": 0}
    return {"n": int(len(e)), "bias": float(e.mean()), "mae": float(np.abs(e).mean()),
            "rmse": float(np.sqrt(np.mean(e ** 2))),
            "p95_abs": float(np.quantile(np.abs(e), 0.95))}


def summarise(table):
    """Overall statistics and a per-day table."""
    st, sp = table[table["scored_t"]], table[table["scored_p"]]
    out = {"temperature_c": errors(st["t_twin"], st["t_meas"]),
           "power_w": errors(sp["p_twin"], sp["p_meas"]),
           "power_w_measured_temperature": errors(sp["p_twin_tmeas"], sp["p_meas"])}
    if len(sp):
        mean_p = float(sp["p_meas"].mean())
        for k in ("power_w", "power_w_measured_temperature"):
            out[k]["rmse_rel"] = out[k]["rmse"] / mean_p
        out["energy_rel_difference"] = float(sp["p_twin"].sum() / sp["p_meas"].sum() - 1.0)
    rows = []
    for date, d in table.groupby("date"):
        dt, dp = d[d["scored_t"]], d[d["scored_p"]]
        et, ep = errors(dt["t_twin"], dt["t_meas"]), errors(dp["p_twin"], dp["p_meas"])
        rows.append({"date": date, "rows": len(dt), "T_bias_C": et.get("bias"),
                     "T_rmse_C": et.get("rmse"), "T_p95_C": et.get("p95_abs"),
                     "P_rows": len(dp), "P_bias_W": ep.get("bias"), "P_rmse_W": ep.get("rmse")})
    return out, pd.DataFrame(rows)


def sweep_check(folder, p):
    """Twin P_mp against the maximum of every usable sweep. None without sweep files."""
    from analysis import gamma as gamma_mod
    points, summary = gamma_mod.read_sweeps(folder)
    if not len(points):
        return None
    s = gamma_mod.usable_sweeps(gamma_mod.sweep_parameters(points, summary))
    if not len(s):
        return None
    model = electrical.module_mpp(p, s["G"].to_numpy(float), s["T_cell"].to_numpy(float))[0]
    e = errors(model, s["P_mp"])
    e["rmse_rel"] = e["rmse"] / float(s["P_mp"].mean())
    e["t_cell_range_c"] = [float(s["T_cell"].min()), float(s["T_cell"].max())]
    return e


def _fmt(e, unit, rel=False):
    if not e or not e.get("n"):
        return "no rows"
    s = (f"n = {e['n']}, bias {e['bias']:+.2f} {unit}, MAE {e['mae']:.2f} {unit}, "
         f"RMSE {e['rmse']:.2f} {unit}, 95 % of |error| below {e['p95_abs']:.2f} {unit}")
    return s + (f", RMSE = {100 * e['rmse_rel']:.1f} % of the mean" if rel and "rmse_rel" in e
                else "")


MODEL_WIND = "wx_model_wind_speed_10m_ms"
MODEL_WIND_NOTE = ("Open-Meteo MODEL wind at 10 m (grid cell, joined by time; NOT measured at "
                   "the module, and not at the height the twin's u1 placeholder assumes): an "
                   "exploratory run, not a validation of the wind term")


def report(stats, days, sweeps, p, source, has_wind, title="Twin validation",
           wind_column="wind"):
    tags = p.tags()
    lines = [
        f"# {title}", "",
        f"> {SIM_NOTICE}", "",
        f"- Data: {source}",
        "- Twin parameters: " + ", ".join(f"{n} {t}" for t, n in sorted(tags.items())),
        "- Wind: " + ((MODEL_WIND_NOTE if wind_column == MODEL_WIND else
                       "measured (wind column of the files)") if has_wind else
                      f"not in the files; the twin used {p.wind_fallback_ms:g} m/s "
                      "throughout, so it cannot follow wind lulls"),
        f"- Days: {len(days)}; rows below {MIN_G:g} W/m2 are not scored", "",
        "| Quantity | Twin minus measured |", "|---|---|",
        f"| Module temperature (mean of T1..T5) | {_fmt(stats['temperature_c'], 'degC')} |",
        f"| Power, twin's own temperature | {_fmt(stats['power_w'], 'W', True)} |",
        "| Power, measured temperature (electrical model alone) | "
        f"{_fmt(stats['power_w_measured_temperature'], 'W', True)} |",
    ]
    if sweeps:
        lines.append(f"| P_mp against I-V sweep maxima | {_fmt(sweeps, 'W', True)} |")
    if "energy_rel_difference" in stats:
        lines += ["", f"Energy over the scored rows: twin {100 * stats['energy_rel_difference']:+.2f} % "
                      "against the logged power."]
    lines += ["", "## Per day", "", "| Date | Rows | T bias, degC | T RMSE, degC | "
              "T 95 %, degC | Power rows | P bias, W | P RMSE, W |", "|---|---|---|---|---|---|---|---|"]

    def num(x):
        return "-" if x is None or not np.isfinite(x) else f"{x:+.2f}"
    for _, r in days.iterrows():
        lines.append(f"| {r['date']} | {r['rows']} | {num(r['T_bias_C'])} | {num(r['T_rmse_C'])} "
                     f"| {num(r['T_p95_C'])} | {r['P_rows']} | {num(r['P_bias_W'])} | "
                     f"{num(r['P_rmse_W'])} |")
    return "\n".join(lines) + "\n"


def figure(table, path, title):
    """The day with the median temperature RMSE: measured against twin."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    scored = table[table["scored_t"]]
    rmse = scored.groupby("date").apply(
        lambda d: float(np.sqrt(np.mean((d["t_twin"] - d["t_meas"]) ** 2))), include_groups=False)
    day = rmse.sort_values().index[len(rmse) // 2]
    d = table[table["date"] == day]
    fig, ax = plt.subplots(2, 1, figsize=(9, 5.5), sharex=True)
    ax[0].plot(d["t_local"], d["t_meas"], color="#2a78d6", lw=1.2, label="measured (mean of T1..T5)")
    ax[0].plot(d["t_local"], d["t_twin"], color="#eb6834", lw=1.2, label="twin")
    ax[0].set_ylabel("module temperature, degC")
    ax[0].legend(frameon=False, loc="upper left")
    ax[1].plot(d["t_local"], d["p_meas"], color="#2a78d6", lw=1.2, label="logged power")
    ax[1].plot(d["t_local"], d["p_twin"], color="#eb6834", lw=1.2, label="twin P_mp")
    ax[1].set_ylabel("power, W")
    ax[1].legend(frameon=False, loc="upper left")
    ax[0].set_title(f"{title} - {day} (day with the median temperature error)", fontsize=10)
    for a in ax:
        a.grid(alpha=0.3)
        a.spines[["top", "right"]].set_visible(False)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def run(frame, out, p, source, sweeps_folder=None, title="Twin validation", log=print,
        wind_column="wind"):
    """Validate on an ingested frame; writes report.md, per_day.csv, rows.csv and a figure
    to `out`. Returns the statistics. wind_column: see fit.thermal_arrays."""
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    table, has_wind = replay(frame, p, wind_column)
    stats, days = summarise(table)
    sweeps = sweep_check(sweeps_folder, p) if sweeps_folder else None
    (out / "report.md").write_text(report(stats, days, sweeps, p, source, has_wind, title,
                                          wind_column), encoding="utf-8")
    days.to_csv(out / "per_day.csv", index=False)
    table.to_csv(out / "rows.csv", index=False)
    figure(table, out / "example_day.png", title)
    log(f"{title}: T {_fmt(stats['temperature_c'], 'degC')}")
    log(f"{' ' * len(title)}  P {_fmt(stats['power_w'], 'W', True)}")
    if sweeps:
        log(f"{' ' * len(title)}  sweep maxima {_fmt(sweeps, 'W', True)}")
    log(f"{' ' * len(title)}  -> {out / 'report.md'}")
    return {**stats, "sweeps": sweeps, "has_wind": has_wind}


def run_folder(folder, out, p, source, model_wind=False, **kw):
    """model_wind: join Open-Meteo MODEL wind (wxref, docs/weather-reference.md) and drive
    the twin with it instead of the logger's wind column."""
    from analysis import ingest
    frame = ingest.read_logger(folder)
    if model_wind:
        from wxref import analysis as wxa
        frame = wxa.frame_with_model(frame, wxa.model_for(frame, log=kw.get("log", print)))
        kw["wind_column"] = MODEL_WIND
        source += "; wind: Open-Meteo MODEL wind at 10 m"
    return run(frame, out, p, source, sweeps_folder=folder, **kw)


# --- the synthetic demo -------------------------------------------------------------------------

def build_demo_files(root, seed=11, n_days=10):
    """Synthetic rig files for the demo: day files (wind = NaN, as the rig writes them)
    and the sweep files of four gamma runs. Returns (raw folder, the day table)."""
    from analysis import fake_logger
    from data import synth
    raw = pathlib.Path(root) / "raw"
    days, _ = synth.generate(seed, n_days=n_days, hard=False)
    days = one_hidden_module(days, seed)
    fake_logger.write_logger_files(days, raw)
    dates = sorted(days["date"].unique())
    runs = [(d, hms) for d in (dates[1], dates[min(4, len(dates) - 1)])
            for hms in ("11:30:00", "13:00:00")]
    fake_logger.write_gamma_runs(runs, raw, seed=seed)
    return raw, days


def one_hidden_module(days, seed):
    """Rewrite voltage, current and power of a synthetic day table so that they come from
    the SAME hidden module as the synthetic sweeps (analysis/fake_logger.module_model, a
    single-diode curve that is NOT the twin's placeholder), held at its maximum power
    point. data/synth.py uses a simpler power formula; with two different modules in one
    data set the fit could not be checked against the logged power. Computed with pvlib,
    not with twin/electrical.py."""
    import pvlib
    from analysis import fake_logger
    rng = np.random.default_rng(seed + 1000)
    g = days["irradiance"].to_numpy(float)
    t_cell = days[BACK_SENSORS].mean(axis=1).to_numpy(float) + config.CELL_MINUS_BACK_C * g / 1000
    lit = g >= 1.0
    sd = pvlib.pvsystem.singlediode(*fake_logger.curve(fake_logger.module_model(), g[lit],
                                                       t_cell[lit]))
    v, i = np.zeros(len(g)), np.zeros(len(g))
    v[lit], i[lit] = np.asarray(sd["v_mp"]), np.asarray(sd["i_mp"])
    out = days.copy()
    out["voltage"] = np.round(v + rng.normal(0, config.V_NOISE, len(g)) * lit, 3)
    out["current"] = np.round(i + rng.normal(0, config.I_NOISE, len(g)) * lit, 4)
    out["power"] = np.round(out["voltage"] * out["current"], 3)
    return out


def demo(out="out/twin/validation_demo", seed=11, n_days=10, log=print):
    """Placeholders against synthetic files, then the fit, then the fitted values."""
    out = pathlib.Path(out)
    raw, days = build_demo_files(out, seed, n_days)       # days: the generator's own table
    src = f"SYNTHETIC rig files ({n_days} days, seed {seed}, data/synth.py): a test of the hook"
    p0 = params.load(None, None)
    res = {"placeholders_files": run_folder(
        raw, out / "1_placeholders_logger_files", p0, src + "; logger file format",
        title="Twin validation, placeholders, SYNTHETIC logger files", log=log)}
    res["placeholders_wind"] = run(
        frame_from_days(one_hidden_module(days, seed)), out / "2_placeholders_with_wind", p0,
        src + "; the generator's table including its wind column",
        title="Twin validation, placeholders, SYNTHETIC table with wind", log=log)
    values, diag, _ = fit.fit_folder(raw, p0, log=log)
    overlay = fit.write_overlay(out / "fit_synthetic.json", values, params.FIT_SYNTH,
                                f"twin.fit on {raw} (synthetic)", diag)
    p1 = params.load(overlay, None)
    res["fitted_files"] = run_folder(
        raw, out / "3_fitted_logger_files", p1, src + "; parameters fitted to these files",
        title="Twin validation, FITTED to the synthetic files", log=log)
    res["fit"] = diag
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", help="folder with the logger's files")
    ap.add_argument("--real", action="store_true", help="the folder holds REAL rig files")
    ap.add_argument("--params", help="overlay file to use instead of params_measured.json")
    ap.add_argument("--out", default="out/twin/validation")
    ap.add_argument("--demo", action="store_true", help="run on synthetic files")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--wx-model-wind", action="store_true",
                    help="drive the twin with Open-Meteo MODEL wind at 10 m (wxref; needs "
                         "config.WX_SITE_LAT/LON) instead of the files' wind column")
    a = ap.parse_args()
    if a.demo:
        demo(a.out + "_demo" if a.out == "out/twin/validation" else a.out, a.seed, a.days)
        print("SYNTHETIC data: this shows that the hook and the fit run, not model accuracy.")
        return
    if not a.data:
        ap.error("give --data <folder>, or --demo")
    p = params.load(a.params, None) if a.params else params.load()
    kind = "REAL rig files" if a.real else "logger files (not declared real)"
    run_folder(a.data, a.out, p, f"{kind} in {a.data}", model_wind=a.wx_model_wind)


if __name__ == "__main__":
    main()
