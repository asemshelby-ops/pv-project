"""All control strategies on the SAME weather and the SAME rooftop. SIMULATION.

    python -m twin.compare                      5 synthetic days, report in out/twin/compare
    python -m twin.compare --days 10 --seed 22
    python -m twin.compare --no-sensitivity     the main table only (faster)

Every strategy gets the same weather days, the same draw of the rooftop (mismatch,
soiling, wind exposure) and the same sensor noise. Reported per strategy: DC energy, pump
and valve energy, net energy, water used (evaporated + lost) and circulated, pump hours.

Three sensitivity blocks follow, because the result depends on placeholders:
  * evaporation switched off in the plant (the no-evaporation case of CLAUDE.md);
  * the predictive controller with a wrong model of the plant, and with a higher
    switching ratio (spray only when the expected gain is well above the pump power);
  * the fixed threshold and continuous spraying at other settings. The best of these is
    picked ON THE SAME DAYS it is scored on, so it is optimistic for that strategy.

Weather: synthetic days of data/synth.py (hard variant) from a seed the NASIM model was
not trained on. NASIM-GBT itself is the model of the paper pipeline's synthetic run.
Nothing here is a measurement; no number says what a real plant would gain.
"""
import argparse
import json
import pathlib
import time

import numpy as np
import pandas as pd

import config
from twin import SIM_NOTICE, controllers, electrical, params, rooftop

WEATHER_SEED = 21           # not config.EXPORT_SEED (5): NASIM was trained on that run
ROOF_SEED = 1
NOISE_SEED = 5
SUM_KEYS = ["e_dc_wh", "e_pump_wh", "e_valves_wh", "e_net_wh", "water_used_l",
            "water_circulated_l", "pump_hours", "zone_hours", "valve_openings", "alarms"]


def strategies(p, nasim_run=controllers.NASIM_RUN, nasim=True):
    """The five strategies of the comparison, in report order."""
    out = [controllers.NoCooling(), controllers.Continuous(),
           controllers.FixedThreshold(p.fixed_on_c, p.fixed_off_c)]
    if nasim:
        out.append(controllers.SpikeTriggered(controllers.nasim_factory(nasim_run),
                                              "Spike-triggered (NASIM-GBT)"))
    out.append(controllers.Predictive())
    return out


def run_set(p, weathers, ctrls, roof_seed=ROOF_SEED, noise_seed=NOISE_SEED, keep_day=0,
            log=print):
    """Each controller over every day. Returns (per-strategy totals, per-day rows, the
    sample tables of day `keep_day` per strategy)."""
    roof = rooftop.build(p, roof_seed)
    totals, per_day, tables = [], [], {}
    for c in ctrls:
        t0 = time.time()
        days = []
        for d, w in enumerate(weathers):
            table, s = rooftop.simulate_day(roof, w, c, noise_seed + d)
            days.append({"strategy": c.name, "date": w.date, **s})
            if d == keep_day:
                tables[c.name] = table
        per_day += days
        tot = {"strategy": c.name, **{k: sum(x[k] for x in days) for k in SUM_KEYS}}
        tot["t_module_mean_c"] = float(np.mean([x["t_module_mean_c"] for x in days]))
        tot["t_module_max_c"] = max(x["t_module_max_c"] for x in days)
        tot["t_tank_max_c"] = max(x["t_tank_max_c"] for x in days)
        totals.append(tot)
        log(f"  {c.name:38s} net {tot['e_net_wh'] / 1000:8.2f} kWh   "
            f"({time.time() - t0:.0f} s)")
    out = pd.DataFrame(totals)
    base = out.loc[out["strategy"] == controllers.NoCooling.name, "e_net_wh"]
    if len(base):
        base = float(base.iloc[0])
        out["gain_wh"] = out["e_net_wh"] - base
        out["gain_rel"] = out["e_net_wh"] / base - 1.0
        out["gross_gain_rel"] = out["e_dc_wh"] / base - 1.0
        with np.errstate(divide="ignore", invalid="ignore"):
            out["litres_per_net_kwh"] = np.where(out["gain_wh"] > 0, out["water_used_l"]
                                                 / (out["gain_wh"] / 1000.0), np.nan)
    return out, pd.DataFrame(per_day), tables


def settings_grid(p):
    """Other settings of the two classical strategies (sensitivity block 3)."""
    duties = (p.pump_min_duty, 1.0)
    out = [controllers.NoCooling()] + [controllers.Continuous(d) for d in duties]
    for on_c, off_c in ((40.0, 30.0), (45.0, 35.0), (50.0, 40.0), (55.0, 45.0)):
        out += [controllers.FixedThreshold(on_c, off_c, d) for d in duties]
    return out


def wrong_models(p):
    """The predictive controller with a wrong picture of the plant (sensitivity block 2)."""
    low = p.replace(spray_u_module_water=0.7 * p.spray_u_module_water,
                    evap_hc0=0.7 * p.evap_hc0, evap_hc1=0.7 * p.evap_hc1, u0=1.2 * p.u0)
    high = p.replace(spray_u_module_water=1.3 * p.spray_u_module_water,
                     evap_hc0=1.3 * p.evap_hc0, evap_hc1=1.3 * p.evap_hc1, u0=0.8 * p.u0)
    return [controllers.NoCooling(),
            controllers.Predictive(),
            controllers.Predictive(low, "Predictive, model expects 30 % less cooling"),
            controllers.Predictive(high, "Predictive, model expects 30 % more cooling"),
            controllers.Predictive(None, "Predictive, right model, on/off ratio 1.6/1.3",
                                   on_ratio=1.6, off_ratio=1.3),
            controllers.Predictive(None, "Predictive, right model, on/off ratio 2.0/1.6",
                                   on_ratio=2.0, off_ratio=1.6)]


# --- report -----------------------------------------------------------------------------------

def _table(res):
    lines = ["| Strategy | DC energy, kWh | Pump + valves, kWh | Net energy, kWh | Net vs no "
             "cooling | Water used, L | Water circulated, m3 | Pump hours | Zone-hours | "
             "Valve openings | Mean module T, degC | L per net kWh gained |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in res.iterrows():
        lpk = r.get("litres_per_net_kwh", np.nan)
        lines.append(
            f"| {r['strategy']} | {r['e_dc_wh'] / 1000:.2f} | "
            f"{(r['e_pump_wh'] + r['e_valves_wh']) / 1000:.2f} | {r['e_net_wh'] / 1000:.2f} | "
            f"{100 * r['gain_rel']:+.2f} % | {r['water_used_l']:.0f} | "
            f"{r['water_circulated_l'] / 1000:.1f} | {r['pump_hours']:.1f} | "
            f"{r['zone_hours']:.1f} | {int(r['valve_openings'])} | {r['t_module_mean_c']:.1f} | "
            + ("-" if not np.isfinite(lpk) else f"{lpk:.0f}") + " |")
    return lines


def report(main, blocks, p, roof, weathers, meta):
    n = roof.n_modules
    stc = electrical.stc(p)
    tags = p.tags()
    g_peak = max(float(w.g.max()) for w in weathers)
    lines = [
        "# Cooling-control strategies on the simulated rooftop", "",
        f"> **{SIM_NOTICE}**", ">",
        "> Every number below comes from the model with placeholder parameters "
        "(`docs/twin/parameters.md`). The ranking of the strategies and the size of every "
        "gain can change when the placeholders are replaced by measurements.", "",
        "## Set-up (all simulated)", "",
        f"- Rooftop: {roof.shape[0]} strings x {roof.shape[1]} modules = {n} module twins, "
        f"{n * stc['p_mp'] / 1000:.1f} kW at reference conditions (model value); one spray zone "
        "and valve per string, one shared pump and tank; one ideal MPPT per string.",
        f"- Spread of this rooftop draw (seed {roof.seed}): photocurrent factor "
        f"{roof.f_il.min():.3f}-{roof.f_il.max():.3f}, soiling factor "
        f"{roof.soiling.min():.3f}-{roof.soiling.max():.3f}, wind exposure "
        f"{roof.wind_factor.min():.2f}-{roof.wind_factor.max():.2f}.",
        f"- Weather: {len(weathers)} synthetic days ({weathers[0].date} to {weathers[-1].date}), "
        f"{meta['weather']}; highest irradiance {g_peak:.0f} W/m2. Logged window "
        f"{config.DAY_START[:5]}-{config.DAY_END[:5]} local, 10 s steps.",
        "- Parameters: " + ", ".join(f"{c} {t}" for t, c in sorted(tags.items())) + ".",
        f"- Pump curve: {p.meta['pump_curve'].tag} "
        f"({'placeholder from the R385 listing' if p.meta['pump_curve'].tag == params.LIT else 'measured table'}).",
        f"- Spike detector: {meta['nasim']}.",
        "- Net energy = DC energy of the strings - pump energy - valve energy. Inverter, "
        "cables and the pump's supply are not modelled.",
        "- Water used = evaporated on the glass + the share that does not return to the "
        "tank; water circulated = everything the pump moved (it returns to the tank).",
        "", "## Result (simulation)", "", *_table(main), ""]
    for title, note, res in blocks:
        lines += [f"## {title} (simulation)", "", note, "", *_table(res), ""]
    lines += [
        "## What this table can and cannot say", "",
        "- It compares control strategies inside ONE model. It is a tool for developing the "
        "controllers before hardware exists.",
        "- It is not evidence of what spray cooling gains on a real roof: the cooling per "
        "litre, the evaporation, the pump power and the module's temperature coefficient "
        "are all placeholders.",
        "- The pump here is the R385 listing multiplied by the number of sprayed modules. A "
        "real installation has one larger pump, pipe losses and a different efficiency.",
        "- Days are simulated one by one from 06:00; the tank starts each day at ambient "
        "temperature.",
        ""]
    return "\n".join(lines)


def figure(tables, p, path, date):
    """Example day: mean module temperature and open zones for each strategy."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    names = list(tables)
    s = int(p.n_strings)
    colors = ["#52514e", "#2a78d6", "#eb6834", "#6250d6", "#1baf7a", "#eda100"]
    fig, ax = plt.subplots(3, 1, figsize=(10, 8), sharex=True,
                           gridspec_kw={"height_ratios": [3, 2, 1.6]})
    for name, c in zip(names, colors):
        t = tables[name]
        temp = t[[f"t_string_{z + 1}" for z in range(s)]].mean(axis=1)
        ax[0].plot(t["t_local"], temp, color=c, lw=1.1, label=name)
        ax[1].plot(t["t_local"], (t["p_dc"] - t["p_pump"] - t["p_valves"])
                   .rolling(30, center=True, min_periods=1).mean() / 1000, color=c, lw=1.1)
    for row, (name, c) in enumerate(zip(names, colors)):
        t = tables[name]
        n_open = t[[f"valve_{z + 1}" for z in range(s)]].sum(axis=1).to_numpy()
        ax[2].fill_between(t["t_local"], row, row + 0.9 * n_open / s, color=c, lw=0, step="post")
    ax[0].set_ylabel("mean module temperature, degC")
    ax[1].set_ylabel("net power, kW (5-min mean)")
    ax[2].set_yticks(np.arange(len(names)) + 0.45)
    ax[2].set_yticklabels([n.split(" (")[0] for n in names], fontsize=8)
    ax[2].set_ylabel("open zones (bar height)")
    ax[0].legend(frameon=False, fontsize=8, ncol=2, loc="upper left")
    ax[0].set_title(f"SIMULATION - rooftop twin with placeholder parameters - {date}", fontsize=10)
    for a in ax:
        a.grid(alpha=0.3)
        a.spines[["top", "right"]].set_visible(False)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def synthetic_weather(seed=WEATHER_SEED, n_days=5, hard=True):
    from data import synth
    days, events = synth.generate(seed, n_days=n_days, hard=hard)
    return rooftop.weather_days(days), events


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--seed", type=int, default=WEATHER_SEED, help="weather seed")
    ap.add_argument("--roof-seed", type=int, default=ROOF_SEED)
    ap.add_argument("--base", action="store_true", help="base synthetic variant, not hard")
    ap.add_argument("--params", help="overlay file (default: twin/params_measured.json if any)")
    ap.add_argument("--nasim-run", default=str(controllers.NASIM_RUN))
    ap.add_argument("--no-sensitivity", action="store_true")
    ap.add_argument("--out", default="out/twin/compare")
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    p = params.load(a.params, None) if a.params else params.load()
    weathers, events = synthetic_weather(a.seed, a.days, hard=not a.base)
    meta = {"weather": f"data/synth.py, {'base' if a.base else 'hard'} variant, seed {a.seed}",
            "nasim": f"NASIM-GBT trained and tuned by the paper pipeline on SYNTHETIC data "
                     f"({a.nasim_run}); weather seed {a.seed} was not part of its training"}
    print(f"SIMULATION: {len(weathers)} days, {int(p.n_strings * p.modules_per_string)} modules")
    main_res, per_day, tables = run_set(p, weathers, strategies(p, a.nasim_run), a.roof_seed)
    blocks = []
    if not a.no_sensitivity:
        print("sensitivity: evaporation off")
        res, _, _ = run_set(p.replace(evap_enabled=0), weathers, strategies(p, a.nasim_run),
                            a.roof_seed)
        blocks.append(("Sensitivity 1: no evaporation",
                       "The plant with `evap_enabled = 0`: only the heat the water carries "
                       "away. The model-based controllers know it.", res))
        print("sensitivity: predictive controller with a wrong model")
        res, _, _ = run_set(p, weathers, wrong_models(p), a.roof_seed)
        blocks.append(("Sensitivity 2: predictive controller with a wrong model or another "
                       "switching ratio",
                       "The plant is unchanged. Rows 3-4: the controller's own copy of the "
                       "spray and loss parameters is off by 30 % and 20 %. Rows 5-6: the "
                       "right model, but a zone is opened only when expected gain / pump "
                       "power reaches a higher ratio than the default "
                       f"{p.predictive_on_ratio:g}/{p.predictive_off_ratio:g}.", res))
        print("sensitivity: other settings of the classical strategies")
        res, _, _ = run_set(p, weathers, settings_grid(p), a.roof_seed)
        blocks.append(("Sensitivity 3: other settings of continuous and fixed-threshold",
                       "Scored on the same days they would be chosen on: the best row is "
                       "optimistic for that strategy.", res))
    roof = rooftop.build(p, a.roof_seed)
    (out / "report.md").write_text(report(main_res, blocks, p, roof, weathers, meta),
                                   encoding="utf-8")
    main_res.to_csv(out / "strategies.csv", index=False)
    per_day.to_csv(out / "per_day.csv", index=False)
    for title, _, res in blocks:
        res.to_csv(out / (title.split(":")[0].lower().replace(" ", "_") + ".csv"), index=False)
    figure(tables, p, out / "example_day.png", weathers[0].date)
    (out / "run.json").write_text(json.dumps(
        {"label": SIM_NOTICE, "days": a.days, "weather_seed": a.seed, "roof_seed": a.roof_seed,
         "parameter_tags": p.tags(), **meta}, indent=2))
    print(f"report -> {out / 'report.md'}")
    print(SIM_NOTICE)


if __name__ == "__main__":
    main()
