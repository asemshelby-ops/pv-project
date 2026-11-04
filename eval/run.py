"""One command for Steps 2-4:
    python -m eval.run --seed 5                   one seed, base synthetic data
    python -m eval.run --seeds 5 6 7 8 9          several seeds + mean/spread summary
    python -m eval.run --seeds 5 6 7 8 9 --hard   the same on the hard variant
Seeds 0-4 were used to design NASIM-GBT's current version; judge it on 5-9.

  1. generate the synthetic days (data/synth.py): base 60 days, hard 150 days
  2. split chronologically by whole days: base 36/12/12, hard 90/30/30
  3. train NASIM-GBT versions (with and without wind) on the training days
  4. tune every tunable setting on the VALIDATION days only (best event F1,
     ties broken by fewer false alarms per day); a best value on the edge of
     its grid extends the grid and the search is repeated
  5. run every detector once, sample by sample, on the TEST days and score it
  6. write out/results/<base|hard>/seed<N>/: results.md, results.csv,
     tuned.json, example-day plots, feature importance, the trained models
  7. with several seeds: out/results/<base|hard>/summary_seeds<a>-<b>.md

SYNTHETIC DATA: the results prove that the pipeline works, not the method.
"""
import argparse
import concurrent.futures
import itertools
import json
import pathlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from data import synth  # noqa: E402
from data.schema import BACK_SENSORS  # noqa: E402
from detect import gbt, stream  # noqa: E402
from detect.cusum import CusumDetector  # noqa: E402
from detect.features import feature_names  # noqa: E402
from detect.threshold import ThresholdDetector  # noqa: E402
from eval.metrics import score  # noqa: E402
from eval.split import split_days  # noqa: E402

BANNER = ("SYNTHETIC DATA. These numbers show that the pipeline works end to end "
          "(generation -> streaming detection -> event scoring). They do NOT show "
          "that the method works on the real module; that needs Phase 1 data.")

# Starting grids. Edge extenders return the next value outward, or None (stop).
THR_GRID = [round(x, 1) for x in np.arange(1.0, 4.01, 0.1)]
THR_EXT = (lambda x: round(x - 0.1, 1) if x > 0.55 else None, lambda x: round(x + 0.1, 1))
CUSUM_K_GRID = [0.1, 0.2, 0.3, 0.5, 0.8, 1.2]
CUSUM_H_GRID = [5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 320.0]
K_EXT = (lambda x: x / 2 if x > 0.01 else None, lambda x: round(x * 1.5, 4))
H_EXT = (lambda x: x / 2 if x > 1 else None, lambda x: x * 2)
P_GRID = [round(x, 2) for x in np.arange(0.05, 0.96, 0.05)] + [0.97, 0.98, 0.99]
P_EXT = (lambda x: x / 2 if x > 1e-3 else None,
         lambda x: 1 - (1 - x) / 2 if 1 - x > 1e-6 else None)
MAX_EXTENSIONS = 6

# NASIM-GBT versions: (name, hybrid features, class weighting, persist, mismatched model)
GBT_VERSIONS = [
    ("NASIM-GBT v1", False, "full", 1, False),
    ("NASIM-GBT", config.GBT_HYBRID, config.GBT_CLASS_WEIGHT, config.GBT_PERSIST, False),
    ("NASIM-GBT, model +/-20%", config.GBT_HYBRID, config.GBT_CLASS_WEIGHT,
     config.GBT_PERSIST, True),
]


def timestamps(days):
    return pd.to_datetime(days["date"] + " " + days["time"])


def evaluate(alarms, part):
    days, events = part
    return score(timestamps(days), alarms, events, days["date"].nunique())


def better(m, best):
    return best is None or (m["F1"], -m["FA_per_day"]) > (best["F1"], -best["FA_per_day"])


def tune_grid(evaluate_setting, axes, extenders):
    """Grid search on validation. While the best value of an axis sits on that
    axis' edge, extend the axis outward and search again (at most
    MAX_EXTENSIONS times). Returns (best setting tuple, still_at_edge)."""
    axes = [list(a) for a in axes]
    memo = {}
    for _ in range(MAX_EXTENSIONS + 1):
        best, best_s = None, None
        for s in itertools.product(*axes):
            if s not in memo:
                memo[s] = evaluate_setting(s)
            if better(memo[s], best):
                best, best_s = memo[s], s
        moved = False
        for i, ax in enumerate(axes):
            lo, hi = extenders[i]
            new = None
            if best_s[i] == ax[0] and lo:
                new = lo(ax[0])
                if new is not None and new not in ax:
                    ax.insert(0, new)
                    moved = True
            elif best_s[i] == ax[-1] and hi:
                new = hi(ax[-1])
                if new is not None and new not in ax:
                    ax.append(new)
                    moved = True
        if not moved:
            break
    at_edge = any(best_s[i] in (ax[0], ax[-1]) and len(ax) > 1 for i, ax in enumerate(axes))
    return best_s, at_edge


def tune(make, axes, extenders, part):
    """make(*setting) -> detector; streaming run on the validation part."""
    return tune_grid(lambda s: evaluate(stream.run(make(*s), part[0]), part), axes, extenders)


# --- GBT: compute p once per sample, then apply the alarm rule per threshold ---

def p_trace(model, use_wind, days, hybrid=False, model_params=None):
    det = gbt.GbtDetector(model, use_wind, np.inf, hybrid=hybrid, model_params=model_params)
    p = np.empty(len(days))
    for i, (new_day, s) in enumerate(stream.samples(days)):
        if new_day:
            det.reset()
        det.update(s)
        p[i] = det.p
    return p


def alarms_from_p(p, threshold, days, persist=1):
    """Same alarm, persistence and silence logic as GbtDetector.update (tested equal)."""
    out = np.zeros(len(p), dtype=bool)
    date = days["date"].to_numpy()
    quiet = run = 0
    for i in range(len(p)):
        if i == 0 or date[i] != date[i - 1]:
            quiet = run = 0
        if quiet > 0:
            quiet -= 1
        if np.isnan(p[i]):
            run = 0
            continue
        run = run + 1 if p[i] >= threshold else 0
        if quiet > 0:
            continue
        if run >= persist:
            out[i] = True
            quiet = gbt.SILENCE_N
            run = 0
    return out


def tune_p(p, part, persist=1):
    return tune_grid(lambda s: evaluate(alarms_from_p(p, s[0], part[0], persist), part),
                     [P_GRID], [P_EXT])


# --- the experiment -------------------------------------------------------------

def mismatch_sets():
    f = config.MISMATCH_FRAC
    for sc, s0, s1 in itertools.product((-1, 1), repeat=3):
        yield (f"C{sc * f:+.0%} u0{s0 * f:+.0%} u1{s1 * f:+.0%}",
               dict(c=config.HEAT_CAPACITY_J_K * (1 + sc * f),
                    u0=config.FAIMAN_U0 * (1 + s0 * f),
                    u1=config.FAIMAN_U1 * (1 + s1 * f)))


def hybrid_mismatch():
    m = config.HYBRID_MISMATCH
    return dict(c=config.HEAT_CAPACITY_J_K * (1 + m["c_frac"]),
                u0=config.FAIMAN_U0 * (1 + m["u0_frac"]),
                u1=config.FAIMAN_U1 * (1 + m["u1_frac"]))


def variant(hard):
    return "hard" if hard else "base"


def split_sizes(hard):
    return config.HARD_SPLIT_DAYS if hard else config.SPLIT_DAYS


def run_experiment(seed, out, hard=False):
    days, events = synth.generate(seed, n_days=config.HARD_N_DAYS if hard else config.N_DAYS,
                                  hard=hard)
    sdir = pathlib.Path(f"out/synth/{variant(hard)}/seed{seed}")
    sdir.mkdir(parents=True, exist_ok=True)
    days.to_csv(sdir / "days.csv", index=False)
    events.to_csv(sdir / "events.csv", index=False)
    parts = split_days(days, events, split_sizes(hard))
    val, test = parts["val"], parts["test"]
    rows, tuned, alarms_test, models = [], {}, {}, {}

    def record(name, alarms, setting, at_edge=False, **extra):
        m = evaluate(alarms, test)
        rows.append({"detector": name, "setting": setting, "at_edge": at_edge, **m, **extra})
        alarms_test[name] = alarms
        print(f"  [{variant(hard)} {seed}] {name:40s} F1={m['F1']:.2f}  "
              f"FA/day={m['FA_per_day']:.2f}", flush=True)

    record("Threshold (spec)", stream.run(ThresholdDetector(), test[0]),
           f"rise {config.THR_RISE_C} degC")
    (r,), edge = tune(lambda x: ThresholdDetector(rise_c=x), [THR_GRID], [THR_EXT], val)
    tuned["threshold_rise_c"] = {"value": r, "at_edge": edge}
    record("Threshold (tuned)", stream.run(ThresholdDetector(rise_c=r), test[0]),
           f"rise {r} degC", edge)

    for use_wind in (True, False):
        tag = "" if use_wind else " (no wind)"
        (k, h), edge = tune(lambda k, h: CusumDetector(use_wind, k, h),
                            [CUSUM_K_GRID, CUSUM_H_GRID], [K_EXT, H_EXT], val)
        tuned[f"cusum{tag}"] = {"k": k, "h": h, "at_edge": edge}
        record(f"CUSUM{tag}", stream.run(CusumDetector(use_wind, k, h), test[0]),
               f"k {k}, h {h}", edge)
        mis = []
        for label, mp in mismatch_sets():
            (k, h), edge = tune(lambda k, h: CusumDetector(use_wind, k, h, model_params=mp),
                                [CUSUM_K_GRID, CUSUM_H_GRID], [K_EXT, H_EXT], val)
            al = stream.run(CusumDetector(use_wind, k, h, model_params=mp), test[0])
            mis.append((evaluate(al, test)["F1"], label, k, h, al, edge))
        mis.sort(key=lambda x: x[0])
        tuned[f"cusum{tag} mismatch"] = [dict(model=m[1], k=m[2], h=m[3], at_edge=m[5],
                                              test_F1=m[0]) for m in mis]
        for which, m in (("median of 8", mis[3]), ("worst of 8", mis[0])):
            record(f"CUSUM{tag}, model +/-20% ({which})", m[4],
                   f"{m[1]}; k {m[2]}, h {m[3]}", m[5])

    train = parts["train"]
    y, use = gbt.sample_targets(*train)
    for use_wind in (True, False):
        tag = "" if use_wind else " (no wind)"
        for name, hybrid, weighting, persist, mismatch in GBT_VERSIONS:
            mp = hybrid_mismatch() if mismatch else None
            names = feature_names(use_wind, hybrid)
            model = gbt.train(gbt.build_features(train[0], use_wind, hybrid, mp), y, use,
                              seed, names=names, weighting=weighting)
            b = model.booster_
            slug = (f"{'v1' if not hybrid else 'hybrid'}{'_mismatch' if mismatch else ''}"
                    f"_{'wind' if use_wind else 'nowind'}")
            b.save_model(str(out / f"gbt_{slug}.txt"))
            models[f"{name}{tag}"] = b
            (t,), edge = tune_p(p_trace(model, use_wind, val[0], hybrid, mp), val, persist)
            tuned[f"{name}{tag} p_threshold"] = {"value": t, "at_edge": edge}
            gain = b.feature_importance("gain")
            share = (float(gain[names.index("wind_2m")] / gain.sum())
                     if use_wind else float("nan"))
            al = stream.run(gbt.GbtDetector(model, use_wind, t, hybrid=hybrid,
                                            persist=persist, model_params=mp), test[0])
            record(f"{name}{tag}", al,
                   f"p >= {t:.6g}, persist {persist}, weight {weighting}"
                   + (", hybrid" if hybrid else "") + (f", model {config.HYBRID_MISMATCH}"
                                                        if mismatch else ""),
                   edge, wind_2m_gain_share=share)
    return rows, tuned, alarms_test, parts, models


# --- reporting ------------------------------------------------------------------

MAIN = ["detector", "setting", "TP", "FN", "FP", "precision", "recall", "F1",
        "delay_median_min", "FA_per_day", "FA_irradiance_rise", "minor_lulls_alarmed"]
NATURAL = ["detector", "natural_spikes", "natural_detected", "natural_missed",
           "natural_alarms"]


def md_table(df, cols):
    def fmt(x):
        return f"{x:.2f}" if isinstance(x, float) else str(x)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(fmt(r[c]) for c in cols) + " |" for _, r in df.iterrows()]
    return "\n".join(lines)


def plot_days(parts, alarms, out, n=3):
    days, events = parts["test"]
    ev = events.assign(start=pd.to_datetime(events["start"]), end=pd.to_datetime(events["end"]))
    ev["day"] = ev["start"].dt.strftime("%Y-%m-%d")
    by_day = ev.groupby("day")["class"].agg(set)
    want = [{"spike", "irradiance_rise"}, {"mixed"}, {"natural_spike"}, {"spike"}]
    picks = []
    for w in want:
        for d in by_day.index:
            if w <= by_day[d] and d not in picks:
                picks.append(d)
                break
    colours = {"spike": "tab:red", "minor_lull": "tab:orange", "irradiance_rise": "tab:blue",
               "mixed": "tab:gray", "natural_spike": "tab:purple", "natural_minor": "plum",
               "ambient_rise": "tab:green"}
    shown = ["Threshold (spec)", "CUSUM", "NASIM-GBT v1", "NASIM-GBT", "NASIM-GBT (no wind)"]
    ts_all = timestamps(days)
    paths = []
    for d in picks[:n]:
        m = (days["date"] == d).to_numpy()
        ts = ts_all[m]
        fig, ax = plt.subplots(4, 1, figsize=(11, 8.5), sharex=True,
                               gridspec_kw={"height_ratios": [3, 2, 2, 2]})
        for a in ax[:3]:
            for _, e in ev[ev["day"] == d].iterrows():
                a.axvspan(e["start"], e["end"], color=colours[e["class"]], alpha=0.25)
        ax[0].plot(ts, days.loc[m, BACK_SENSORS].mean(axis=1), "k", lw=0.8, label="module (mean T1..T5)")
        ax[0].plot(ts, days.loc[m, "T_ambient"], "g", lw=0.8, label="ambient")
        ax[0].set_ylabel("degC")
        ax[0].legend(fontsize=8, loc="upper left")
        ax[1].plot(ts, days.loc[m, "irradiance"], lw=0.8)
        ax[1].set_ylabel("G in-plane, W/m2")
        ax[2].plot(ts, days.loc[m, "wind"], lw=0.5)
        ax[2].set_ylabel("wind, m/s")
        for j, name in enumerate(shown):
            hits = ts[alarms[name][m]]
            ax[3].plot(hits, np.full(len(hits), j), "v", ms=7)
        ax[3].set_yticks(range(len(shown)), shown, fontsize=8)
        ax[3].set_ylim(-0.7, len(shown) - 0.3)
        ax[3].set_xlabel("local time   (shading: red spike, blue irradiance_rise, "
                         "orange minor_lull, grey mixed, purple natural_spike, green ambient_rise)", fontsize=8)
        ax[0].set_title(f"NASIM - synthetic test day {d} (alarms bottom)", fontsize=10)
        fig.tight_layout()
        p = out / f"test_day_{d}.png"
        fig.savefig(p, dpi=110)
        plt.close(fig)
        paths.append(p.name)
    return paths


def plot_importance(models, out):
    """Gain importance of each NASIM-GBT feature (share of the total gain);
    one row per wind setting, one column per version."""
    names = list(models)
    cols = len(GBT_VERSIONS)
    fig, axes = plt.subplots(2, cols, figsize=(5.2 * cols, 8))
    for ax, name in zip(axes.flat, names):
        b = models[name]
        gain = b.feature_importance("gain")
        share = gain / gain.sum()
        order = np.argsort(share)
        ax.barh(np.array(b.feature_name())[order], share[order])
        ax.set_title(name, fontsize=10)
        ax.set_xlabel("share of total split gain", fontsize=8)
        ax.tick_params(labelsize=8)
    fig.suptitle("NASIM-GBT feature importance (synthetic training days)", fontsize=11)
    fig.tight_layout()
    fig.savefig(out / "gbt_feature_importance.png", dpi=100)
    plt.close(fig)


PUMP_NOTE = ("NASIM-GBT also alarms on minor lulls (< 3 degC, neutral, not scored): "
             "see minor_lulls_alarmed. These alarms are not errors of the scoring, but "
             "they would start the pump on lulls too small to pay; in NASIM the pump "
             "net-power rule (expected gain vs pump energy) must filter them.")
VERSION_NOTE = ("NASIM-GBT v1 = the Step 4 model (full class weighting, alarm on one "
                "sample). NASIM-GBT = current: hybrid (adds CUSUM residual d and "
                "statistic S as features), sqrt class weighting, 2 consecutive samples. "
                "'model +/-20%' = current, with the hybrid's internal thermal model off "
                f"by {config.HYBRID_MISMATCH} (fixed in advance).")


def run_seed(seed, hard):
    """Full experiment + report for one seed; returns the result rows."""
    out = pathlib.Path(f"out/results/{variant(hard)}/seed{seed}")
    out.mkdir(parents=True, exist_ok=True)
    rows, tuned, alarms, parts, models = run_experiment(seed, out, hard)
    df = pd.DataFrame(rows)
    df.to_csv(out / "results.csv", index=False)
    (out / "tuned.json").write_text(json.dumps(tuned, indent=2))
    plots = plot_days(parts, alarms, out)
    plot_importance(models, out)
    counts = parts["test"][1]["class"].value_counts().to_dict()
    spikes = {k: int((v[1]["class"] == "spike").sum()) for k, v in parts.items()}
    sz = split_sizes(hard)
    md = [f"# NASIM - spike detection on synthetic data ({variant(hard)}, seed {seed})", "",
          f"**{BANNER}**", "",
          f"Split by whole days, chronological: {sz[0]} train / {sz[1]} validation / "
          f"{sz[2]} test. All settings tuned on validation only; test run once. "
          f"Spikes per split: {spikes}.", "",
          f"Test-set events: {counts}. `mixed` windows ({counts.get('mixed', 0)}) "
          "are excluded from scoring.", "",
          VERSION_NOTE, "",
          "## Main table (test days)", "",
          md_table(df, MAIN), "",
          "Precision/recall over `spike` events; FP = alarms outside any event or "
          "inside `irradiance_rise` (FA_irradiance_rise counts the latter); "
          "delay = first alarm minus lull onset, minutes. minor_lulls_alarmed: "
          f"minor lulls (< 3 degC, neutral, not scored) with an alarm, out of "
          f"{counts.get('minor_lull', 0)}. Tuned values still on a grid edge after "
          f"extension: {int(df['at_edge'].sum())}.", "",
          f"**Pump rule:** {PUMP_NOTE}", "",
          "## Natural spikes (background wind, scored separately)", "",
          md_table(df, NATURAL), "",
          "## NASIM-GBT feature importance", "",
          "![importance](gbt_feature_importance.png)", "",
          "## Plots", ""] + [f"![{q}]({q})" for q in plots]
    (out / "results.md").write_text("\n".join(md), encoding="utf-8")
    df.insert(0, "seed", seed)
    df["minor_lulls"] = counts.get("minor_lull", 0)
    for k, v in spikes.items():
        df[f"spikes_{k}"] = v
    print(f"[{variant(hard)} seed {seed}] done", flush=True)
    return df


SUMMARY_COLS = ["F1", "precision", "recall", "delay_median_min", "FA_per_day"]


def summarise(all_rows, hard, seeds):
    df = pd.concat(all_rows, ignore_index=True)
    out = pathlib.Path(f"out/results/{variant(hard)}")
    tag = f"seeds{min(seeds)}-{max(seeds)}"
    df.to_csv(out / f"all_{tag}.csv", index=False)
    order = list(dict.fromkeys(df["detector"]))
    g = df.groupby("detector", sort=False)
    first = df[df["detector"] == order[0]]

    def cell(x):
        return f"{x.mean():.2f} +/- {x.std(ddof=1):.2f} ({x.min():.2f}-{x.max():.2f})"

    lines = ["| detector | " + " | ".join(SUMMARY_COLS) +
             " | spikes TP/total | FA on irr. rise | minor lulls alarmed | on grid edge |",
             "|" + "---|" * (len(SUMMARY_COLS) + 5)]
    for name in order:
        x = g.get_group(name)
        lines.append(f"| {name} | " + " | ".join(cell(x[c]) for c in SUMMARY_COLS) +
                     f" | {x['TP'].sum()}/{x['spikes'].sum()} | {x['FA_irradiance_rise'].sum()}"
                     f" | {x['minor_lulls_alarmed'].sum()}/{x['minor_lulls'].sum()}"
                     f" | {int(x['at_edge'].sum())} |")
    wind = ["| model | wind_2m share of gain |", "|---|---|"]
    for name in order:
        x = g.get_group(name)
        if "wind_2m_gain_share" in x and x["wind_2m_gain_share"].notna().any():
            wind.append(f"| {name} | {cell(x['wind_2m_gain_share'])} |")
    nat = ["| detector | natural spikes | detected | missed | alarms |", "|---|---|---|---|---|"]
    for name in order:
        x = g.get_group(name)
        nat.append(f"| {name} | {x['natural_spikes'].sum()} | {x['natural_detected'].sum()} | "
                   f"{x['natural_missed'].sum()} | {x['natural_alarms'].sum()} |")
    md = [f"# NASIM - summary over seeds {list(seeds)} ({variant(hard)} synthetic data)", "",
          f"**{BANNER}**", "",
          "Each cell: mean +/- standard deviation over seeds (min-max). Each seed is a "
          "complete, independent run: new synthetic data, training, tuning on "
          "validation, one test run. Counts are summed over seeds.", "",
          f"Split {split_sizes(hard)} days. Spikes per seed - train "
          f"{first['spikes_train'].tolist()}, validation {first['spikes_val'].tolist()}, "
          f"test {first['spikes_test'].tolist()}; test irradiance rises "
          f"{first['irradiance_rises'].tolist()}; test mixed (excluded) "
          f"{first['mixed_excluded'].tolist()}.", "",
          VERSION_NOTE, "",
          "\n".join(lines), "",
          f"**Pump rule:** {PUMP_NOTE}", "",
          "## How much NASIM-GBT relies on wind_2m", "", "\n".join(wind), "",
          "## Natural spikes (summed over seeds)", "", "\n".join(nat), ""]
    path = out / f"summary_{tag}.md"
    path.write_text("\n".join(md), encoding="utf-8")
    print("\n".join(lines + [""] + wind))
    return path


def main():
    ap = argparse.ArgumentParser(description="NASIM synthetic evaluation")
    ap.add_argument("--seed", type=int, default=None, help="one seed (default 5)")
    ap.add_argument("--seeds", type=int, nargs="+", default=None, help="several seeds")
    ap.add_argument("--hard", action="store_true", help="hard synthetic variant")
    ap.add_argument("--workers", type=int, default=5, help="parallel seeds")
    a = ap.parse_args()
    seeds = a.seeds or [config.SEEDS[0] if a.seed is None else a.seed]
    if len(seeds) == 1:
        run_seed(seeds[0], a.hard)
    else:
        with concurrent.futures.ProcessPoolExecutor(min(a.workers, len(seeds))) as ex:
            rows = list(ex.map(run_seed, seeds, [a.hard] * len(seeds)))
        print(f"-> {summarise(rows, a.hard, seeds)}")
    print(f"\n{BANNER}")


if __name__ == "__main__":
    main()
