"""Per-cause recall and median delay (SYNTHETIC test days) for the NASIM report.

Reads table3_per_cause.csv of a pipeline run, so the bars carry the pipeline's numbers.
Run: python docs/reports/src/fig_per_cause.py <results folder> <out.png>
"""
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

INK, MUTED, GRID = "#0b0b0b", "#52514e", "#d9d8d4"
SLOT = ["#2a78d6", "#eb6834", "#1baf7a", "#8a5bd1"]
DET = [("Threshold (tuned)", SLOT[0], ""), ("CUSUM", SLOT[1], "////"),
       ("Irradiance step", SLOT[3], "...."), ("NASIM-GBT", SLOT[2], "xxxx")]
CAUSES = ["irradiance", "non_irradiance", "mixed"]

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8,
                     "axes.spines.top": False, "axes.spines.right": False,
                     "axes.edgecolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
                     "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.5,
                     "axes.axisbelow": True, "legend.frameon": False, "hatch.linewidth": 0.6})

t = pd.read_csv(f"{sys.argv[1]}/table3_per_cause.csv", na_values=["-"])
t["cause"] = t["cause"].ffill()
t["n"] = t["n"].ffill()
print(t.to_string())
det_col, cause_col = "detector", "cause"
fig, ax = plt.subplots(1, 2, figsize=(7.2, 2.6), gridspec_kw={"wspace": 0.25})
x = np.arange(len(CAUSES))
w = 0.2
for k, (name, colour, hatch) in enumerate(DET):
    rec, dly = [], []
    for c in CAUSES:
        r = t[(t[det_col] == name) & (t[cause_col] == c)]
        rec.append(float(r["recall"].iloc[0]))
        dly.append(float(r["delay min"].iloc[0]))
    for a, vals in ((ax[0], rec), (ax[1], dly)):
        a.bar(x + (k - 1.5) * w, vals, w, color=colour + "cc", edgecolor=colour,
              hatch=hatch, lw=0.6, label=name)
labels = [f"{c}\n(n = {int(t[(t[det_col] == DET[0][0]) & (t[cause_col] == c)]['n'].iloc[0])})"
          for c in CAUSES]
for a in ax:
    a.set_xticks(x, labels)
ax[0].set_ylabel("Recall")
ax[0].set_ylim(0, 1.05)
ax[1].set_ylabel("Median delay (min)")
ax[0].legend(loc="lower center", bbox_to_anchor=(1.1, 1.0), ncols=4)
fig.text(0.5, -0.06, "SYNTHETIC DATA", ha="center", fontsize=7, color="#b03a10",
         fontweight="bold")
fig.savefig(sys.argv[2], dpi=300, bbox_inches="tight")
