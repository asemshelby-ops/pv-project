"""Block diagram for the NASIM report: how one 10 s sample becomes an alarm.

Draws only what the code does (detect/features.py, detect/gbt.py, export/c/nasim.c);
no results. Run: python docs/reports/src/fig_pipeline.py <out.png>
"""
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

INK, MUTED = "#0b0b0b", "#52514e"
BLUE, ORANGE, AQUA, VIOLET = "#2a78d6", "#eb6834", "#1baf7a", "#8a5bd1"

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8})
fig, ax = plt.subplots(figsize=(7.2, 2.9))
ax.set_xlim(0, 100)
ax.set_ylim(0, 42)
ax.axis("off")


def box(x, y, w, h, title, body, colour):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.2",
                                fc=colour + "1f", ec=colour, lw=1.0))
    ax.text(x + w / 2, y + h - 2.2, title, ha="center", va="top", fontsize=8.2,
            fontweight="bold", color=INK)
    ax.text(x + w / 2, y + h - 6.4, body, ha="center", va="top", fontsize=6.9,
            color=INK, linespacing=1.35)


def arrow(x0, x1, y):
    ax.annotate("", xy=(x1, y), xytext=(x0, y),
                arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.0))


Y, H = 5, 35
box(0.5, Y, 17, H, "Sensors (10 s)",
    "5 back-sheet\nDS18B20 sensors:\nmean, then median\nof last 3 samples\n\nPYR20 irradiance,\nin the module plane\n\n"
    "air temperature\n\nRTC date and time", BLUE)
box(21.5, Y, 19, H, "10 features",
    "temperature rise\nover 2, 5, 10 min\n\nirradiance change\nover 2 min; range\nover 10 min\n\n"
    "clear-sky index\nsun elevation\n(T - Ta) / G\nCUSUM residual and S", BLUE)
box(44.5, Y, 17, H, "50 small trees",
    "LightGBM,\ndepth <= 4,\n<= 15 leaves each\n\nsum of tree\noutputs ->\nsigmoid", AQUA)
box(65.5, Y, 16, H, "p(spike)",
    "a number from\n0 to 1, logged\nevery row\n\nalarm when\np >= 0.98 on 2\nsamples running\n(20 s)", AQUA)
box(85.5, Y, 14, H, "Alarm",
    "bit 1 of the\n'alarm' column\n\nthen 10 min\nsilence\n\n(later: input\nto cooling)", ORANGE)
for x0, x1 in ((18.3, 21.0), (41.3, 44.0), (62.3, 65.0), (82.3, 85.0)):
    arrow(x0, x1, Y + H / 2)
ax.text(50, 1.2, "Same feature code in Python (training) and C on the ESP32 (inference); "
        "only past samples are used.", ha="center", va="center", fontsize=6.8,
        color=MUTED, style="italic")
fig.savefig(sys.argv[1], dpi=300, bbox_inches="tight")
