"""One command for Steps 2-4:
    python -m eval.run --seed 0            one seed, base synthetic data
    python -m eval.run --seeds 0 1 2 3 4   several seeds + mean/spread summary
    python -m eval.run --seeds 0 1 2 3 4 --hard   the same on the hard variant

  1. generate the synthetic days (data/synth.py)
  2. split chronologically by whole days: train / validation / test
  3. train NASIM-GBT (with and without wind) on the training days
  4. tune every tunable setting on the VALIDATION days only (best event F1,
     ties broken by fewer false alarms per day)
  5. run every detector once, sample by sample, on the TEST days and score it
  6. write out/results/<base|hard>/seed<N>/: results.md, results.csv,
     tuned.json, example-day plots, feature importance, the trained models
  7. with several seeds: out/results/<base|hard>/summary.md (mean +/- spread)

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
from detect.features import feature_names  # noqa: E402
from detect.cusum import CusumDetector  # noqa: E402
from detect.threshold import ThresholdDetector  # noqa: E402
from eval.metrics import score  # noqa: E402
from eval.split import split_days  # noqa: E402

BANNER = ("SYNTHETIC DATA. These numbers show that the pipeline works end to end "
          "(generation -> streaming detection -> event scoring). They do NOT show "
          "that the method works on the real module; that needs Phase 1 data.")

THR_GRID = [round(x, 1) for x in np.arange(1.0, 4.01, 0.1)]
CUSUM_K_GRID = [0.1, 0.2, 0.3, 0.5, 0.8, 1.2]
CUSUM_H_GRID = [5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 320.0]
P_GRID = [round(x, 2) for x in np.arange(0.05, 0.96, 0.05)] + [0.97, 0.98, 0.99, 0.995, 0.999, 0.9995, 0.9999]


def timestamps(days):
    return pd.to_datetime(days["date"] + " " + days["time"])


def evaluate(alarms, part):
    days, events = part
    return score(timestamps(days), alarms, events, days["date"].nunique())


def better(m, best):
    return best is None or (m["F1"], -m["FA_per_day"]) > (best["F1"], -best["FA_per_day"])


def tune(make, grid, part):
    """Best setting on the validation part: make(setting) -> detector."""
    best, best_s = None, None
    for s in grid:
        m = evaluate(stream.run(make(s), part[0]), part)
        if better(m, best):
            best, best_s = m, s
    return best_s


# --- GBT: compute p once per sample, then apply the alarm rule per threshold ---

def p_trace(model, use_wind, days):
    det = gbt.GbtDetector(model, use_wind, p_threshold=np.inf)
    p = np.empty(len(days))
    for i, (new_day, s) in enumerate(stream.samples(days)):
        if new_day:
            det.reset()
        det.update(s)
        p[i] = det.p
    return p


def alarms_from_p(p, threshold, days):
    """Same alarm and silence logic as GbtDetector.update (tested equal)."""
    out = np.zeros(len(p), dtype=bool)
    date = days["date"].to_numpy()
    quiet = 0
    for i in range(len(p)):
        if i == 0 or date[i] != date[i - 1]:
            quiet = 0
        if quiet > 0:
            quiet -= 1
        if np.isnan(p[i]) or quiet > 0:
            continue
        if p[i] >= threshold:
            out[i] = True
            quiet = gbt.SILENCE_N
    return out


def tune_p(p, part):
    best, best_t = None, None
    for t in P_GRID:
        m = evaluate(alarms_from_p(p, t, part[0]), part)
        if better(m, best):
            best, best_t = m, t
    return best_t


# --- the experiment -------------------------------------------------------------

def mismatch_sets():
    f = config.MISMATCH_FRAC
    for sc, s0, s1 in itertools.product((-1, 1), repeat=3):
        yield (f"C{sc * f:+.0%} u0{s0 * f:+.0%} u1{s1 * f:+.0%}",
               dict(c=config.HEAT_CAPACITY_J_K * (1 + sc * f),
                    u0=config.FAIMAN_U0 * (1 + s0 * f),
                    u1=config.FAIMAN_U1 * (1 + s1 * f)))


def variant(hard):
    return "hard" if hard else "base"


def run_experiment(seed, out, hard=False):
    days, events = synth.generate(seed, hard=hard)
    sdir = pathlib.Path(f"out/synth/{variant(hard)}/seed{seed}")
    sdir.mkdir(parents=True, exist_ok=True)
    days.to_csv(sdir / "days.csv", index=False)
    events.to_csv(sdir / "events.csv", index=False)
    parts = split_days(days, events)
    val, test = parts["val"], parts["test"]
    rows, tuned, alarms_test = [], {}, {}

    def record(name, alarms, setting):
        m = evaluate(alarms, test)
        rows.append({"detector": name, "setting": setting, **m})
        alarms_test[name] = alarms
        print(f"  {name:38s} F1={m['F1']:.2f}  FA/day={m['FA_per_day']:.2f}")

    print("Threshold ...")
    record("Threshold (spec)", stream.run(ThresholdDetector(), test[0]),
           f"rise {config.THR_RISE_C} degC")
    r = tune(lambda x: ThresholdDetector(rise_c=x), THR_GRID, val)
    tuned["threshold_rise_c"] = r
    record("Threshold (tuned)", stream.run(ThresholdDetector(rise_c=r), test[0]),
           f"rise {r} degC")

    kh = list(itertools.product(CUSUM_K_GRID, CUSUM_H_GRID))
    for use_wind in (True, False):
        tag = "" if use_wind else " (no wind)"
        print(f"CUSUM{tag} ...")
        k, h = tune(lambda s: CusumDetector(use_wind, *s), kh, val)
        tuned[f"cusum{tag}"] = {"k": k, "h": h}
        record(f"CUSUM{tag}", stream.run(CusumDetector(use_wind, k, h), test[0]),
               f"k {k}, h {h}")
        mis = []
        for label, mp in mismatch_sets():
            k, h = tune(lambda s: CusumDetector(use_wind, *s, model_params=mp), kh, val)
            al = stream.run(CusumDetector(use_wind, k, h, model_params=mp), test[0])
            mis.append((evaluate(al, test)["F1"], label, k, h, al))
        mis.sort(key=lambda x: x[0])
        tuned[f"cusum{tag} mismatch"] = [dict(model=m[1], k=m[2], h=m[3], test_F1=m[0])
                                         for m in mis]
        for which, m in (("median of 8", mis[3]), ("worst of 8", mis[0])):
            record(f"CUSUM{tag}, model +/-20% ({which})", m[4],
                   f"{m[1]}; k {m[2]}, h {m[3]}")

    train = parts["train"]
    y, use = gbt.sample_targets(*train)
    models = {}
    for use_wind in (True, False):
        tag = "" if use_wind else " (no wind)"
        print(f"NASIM-GBT{tag} ...")
        model = gbt.train(gbt.build_features(train[0], use_wind), y, use, seed,
                          names=feature_names(use_wind))
        model.booster_.save_model(str(out / f"gbt{'_wind' if use_wind else '_nowind'}.txt"))
        models[f"NASIM-GBT{tag}"] = model.booster_
        t = tune_p(p_trace(model, use_wind, val[0]), val)
        tuned[f"gbt{tag}_p_threshold"] = t
        record(f"NASIM-GBT{tag}",
               stream.run(gbt.GbtDetector(model, use_wind, p_threshold=t), test[0]),
               f"p >= {t}")
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
    shown = ["Threshold (spec)", "CUSUM", "NASIM-GBT", "NASIM-GBT (no wind)"]
    ts_all = timestamps(days)
    paths = []
    for d in picks[:n]:
        m = (days["date"] == d).to_numpy()
        ts = ts_all[m]
        fig, ax = plt.subplots(4, 1, figsize=(11, 8), sharex=True,
                               gridspec_kw={"height_ratios": [3, 2, 2, 1.6]})
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
    """Gain importance of each NASIM-GBT feature (share of the total gain)."""
    fig, axes = plt.subplots(1, len(models), figsize=(5.5 * len(models), 3.8))
    for ax, (name, b) in zip(np.atleast_1d(axes), models.items()):
        gain = b.feature_importance("gain")
        share = gain / gain.sum()
        order = np.argsort(share)
        ax.barh(np.array(b.feature_name())[order], share[order])
        ax.set_title(name, fontsize=10)
        ax.set_xlabel("share of total split gain")
    fig.suptitle("NASIM-GBT feature importance (synthetic training days)", fontsize=10)
    fig.tight_layout()
    fig.savefig(out / "gbt_feature_importance.png", dpi=110)
    plt.close(fig)


PUMP_NOTE = ("NASIM-GBT also alarms on minor lulls (< 3 degC, neutral, not scored): "
             "see minor_lulls_alarmed. These alarms are not errors of the scoring, but "
             "they would start the pump on lulls too small to pay; in NASIM the pump "
             "net-power rule (expected gain vs pump energy) must filter them.")


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
    md = [f"# NASIM - spike detection on synthetic data ({variant(hard)}, seed {seed})", "",
          f"**{BANNER}**", "",
          f"Split by whole days, chronological: {config.SPLIT_DAYS[0]} train / "
          f"{config.SPLIT_DAYS[1]} validation / {config.SPLIT_DAYS[2]} test. "
          "All settings tuned on validation only; test run once.", "",
          f"Test-set events: {counts}. `mixed` windows ({counts.get('mixed', 0)}) "
          "are excluded from scoring.", "",
          "## Main table (test days)", "",
          md_table(df, MAIN), "",
          "Precision/recall over `spike` events; FP = alarms outside any event or "
          "inside `irradiance_rise` (FA_irradiance_rise counts the latter); "
          "delay = first alarm minus lull onset, minutes. minor_lulls_alarmed: "
          f"minor lulls (< 3 degC, neutral, not scored) with an alarm, out of "
          f"{counts.get('minor_lull', 0)}.", "",
          f"**Pump rule:** {PUMP_NOTE}", "",
          "## Natural spikes (background wind, scored separately)", "",
          md_table(df, NATURAL), "",
          "## NASIM-GBT feature importance", "",
          "![importance](gbt_feature_importance.png)", "",
          "## Plots", ""] + [f"![{q}]({q})" for q in plots]
    (out / "results.md").write_text("\n".join(md), encoding="utf-8")
    df.insert(0, "seed", seed)
    df["minor_lulls"] = counts.get("minor_lull", 0)
    print(f"[{variant(hard)} seed {seed}] done")
    return df


SUMMARY_COLS = ["F1", "precision", "recall", "delay_median_min", "FA_per_day"]


def summarise(all_rows, hard, seeds):
    df = pd.concat(all_rows, ignore_index=True)
    out = pathlib.Path(f"out/results/{variant(hard)}")
    df.to_csv(out / "all_seeds.csv", index=False)
    order = list(dict.fromkeys(df["detector"]))
    g = df.groupby("detector", sort=False)

    def cell(x):
        return f"{x.mean():.2f} +/- {x.std(ddof=1):.2f} ({x.min():.2f}-{x.max():.2f})"

    lines = ["| detector | " + " | ".join(SUMMARY_COLS) +
             " | spikes TP/total | FA on irr. rise | minor lulls alarmed |",
             "|" + "---|" * (len(SUMMARY_COLS) + 4)]
    for name in order:
        x = g.get_group(name)
        lines.append(f"| {name} | " + " | ".join(cell(x[c]) for c in SUMMARY_COLS) +
                     f" | {x['TP'].sum()}/{x['spikes'].sum()} | {x['FA_irradiance_rise'].sum()}"
                     f" | {x['minor_lulls_alarmed'].sum()}/{x['minor_lulls'].sum()} |")
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
          "Test-set events per seed: spikes "
          f"{df[df['detector'] == order[0]]['spikes'].tolist()}, irradiance rises "
          f"{df[df['detector'] == order[0]]['irradiance_rises'].tolist()}, mixed (excluded) "
          f"{df[df['detector'] == order[0]]['mixed_excluded'].tolist()}.", "",
          md_table_raw(lines), "",
          f"**Pump rule:** {PUMP_NOTE}", "",
          "## Natural spikes (summed over seeds)", "", md_table_raw(nat), ""]
    (out / "summary.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(lines))
    return out / "summary.md"


def md_table_raw(lines):
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="NASIM synthetic evaluation")
    ap.add_argument("--seed", type=int, default=None, help="one seed (default 0)")
    ap.add_argument("--seeds", type=int, nargs="+", default=None, help="several seeds")
    ap.add_argument("--hard", action="store_true", help="hard synthetic variant")
    ap.add_argument("--workers", type=int, default=5, help="parallel seeds")
    a = ap.parse_args()
    seeds = a.seeds or [0 if a.seed is None else a.seed]
    if len(seeds) == 1:
        run_seed(seeds[0], a.hard)
    else:
        with concurrent.futures.ProcessPoolExecutor(min(a.workers, len(seeds))) as ex:
            rows = list(ex.map(run_seed, seeds, [a.hard] * len(seeds)))
        print(f"-> {summarise(rows, a.hard, seeds)}")
    print(f"\n{BANNER}")


if __name__ == "__main__":
    main()
