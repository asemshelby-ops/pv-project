"""Which unmeasured quantity changes the result most? One-at-a-time ranking. SIMULATION.

    python -m twin.rank                 5 synthetic days, report in out/twin/rank (~10 min)
    python -m twin.rank --days 2        quicker, coarser

Every row changes ONE quantity of the plant (or of the weather) to a low and a high
value and runs no cooling, the fixed threshold 45/35 degC at full and at the lowest pump
duty, and the predictive rule on the same days, rooftop and sensor noise as
twin/compare.py. The model-based controller knows the changed value (as if it had been
measured). The quantities are ranked by the largest change of net energy against no
cooling, in percentage points, over the three cooling strategies.

The low and high values are ASSUMED ranges, chosen to bracket what we cannot rule out
before measuring; they are listed with their reason in RANGES below and in the report.
A wider range ranks a quantity higher: the ranking says "what to measure first", not how
uncertain each quantity really is. Nothing here is a measurement.
"""
import argparse
import dataclasses
import json
import pathlib
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from twin import SIM_NOTICE, compare, controllers, electrical, fit, params


def _scale_curve(p, col, f):
    return tuple(tuple(v * f if i == col else v for i, v in enumerate(row))
                 for row in p.pump_curve)


# (key, label, why this range, low, high); low/high = ("param", fn(p) -> dict) or
# ("weather", fn(w) -> Weather)
RANGES = [
    ("evaporation", "Evaporation from the wet glass",
     "low: no evaporation (the bound CLAUDE.md used); high: the McAdams wind coefficient "
     "of the placeholder, which includes radiation and free convection and so overstates "
     "the mass transfer (refs.md). The Watmuff form, 2.8 + 3.0 v, is a separate row",
     ("param", lambda p: {"evap_enabled": 0}), ("param", lambda p: {})),
    ("evap_watmuff", "Evaporation, Watmuff coefficient instead of McAdams",
     "low: h = 2.8 + 3.0 v (Watmuff et al. 1977, radiation and free convection removed); "
     "high: the placeholder 5.7 + 3.8 v",
     ("param", lambda p: {"evap_hc0": 2.8, "evap_hc1": 3.0}), ("param", lambda p: {})),
    ("pump_power", "Pump electrical power at a given flow",
     "low: x0.5; high: x3, a typical 12 V spray pump (17-33 W at 2 L/min, CLAUDE.md) "
     "against the R385 listing (7.2 W at 1.75 L/min)",
     ("param", lambda p: {"pump_curve": _scale_curve(p, 2, 0.5)}),
     ("param", lambda p: {"pump_curve": _scale_curve(p, 2, 3.0)})),
    ("pump_flow", "Pump flow through our nozzles at a given duty",
     "x0.5 / x1.5: the listings give free flow and disagree on the head; the nozzles cut it",
     ("param", lambda p: {"pump_curve": _scale_curve(p, 1, 0.5)}),
     ("param", lambda p: {"pump_curve": _scale_curve(p, 1, 1.5)})),
    ("spray_u", "Heat transfer module -> sprayed water (spray_u_module_water)",
     "x0.5 / x2, no source for the placeholder",
     ("param", lambda p: {"spray_u_module_water": 0.5 * p.spray_u_module_water}),
     ("param", lambda p: {"spray_u_module_water": 2.0 * p.spray_u_module_water})),
    ("wetting", "Flow needed to wet the whole glass",
     "x0.5 / x2, no source for the placeholder",
     ("param", lambda p: {"spray_full_wetting_lpm": 0.5 * p.spray_full_wetting_lpm}),
     ("param", lambda p: {"spray_full_wetting_lpm": 2.0 * p.spray_full_wetting_lpm})),
    ("wind", "Wind speed at the module",
     "x0.5 / x1.5 of the synthetic wind (no anemometer on the rig)",
     ("weather", lambda w: dataclasses.replace(w, wind=0.5 * w.wind)),
     ("weather", lambda w: dataclasses.replace(w, wind=1.5 * w.wind))),
    ("humidity", "Relative humidity",
     "x0.5 / x1.5 of the synthetic humidity, capped at 100 %",
     ("weather", lambda w: dataclasses.replace(w, rh=0.5 * w.rh)),
     ("weather", lambda w: dataclasses.replace(w, rh=np.minimum(1.5 * w.rh, 100.0)))),
    ("gamma", "Temperature coefficient gamma of P_mp",
     "-0.30 / -0.50 %/degC (model placeholder: about -0.46)",
     ("param", lambda p: {"eg_ref": fit.calibrate_gamma(p, -0.0030)}),
     ("param", lambda p: {"eg_ref": fit.calibrate_gamma(p, -0.0050)})),
    ("faiman", "Faiman heat loss u0 and u1",
     "x0.8 / x1.2 together",
     ("param", lambda p: {"u0": 0.8 * p.u0, "u1": 0.8 * p.u1}),
     ("param", lambda p: {"u0": 1.2 * p.u0, "u1": 1.2 * p.u1})),
    ("heat_capacity", "Heat capacity of the module",
     "x0.7 / x1.3 (time constant 6.3 +/- 1 min, Herteleer et al. 2023, plus margin)",
     ("param", lambda p: {"heat_capacity_j_per_k": 0.7 * p.heat_capacity_j_per_k}),
     ("param", lambda p: {"heat_capacity_j_per_k": 1.3 * p.heat_capacity_j_per_k})),
    ("tank_ua", "Heat loss of the tank to the air",
     "x0.2 / x5, no source for the placeholder (sun on the tank is not modelled)",
     ("param", lambda p: {"tank_ua_w_per_k_per_module": 0.2 * p.tank_ua_w_per_k_per_module}),
     ("param", lambda p: {"tank_ua_w_per_k_per_module": 5.0 * p.tank_ua_w_per_k_per_module})),
    ("spray_loss", "Share of sprayed water not returning to the tank",
     "0.02 / 0.15 (placeholder 0.05); changes water, not energy",
     ("param", lambda p: {"spray_loss_fraction": 0.02}),
     ("param", lambda p: {"spray_loss_fraction": 0.15})),
]

STRATS = ["Fixed 45/35, full duty", "Fixed 45/35, lowest duty", "Predictive"]


def _ctrls(p):
    return [controllers.NoCooling(),
            controllers.FixedThreshold(p.fixed_on_c, p.fixed_off_c, 1.0),
            controllers.FixedThreshold(p.fixed_on_c, p.fixed_off_c, p.pump_min_duty),
            controllers.Predictive()]


def _apply(p, weathers, change):
    kind, fn = change
    if kind == "param":
        values = fn(p)
        return (p.replace(**values) if values else p), weathers
    return p, [fn(w) for w in weathers]


def run_case(args):
    """One (quantity, level) case. Returns a dict of gains and water per strategy."""
    key, level, days, seed = args
    p = params.load()
    weathers, _ = compare.synthetic_weather(seed, days)
    if key != "base":
        row = next(r for r in RANGES if r[0] == key)
        p, weathers = _apply(p, weathers, row[3] if level == "low" else row[4])
    res, _, _ = compare.run_set(p, weathers, _ctrls(p), log=lambda *a: None)
    out = {"key": key, "level": level, "gamma_model": electrical.gamma_pmp(p)}
    for name, (_, r) in zip(["No cooling"] + STRATS, res.iterrows()):
        out[f"{name}|gain_pct"] = 100.0 * r["gain_rel"]
        out[f"{name}|water_l"] = r["water_used_l"]
        out[f"{name}|net_kwh"] = r["e_net_wh"] / 1000.0
    return out


def report(df, days, seed):
    base = df[df.key == "base"].iloc[0]
    rows = []
    for key, label, why, _, _ in RANGES:
        lo = df[(df.key == key) & (df.level == "low")].iloc[0]
        hi = df[(df.key == key) & (df.level == "high")].iloc[0]
        swing = max(max(abs(lo[f"{s}|gain_pct"] - base[f"{s}|gain_pct"]),
                        abs(hi[f"{s}|gain_pct"] - base[f"{s}|gain_pct"])) for s in STRATS)
        sign_flip = any(np.sign(lo[f"{s}|gain_pct"]) != np.sign(hi[f"{s}|gain_pct"])
                        for s in STRATS)
        wswing = max(max(abs(lo[f"{s}|water_l"] - base[f"{s}|water_l"]),
                         abs(hi[f"{s}|water_l"] - base[f"{s}|water_l"])) for s in STRATS)
        rows.append((swing, key, label, why, lo, hi, sign_flip, wswing))
    rows.sort(key=lambda r: -r[0])
    head = "| Rank | Quantity | Range (assumed) | " + " | ".join(
        f"{s}: low / high, % (base {base[s + '|gain_pct']:+.2f})" for s in STRATS) + \
        " | Largest change, points | Sign of the result changes | Largest change of water, L |"
    lines = [
        f"# Which unmeasured quantity decides the result? ({days} days, weather seed {seed})",
        "", f"> **{SIM_NOTICE}**", "",
        "One quantity at a time, every other placeholder at its value in `twin/params.py`. "
        "Cells: net energy against no cooling, in %, at the low and the high value. "
        "Ranges are assumptions (see `twin/rank.py`); a wider range ranks higher.", "",
        head, "|" + "---|" * (6 + len(STRATS))]
    for i, (swing, key, label, why, lo, hi, flip, wswing) in enumerate(rows, 1):
        cells = " | ".join(f"{lo[s + '|gain_pct']:+.2f} / {hi[s + '|gain_pct']:+.2f}"
                           for s in STRATS)
        lines.append(f"| {i} | {label} | {why} | {cells} | {swing:.2f} | "
                     f"{'yes' if flip else 'no'} | {wswing:.0f} |")
    lines += ["", f"Base case water used, L: " + ", ".join(
        f"{s} {base[s + '|water_l']:.0f}" for s in STRATS), ""]
    return "\n".join(lines), rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--seed", type=int, default=compare.WEATHER_SEED)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", default="out/twin/rank")
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cases = [("base", "base", a.days, a.seed)] + [
        (r[0], lvl, a.days, a.seed) for r in RANGES for lvl in ("low", "high")]
    print(f"SIMULATION: {len(cases)} cases x {a.days} days")
    with ProcessPoolExecutor(a.jobs) as ex:
        results = []
        for r in ex.map(run_case, cases):
            print(f"  {r['key']:14s} {r['level']:5s} predictive "
                  f"{r['Predictive|gain_pct']:+.2f} %")
            results.append(r)
    df = pd.DataFrame(results)
    df.to_csv(out / "rank.csv", index=False)
    text, _ = report(df, a.days, a.seed)
    (out / "report.md").write_text(text, encoding="utf-8")
    (out / "run.json").write_text(json.dumps({"label": SIM_NOTICE, "days": a.days,
                                              "weather_seed": a.seed}, indent=2))
    print(f"report -> {out / 'report.md'}")
    print(SIM_NOTICE)


if __name__ == "__main__":
    main()
