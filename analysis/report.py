"""Figures and tables of the paper, from the pipeline's result objects.

Figures: PDF (vector, for LaTeX) and PNG (300 dpi), IEEE column widths, 8 pt serif.
Tables: CSV (the numbers) and a LaTeX `tabular` (booktabs) for the IEEE template.

Colour: three categorical slots in a fixed order (blue, orange, aqua), the default
palette of the charting guideline used for this project. Every series also differs by
marker, line style or hatch, so the figures survive greyscale printing. One y-scale per
panel: quantities with different units get their own panel, never a second axis.
"""
import pathlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from analysis import events as ev_mod  # noqa: E402
from analysis.metrics import CAUSES  # noqa: E402
from data.schema import BACK_SENSORS  # noqa: E402

INK, MUTED, GRID = "#0b0b0b", "#52514e", "#d9d8d4"
SLOT = ["#2a78d6", "#eb6834", "#1baf7a"]
CAUSE_STYLE = {"irradiance": (SLOT[0], "", "irradiance-driven"),
               "non_irradiance": (SLOT[1], "////", "not irradiance-driven"),
               "mixed": (SLOT[2], "....", "mixed")}
DETECTORS = ["Threshold (tuned)", "CUSUM", "NASIM-GBT"]
DET_STYLE = {"Threshold (tuned)": (SLOT[0], "-", "s"), "CUSUM": (SLOT[1], "--", "^"),
             "NASIM-GBT": (SLOT[2], "-.", "o")}
ONE_COL, TWO_COL = 3.5, 7.16


def style():
    plt.rcParams.update({
        "font.family": "serif", "font.size": 8, "axes.titlesize": 8, "axes.labelsize": 8,
        "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7,
        "axes.linewidth": 0.6, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
        "xtick.color": MUTED, "ytick.color": MUTED, "text.color": INK,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.5,
        "lines.linewidth": 1.2, "legend.frameon": False,
        "savefig.dpi": 300, "savefig.bbox": "tight", "pdf.fonttype": 42})


def _save(fig, out, name):
    for ext in ("pdf", "png"):
        fig.savefig(pathlib.Path(out) / f"{name}.{ext}")
    plt.close(fig)
    return f"{name}.pdf"


def fig_example_day(frame, events, alarms, date, out, synthetic=False):
    d = frame[frame["date"] == date]
    m = (frame["date"] == date).to_numpy()
    t = d["t_local"]
    fig, ax = plt.subplots(3, 1, figsize=(TWO_COL, 3.9), sharex=True,
                           gridspec_kw={"height_ratios": [3, 2, 1.1], "hspace": 0.12})
    ev = events[(events["date_local"] == date) & (events["class"] == "spike") & events["valid"]]
    seen = set()
    for _, e in ev.iterrows():
        colour, hatch, text = CAUSE_STYLE[e["cause"]]
        for a in ax[:2]:
            a.axvspan(pd.Timestamp(e["start"]), pd.Timestamp(e["end"]), facecolor=colour,
                      alpha=0.16, hatch=hatch, edgecolor=colour, linewidth=0,
                      label=None if e["cause"] in seen or a is ax[1] else f"spike, {text}")
        seen.add(e["cause"])
    ax[0].plot(t, d[BACK_SENSORS].mean(axis=1), color=INK, lw=0.9, label="module (mean of 5)")
    ax[0].plot(t, d["T_ambient"], color=MUTED, lw=0.8, ls=":", label="air")
    ax[0].set_ylabel("Temperature (°C)")
    ax[0].legend(loc="upper left", ncols=3)
    ax[1].plot(t, d["irradiance"], color=INK, lw=0.8)
    ax[1].set_ylabel("Irradiance (W/m²)")
    for k, name in enumerate(DETECTORS):
        colour, _, marker = DET_STYLE[name]
        hits = t[alarms[name][m]]
        ax[2].plot(hits, np.full(len(hits), k), marker, color=colour, ms=4.5, ls="none",
                   markeredgecolor="white", markeredgewidth=0.5)
    ax[2].set_yticks(range(len(DETECTORS)), DETECTORS)
    ax[2].set_ylim(-0.6, len(DETECTORS) - 0.4)
    ax[2].grid(axis="y", visible=False)
    ax[2].set_xlabel(f"Local time, {date}" + ("  (synthetic data)" if synthetic else ""))
    ax[2].xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%H:%M"))
    return _save(fig, out, "fig1_example_day")


def fig_spike_statistics(events, n_days, out):
    sp = events[(events["class"] == "spike") & events["valid"]]
    fig, ax = plt.subplots(1, 2, figsize=(TWO_COL, 2.3), gridspec_kw={"wspace": 0.28})
    edges = np.arange(3.0, max(sp["size_C"].max(), 4) + 1.0, 1.0)
    bottom = np.zeros(len(edges) - 1)
    for c in CAUSES:
        colour, hatch, text = CAUSE_STYLE[c]
        h, _ = np.histogram(sp[sp["cause"] == c]["size_C"], edges)
        ax[0].bar(edges[:-1], h, width=0.9, align="edge", bottom=bottom, color=colour,
                  hatch=hatch, edgecolor="white", linewidth=0.8, label=f"{text} (n = {h.sum()})")
        bottom += h
    ax[0].set_xlabel("Spike size (°C)")
    ax[0].set_ylabel("Number of spikes")
    ax[0].legend()
    per = sp.groupby("date_local").size().reindex(sorted(events["date_local"].unique()), fill_value=0)
    counts = np.bincount(np.concatenate([per.to_numpy(), np.zeros(n_days - len(per), int)]))
    ax[1].bar(np.arange(len(counts)), counts, width=0.8, color=MUTED, edgecolor="white")
    ax[1].set_xlabel("Spikes per day")
    ax[1].set_ylabel("Number of days")
    ax[1].set_xticks(np.arange(len(counts)))
    return _save(fig, out, "fig2_spike_statistics")


def fig_detection_delay(detail, out):
    fig, ax = plt.subplots(figsize=(ONE_COL, 2.4))
    lead = []
    for name in DETECTORS:
        m = detail[name][1]
        x = np.sort(m[m["detected"]]["delay_s"].to_numpy()) / 60
        y = np.arange(1, len(x) + 1) / max(len(m), 1)
        colour, ls, _ = DET_STYLE[name]
        ax.step(np.concatenate([[0], x]), np.concatenate([[0], y]), where="post",
                color=colour, ls=ls, label=name)
        lead.append(m)
    cross = (pd.concat(lead)["delay_s"] + pd.concat(lead)["lead_s"]).median() / 60
    if np.isfinite(cross):
        ax.axvline(cross, color=MUTED, lw=0.7, ls=":")
        ax.text(cross, 0.04, " rise reaches 3 °C\n (median)", color=MUTED, fontsize=6.5, va="bottom")
    ax.set_xlabel("Time from spike onset to alarm (min)")
    ax.set_ylabel("Share of spikes detected")
    ax.set_ylim(0, 1.02)
    ax.set_xlim(0, None)
    ax.legend(loc="lower right")
    return _save(fig, out, "fig3_detection_delay")


def fig_gamma(runs, summary, out):
    fig, ax = plt.subplots(figsize=(ONE_COL, 2.4))
    ok = [r for r in runs if r["valid"]]
    for r in ok:
        ax.plot(r["T_cell"], r["P_star"] / r["P25_W"], "o", ms=1.8, color=SLOT[0], alpha=0.45,
                markeredgewidth=0)
    x = np.array([min(r["T_cell"].min() for r in ok), max(r["T_cell"].max() for r in ok)])
    ax.plot(x, 1 + summary["gamma"] * (x - 25), color=INK, lw=1.0)
    ax.text(0.97, 0.95, f"γ = ({100 * summary['gamma']:.3f} ± {100 * summary['U_k2']:.3f}) %/°C\n"
            f"{summary['n_runs']} runs, {summary['n_days']} days (k = 2)",
            transform=ax.transAxes, ha="right", va="top", fontsize=7)
    ax.set_xlabel("Cell temperature (°C)")
    ax.set_ylabel("P$_{mp}$ / P$_{mp}$(25 °C), irradiance-corrected")
    return _save(fig, out, "fig4_gamma")


def fig_validation(esp, ref_values, name, out):
    """Bland-Altman plot: ESP32 minus reference against their mean, one quantity."""
    ok = ~(np.isnan(esp) | np.isnan(ref_values))
    d, mean = esp[ok] - ref_values[ok], (esp[ok] + ref_values[ok]) / 2
    bias, sd = d.mean(), d.std(ddof=1)
    fig, ax = plt.subplots(figsize=(ONE_COL, 2.4))
    ax.plot(mean, d, "o", ms=1.4, color=SLOT[0], alpha=0.35, markeredgewidth=0)
    for y, ls, text in ((bias, "-", "bias"), (bias - 1.96 * sd, "--", "−1.96 s"),
                        (bias + 1.96 * sd, "--", "+1.96 s")):
        ax.axhline(y, color=INK, lw=0.8, ls=ls)
        ax.text(mean.max(), y, f" {text} {y:+.2f}", va="bottom", ha="right", fontsize=6.5)
    ax.set_xlabel(f"Mean of ESP32 and reference, {name}")
    ax.set_ylabel("ESP32 − reference")
    return _save(fig, out, "fig5_validation")


# --- tables -----------------------------------------------------------------------------

def latex(df, caption, label):
    esc = lambda s: str(s).replace("_", r"\_").replace("%", r"\%").replace("°", r"$^\circ$")  # noqa: E731
    head = " & ".join(esc(c) for c in df.columns)
    body = "\n".join(" & ".join(esc(v) for v in row) + r" \\" for row in df.astype(str).to_numpy())
    return (f"\\begin{{table}}[t]\n\\caption{{{caption}}}\n\\label{{{label}}}\n\\centering\n"
            f"\\begin{{tabular}}{{l{'r' * (len(df.columns) - 1)}}}\n\\toprule\n{head} \\\\\n"
            f"\\midrule\n{body}\n\\bottomrule\n\\end{{tabular}}\n\\end{{table}}\n")


def _write(df, out, name, caption):
    out = pathlib.Path(out)
    df.to_csv(out / f"{name}.csv", index=False)
    (out / f"{name}.tex").write_text(latex(df, caption, f"tab:{name}"), encoding="utf-8")
    return df


def table_spikes(events, n_days, gamma=None, out="."):
    sp = events[(events["class"] == "spike") & events["valid"]]
    rows = []
    for c, part in [(c, sp[sp["cause"] == c]) for c in CAUSES] + [("all", sp)]:
        if not len(part):
            continue
        q = part["size_C"].quantile([0.25, 0.5, 0.75])
        row = {"cause": c, "spikes": len(part), "per day": f"{len(part) / n_days:.2f}",
               "size °C, median (IQR)": f"{q[0.5]:.1f} ({q[0.25]:.1f}-{q[0.75]:.1f})",
               "duration min, median": f"{part['duration_s'].median() / 60:.1f}",
               "max rate °C/min, median": f"{part['max_rate_C_per_min'].median():.2f}"}
        if gamma is not None:
            d_cell = part["size_C"] + ev_mod.config.CELL_BACK_DT_C * part["dG"].clip(lower=0) / 1000
            row["power cost %, median"] = f"{-100 * gamma * d_cell.median():.1f}"
        rows.append(row)
    return _write(pd.DataFrame(rows), out, "table1_spike_statistics",
                  "Thermal spikes by cause (valid daytime periods).")


def table_detection(results, out="."):
    r = results[results["detector"].isin(["Threshold (fixed 3.0)"] + DETECTORS)]
    ci = lambda x, k, f: f"{x[k]:{f}} ({x[k + '_lo']:{f}}-{x[k + '_hi']:{f}})"  # noqa: E731
    t = pd.DataFrame({
        "detector": r["detector"], "TP/FN/FP": [f"{a}/{b}/{c}" for a, b, c in zip(r.TP, r.FN, r.FP)],
        "precision": r["precision"].map("{:.2f}".format),
        "recall": [ci(x, "recall", ".2f") for _, x in r.iterrows()],
        "F1": [ci(x, "F1", ".2f") for _, x in r.iterrows()],
        "false alarms/day": [ci(x, "FA_per_day", ".2f") for _, x in r.iterrows()],
        "delay min, median (IQR)": [f"{x.delay_median_min:.1f} ({x.delay_q25_min:.1f}-{x.delay_q75_min:.1f})"
                                    for _, x in r.iterrows()],
        "lead min": r["lead_median_min"].map("{:+.1f}".format),
        "rise left": r["remaining_median"].map("{:.0%}".format)})
    return _write(t, out, "table2_detection",
                  "Event-level detection on the test days (95 % day-bootstrap intervals).")


def table_per_cause(results, out="."):
    r = results[results["detector"].isin(DETECTORS)]
    t = pd.DataFrame({"detector": r["detector"]})
    for c in CAUSES:
        t[f"{c}: n"] = r[f"n_{c}"].to_numpy()
        t[f"{c}: recall"] = r[f"recall_{c}"].map("{:.2f}".format).to_numpy()
        t[f"{c}: F1"] = r[f"F1_{c}"].map("{:.2f}".format).to_numpy()
    return _write(t, out, "table3_per_cause",
                  "Recall and F1 per cause; precision uses all false alarms.")


def table_gamma(runs, summary, out="."):
    rows = [{"run": str(r["t_uncover"])[:16], "sweeps": r["n_sweeps"],
             "span °C": f"{r['T_span_C']:.1f}", "G W/m²": f"{r['G_ref']:.0f}",
             "γ %/°C": f"{100 * r['gamma']:.3f}", "γ (Isc-normalised) %/°C": f"{100 * r['gamma_isc_normalised']:.3f}",
             "α %/°C": f"{100 * r['alpha']:.3f}", "β %/°C": f"{100 * r['beta']:.3f}"}
            for r in runs if r["valid"]]
    rows.append({"run": f"mean of {summary['n_runs']} runs", "sweeps": "", "span °C": "", "G W/m²": "",
                 "γ %/°C": f"{100 * summary['gamma']:.3f} ± {100 * summary['U_k2']:.3f}",
                 "γ (Isc-normalised) %/°C": f"{100 * summary['gamma_isc_normalised']:.3f}",
                 "α %/°C": f"{100 * summary['alpha']:.3f}", "β %/°C": f"{100 * summary['beta']:.3f}"})
    return _write(pd.DataFrame(rows), out, "table4_gamma",
                  "Temperature coefficients from shade-and-uncover runs (expanded uncertainty, k = 2).")


def table_validation(table, out="."):
    t = table.copy()
    for c in ("bias", "loa_low", "loa_high", "rmse", "slope"):
        t[c] = t[c].map(lambda v: "" if pd.isna(v) else f"{v:.3f}")
    t["passes"] = t["passes"].map({True: "yes", False: "no"})
    return _write(t[["quantity", "n", "bias", "loa_low", "loa_high", "rmse", "slope", "passes"]],
                  out, "table5_validation",
                  "ESP32 instrument against the reference logger (ESP32 minus reference).")


def table_cause_check(events, out="."):
    """How the automatic cause rule agrees with known causes: synthetic truth if present,
    and controlled tests (mesh = irradiance, windbreak = not irradiance)."""
    sp = events[(events["class"] == "spike") & events["valid"]]
    parts = []
    if "cause_true" in sp:
        parts.append(pd.crosstab(sp["cause_true"], sp["cause"]).rename_axis("known cause"))
    ctl = sp[sp["origin"] != "natural"]
    if len(ctl):
        parts.append(pd.crosstab(ctl["origin"], ctl["cause"]).rename_axis("known cause"))
    if not parts:
        return None
    t = pd.concat(parts).reindex(columns=CAUSES, fill_value=0).fillna(0).astype(int).reset_index()
    return _write(t, out, "table6_cause_check",
                  "Automatic cause label (columns) against the known cause (rows).")
