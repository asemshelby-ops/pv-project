"""Builds docs/reports/NASIM-model-report.docx (then: soffice --convert-to pdf).

Every number in this file is copied from the repository, and each block says where from:
docs/paper/03 (definition, criteria), docs/paper/11 (synthetic results, parity),
docs/paper/07 sec. 7 (build size, timing), out/paper_demo/seed5/verdict.json and
parity.json (regenerated with analysis.demo_synth and analysis.export_model).
ALL RESULTS ARE SYNTHETIC.

Run from the repository root:  python docs/reports/src/build_report.py
"""
import pathlib

from docx import Document
from docx.enum.section import WD_ORIENT  # noqa: F401
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK  # noqa: F401
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

HERE = pathlib.Path(__file__).resolve().parent
FIGS = HERE / "figs"
OUT = HERE.parent / "NASIM-model-report.docx"

INK = RGBColor(0x0B, 0x0B, 0x0B)
MUTED = RGBColor(0x52, 0x51, 0x4E)
ACCENT = RGBColor(0x1F, 0x5F, 0xB0)
WARN = RGBColor(0xB0, 0x3A, 0x10)
HEAD_FILL = "E8EEF7"
WARN_FILL = "FCEDE6"
NOTE_FILL = "F3F2EF"

doc = Document()

# ---------------------------------------------------------------- page and styles
sec = doc.sections[0]
sec.page_height, sec.page_width = Cm(29.7), Cm(21.0)
sec.left_margin = sec.right_margin = Cm(2.2)
sec.top_margin, sec.bottom_margin = Cm(2.0), Cm(1.9)
sec.header_distance = sec.footer_distance = Cm(0.9)

st = doc.styles["Normal"]
st.font.name = "Calibri"
st.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
st.font.size = Pt(10)
st.font.color.rgb = INK
st.paragraph_format.space_after = Pt(4)
st.paragraph_format.line_spacing = 1.07

for name, size, before, after in (("Heading 1", 13.5, 11, 4), ("Heading 2", 11, 7, 2)):
    h = doc.styles[name]
    h.font.name = "Calibri"
    h.element.rPr.rFonts.set(qn("w:asciiTheme"), "")
    h.font.size = Pt(size)
    h.font.bold = True
    h.font.color.rgb = ACCENT
    h.paragraph_format.space_before = Pt(before)
    h.paragraph_format.space_after = Pt(after)
    h.paragraph_format.keep_with_next = True

for name in ("List Bullet", "List Number"):
    s = doc.styles[name]
    s.font.name = "Calibri"
    s.font.size = Pt(10)
    s.paragraph_format.space_after = Pt(2.5)


def shade(cell_or_par, fill):
    el = cell_or_par._tc if hasattr(cell_or_par, "_tc") else cell_or_par._p
    pr = el.get_or_add_tcPr() if hasattr(cell_or_par, "_tc") else el.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    pr.append(shd)


def field(par, code):
    """Insert a Word field (PAGE, NUMPAGES) into a paragraph."""
    r = par.add_run()
    b = OxmlElement("w:fldChar")
    b.set(qn("w:fldCharType"), "begin")
    t = OxmlElement("w:instrText")
    t.set(qn("xml:space"), "preserve")
    t.text = code
    e = OxmlElement("w:fldChar")
    e.set(qn("w:fldCharType"), "end")
    r._r.append(b)
    r._r.append(t)
    r._r.append(e)
    r.font.size = Pt(8.5)
    r.font.color.rgb = MUTED


def rich(par, text, size=None, color=None):
    """Add text with **bold** spans."""
    parts = text.split("**")
    for i, chunk in enumerate(parts):
        if not chunk:
            continue
        r = par.add_run(chunk)
        r.bold = i % 2 == 1
        if size:
            r.font.size = Pt(size)
        if color:
            r.font.color.rgb = color
    return par


def P(text, style=None, align=None, size=None, color=None, after=None):
    p = doc.add_paragraph(style=style)
    rich(p, text, size, color)
    if align:
        p.alignment = align
    if after is not None:
        p.paragraph_format.space_after = Pt(after)
    return p


def B(text):
    return P(text, style="List Bullet")


_num = [0]


def N(text, restart=False):
    _num[0] = 1 if restart else _num[0] + 1
    p = P(f"{_num[0]}.\t" + text)
    f = p.paragraph_format
    f.left_indent, f.first_line_indent = Cm(0.63), Cm(-0.63)
    f.tab_stops.add_tab_stop(Cm(0.63))
    f.space_after = Pt(2.5)
    return p


def H1(text):
    return doc.add_heading(text, level=1)


def H2(text):
    return doc.add_heading(text, level=2)


def box(text, fill=NOTE_FILL, color=INK, size=10):
    t = doc.add_table(rows=1, cols=1)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    c = t.cell(0, 0)
    shade(c, fill)
    c.paragraphs[0].paragraph_format.space_after = Pt(2)
    lines = text.split("\n")
    rich(c.paragraphs[0], lines[0], size, color)
    for line in lines[1:]:
        p = c.add_paragraph()
        p.paragraph_format.space_after = Pt(2)
        rich(p, line, size, color)
    _borders(t, "BFBDB7")
    set_widths(t, [16.6])
    sp = doc.add_paragraph()
    sp.paragraph_format.space_after = Pt(0)
    sp.paragraph_format.line_spacing = 0.6
    return t


def _borders(table, colour="9E9C96", size="4"):
    tbl = table._tbl
    pr = tbl.tblPr
    b = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement(f"w:{edge}")
        e.set(qn("w:val"), "single")
        e.set(qn("w:sz"), size)
        e.set(qn("w:color"), colour)
        b.append(e)
    pr.append(b)


def set_widths(t, widths):
    """Fixed layout with explicit grid widths (Word and LibreOffice both honour it)."""
    t.autofit = False
    pr = t._tbl.tblPr
    lay = OxmlElement("w:tblLayout")
    lay.set(qn("w:type"), "fixed")
    pr.append(lay)
    tw = OxmlElement("w:tblW")
    tw.set(qn("w:w"), str(int(sum(widths) * 567)))
    tw.set(qn("w:type"), "dxa")
    for old in pr.findall(qn("w:tblW")):
        pr.remove(old)
    pr.append(tw)
    for gc, w in zip(t._tbl.tblGrid.findall(qn("w:gridCol")), widths):
        gc.set(qn("w:w"), str(int(w * 567)))
    for row in t.rows:
        for j, w in enumerate(widths):
            row.cells[j].width = Cm(w)


def caption(text):
    p = doc.add_paragraph()
    rich(p, text, 9, MUTED)
    p.paragraph_format.space_after = Pt(8)
    p.paragraph_format.keep_together = True
    return p


def table(header, rows, widths=None, size=8.5, bold_rows=(), caption_text=None,
          first_col_bold=False):
    if caption_text:
        c = caption(caption_text)
        c.paragraph_format.keep_with_next = True
        c.paragraph_format.space_after = Pt(3)
    t = doc.add_table(rows=1 + len(rows), cols=len(header))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    _borders(t)
    for j, h in enumerate(header):
        cell = t.cell(0, j)
        shade(cell, HEAD_FILL)
        cell.paragraphs[0].paragraph_format.space_after = Pt(0)
        r = cell.paragraphs[0].add_run(h)
        r.bold = True
        r.font.size = Pt(size)
    for i, row in enumerate(rows, start=1):
        for j, v in enumerate(row):
            cell = t.cell(i, j)
            cell.paragraphs[0].paragraph_format.space_after = Pt(0)
            cell.paragraphs[0].paragraph_format.line_spacing = 1.0
            rich(cell.paragraphs[0], str(v), size)
            if (i - 1) in bold_rows or (first_col_bold and j == 0):
                for r in cell.paragraphs[0].runs:
                    r.bold = True
    # repeat header row, keep rows together
    trpr = t.rows[0]._tr.get_or_add_trPr()
    th = OxmlElement("w:tblHeader")
    th.set(qn("w:val"), "true")
    trpr.append(th)
    for row in t.rows:
        cant = OxmlElement("w:cantSplit")
        row._tr.get_or_add_trPr().append(cant)
    if widths:
        set_widths(t, widths)
    sp = doc.add_paragraph()
    sp.paragraph_format.space_after = Pt(2)
    return t


def figure(name, width_cm, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.keep_with_next = True
    p.paragraph_format.space_after = Pt(2)
    p.add_run().add_picture(str(FIGS / name), width=Cm(width_cm))
    caption(text)


# ---------------------------------------------------------------- header / footer
hp = sec.header.paragraphs[0]
hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
rich(hp, "NASIM-GBT detector — status report, 14 Nov 2026   |   ", 8.5, MUTED)
rich(hp, "**ALL RESULTS SYNTHETIC — not measured on the module**", 8.5, WARN)
fp = sec.footer.paragraphs[0]
fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
rich(fp, "Page ", 8.5, MUTED)
field(fp, "PAGE")
rich(fp, " of ", 8.5, MUTED)
field(fp, "NUMPAGES")

# ---------------------------------------------------------------- title block
P("AI-Driven Smart Cooling for PV Panels Based on Real-Time Thermal Spike and Sudden "
  "Degradation Detection", size=9.5, color=MUTED, after=2)
P("**NASIM-GBT: a real-time thermal-spike detector for the ESP32**", size=19,
  color=ACCENT, after=2)
P("Status report on the model, its evaluation and its on-board implementation",
  size=12, color=MUTED, after=8)
P("**To:** project supervisor, Dr. Abdulelah Al-Humaiani    **From:** Asim (lead) and the "
  "project team, Mechatronics, Umm Al-Qura University    **Date:** 14 November 2026",
  size=9.5, after=8)

box("**Read this first: every result in this report is SYNTHETIC.** It was computed on 150 "
    "simulated days produced by our own data generator and written in the exact file "
    "formats of the rig. No number here was measured on the 100 W module. The results show "
    "that the method, the evaluation and the ESP32 code work end to end; they say nothing "
    "about how often spikes occur in Makkah or how well the detector will do on real data. "
    "Values marked [EST] are estimates, not measurements.", fill=WARN_FILL, size=9.8)

H2("Summary")
B("NASIM-GBT is a small gradient-boosted tree model (50 trees, depth ≤ 4) that reads the "
  "module temperature, the in-plane irradiance and the air temperature every 10 s and "
  "raises an alarm when a thermal spike (a rise of ≥ 3.0 °C within 10 min) is starting.")
B("On the 30 synthetic test days (85 spikes) at the pre-registered budget of 0.2 false "
  "alarms per day it reached recall 1.00 with 0.07 false alarms per day and alarmed 4.1 min "
  "earlier than the temperature-threshold rule (95 % interval −5.2 to −2.9 min). **SYNTHETIC.**")
B("After the reviewer analyses we **no longer claim any lead over CUSUM**: tuned for speed, "
  "CUSUM is as early (−0.1 min, −1.0 to +0.8) but finds fewer spikes (recall 0.87). A "
  "one-line irradiance-step rule is as early as NASIM on cloud-return spikes, but finds "
  "none of the others.")
B("The C version of the detector gives exactly the same alarms as Python in all 150 days "
  "(0 mismatches, double precision) — on the PC build of the same C source; the re-run on "
  "the ESP32 board itself is still pending. The logger uses 35 % of flash and 9 % of RAM.")
B("We ask you to confirm the pre-registered criteria (Section 7) before the first field day.")

# ================================================================ 1
H1("1. What the detector does and why")
P("Your direction of 30 September was to control cooling from a sudden temperature jump of "
  "about 3–4 °C instead of from the slow daily heating. The first step, and the subject of "
  "the planned paper, is to **detect such a jump in real time on the ESP32**, while it is "
  "still developing. Cooling and the pump are deferred until after the paper; the "
  "detector's alarm is the signal they will later act on.")
H2("Thermal spike: the reference definition")
P("The module temperature is the mean of the five back-sheet sensors on a 10 s grid. After "
  "the day ends it is smoothed (centred 3-sample median, then 1-min mean). A **spike** "
  "exists when this smoothed temperature rises by **θ = 3.0 °C or more within a trailing "
  "window of W = 10 min**. Onset, the time the rise crosses 3 °C, peak and size are then "
  "fixed from the whole day. A rise of 2.0–3.0 °C is a **minor rise**: it is counted but "
  "neutral in the scoring.")
B("θ = 3.0 °C is the lower end of your “about 3–4 °C”.")
B("W = 10 min is about 1.6 thermal time constants (τ ≈ 6.3 min, Herteleer et al. 2023), "
  "and 3 °C in 10 min is three times the normal heating rate of ≈ 0.1 °C/min "
  "(Moharram et al. 2013).")
P("**Why a detector is still needed if the definition is a threshold.** The reference "
  "labelling sees the whole day, including the future; it is used only to score. A detector "
  "on the ESP32 sees only past samples. A simple causal threshold rule fires at the moment "
  "the rise **reaches** 3 °C, never earlier. The research question is therefore how early "
  "and how reliably a detector fires while the rise is still developing, and at what cost "
  "in false alarms.")
H2("The cloud-return case")
P("On 10 November you confirmed that the rise when the sun returns after a cloud **is** a "
  "spike. Before that our pipeline treated such rises as “hard negatives”, i.e. scored them "
  "as false alarms. Now every rise that meets the definition is a spike, whatever its cause, "
  "and the cause is recorded in a separate field:")
table(["Cause label", "Rule (f_G = share of the rise explained by the irradiance step)",
       "Typical physical meaning"],
      [["irradiance", "f_G ≥ 2/3", "sun returning after cloud, haze or dust; shade removed"],
       ["non_irradiance", "f_G ≤ 1/3", "wind lull, warm-air step, anything the pyranometer "
                                     "does not see"],
       ["mixed", "between 1/3 and 2/3", "both contribute"]],
      widths=[3.0, 6.6, 7.0],
      caption_text="Table 1. Cause label of each spike (docs/paper/03 §3). The limits 1/3 and "
                   "2/3 are assumptions fixed before any field data; f_G uses a first-order "
                   "thermal model with U_eff = 35 W/(m²·K) [EST] and τ = 6.3 min (literature).")
P("Without an anemometer, wind cannot be observed, so the honest labels are "
  "“irradiance-driven or not”. The cause rule is checked on controlled tests whose cause "
  "is known (shade mesh removed = irradiance; windbreak = non-irradiance).")
figure("fig1_example_day.png", 13.2,
       "Figure 1. One SYNTHETIC test day (the generator's calendar date 2027-04-04): module temperature with the "
       "labelled spikes, irradiance, and the alarm times of the detectors (pipeline figure "
       "fig1_example_day).")

# ================================================================ 2
H1("2. The model")
H2("Inputs and features")
P("NASIM-GBT uses the sensors already on the rig: the five back-sheet DS18B20 sensors "
  "(averaged, then a 3-sample median against single-sample glitches), the PYR20 in-plane "
  "irradiance, the air temperature, and the date and time from the RTC. Wind is **not** "
  "used (the anemometer is deferred). From these, ten features are computed on every 10 s "
  "sample, using only the current and earlier samples:")
table(["#", "Feature", "Meaning"],
      [["1–3", "rise_2m, rise_5m, rise_10m", "temperature rise over the last 2, 5 and 10 min (°C)"],
       ["4", "g_change_2m", "relative irradiance change over 2 min"],
       ["5", "g_range_10m", "irradiance (max − min) over 10 min, relative to now"],
       ["6", "kc", "clear-sky index in the module plane (measured / clear-sky irradiance)"],
       ["7", "cos_zenith", "sun position (time of day and season)"],
       ["8", "excess_per_g", "(T_module − T_air) / G × 1000, ≈ 1000 / U of the Faiman model"],
       ["9", "cusum_d", "residual of a weather-driven thermal model minus its slow baseline (°C)"],
       ["10", "cusum_S", "CUSUM statistic of that residual"]],
      widths=[1.2, 4.6, 10.8],
      caption_text="Table 2. The ten model inputs (detect/features.py; the same order is "
                   "written in the header of the generated C file).")
H2("Model type and size")
P("The model is a **gradient-boosted decision-tree classifier** (LightGBM 4.7.0): a sum of "
  "small decision trees, each a short chain of if-then comparisons on the features. Its "
  "output is a number p between 0 and 1, the model's confidence that a spike is starting. "
  "The model is “hybrid”: features 9–10 come from a classical CUSUM on a thermal-model "
  "residual, so the trees can combine the physical model with the raw rises.")
table(["Item", "Value"],
      [["Trees", "50 (size budget)"],
       ["Depth / leaves per tree", "depth ≤ 4, ≤ 15 leaves"],
       ["Learning rate; min. samples per leaf", "0.1; 50"],
       ["Class weighting", "positive samples weighted by √(n_negative / n_positive)"],
       ["Alarm rule", "p ≥ 0.98 on 2 consecutive samples (20 s), then 10 min silence"],
       ["Generated C source", "95 506 bytes of plain if-else code (m2cgen 0.10.0)"],
       ["Reproducibility", "fixed seed, deterministic mode, one thread; versions pinned"]],
      widths=[6.0, 10.6],
      caption_text="Table 3. Model settings (config.py, detect/gbt.py). The threshold 0.98 "
                   "was chosen on the validation days.")
H2("How it was trained and validated")
P("**Data.** 150 SYNTHETIC days (hard variant, seed 5, tilt 20°), generated and then written "
  "as logger files in UTC, with markers, log sheets, sweep files and a reference-logger "
  "export, so that the pipeline reads them exactly as it will read the SD card. The "
  "reference labelling found **359 valid spikes** (207 irradiance, 109 non_irradiance, "
  "43 mixed; median size 6.6 °C, IQR 4.7–9.8) and 373 minor rises. These rates follow from "
  "the generator's assumptions (1.5 clouds and 4–6 wind lulls per day); they are not a "
  "prediction for Makkah.")
P("**Target.** A sample is positive during the first 10 min of each spike; the rest of the "
  "spike and neutral periods are left out of training, so the model is never told that a "
  "real rise is “not a spike”.")
P("**Split by whole days, in time order** — never random samples, which would leak "
  "information between neighbouring samples:")
table(["Part", "Days", "Use"],
      [["Training", "90 (first 60 %)", "fit the trees; 193 spikes"],
       ["Validation", "30 (next 20 %)", "81 spikes; choose every setting of every detector (p threshold, "
                                         "rise limit, κ and h, irradiance step)"],
       ["Test", "30 (last 20 %)", "scored once, after all choices: 85 spikes, 39 of them not "
                                   "irradiance-driven; 340 valid daytime hours"]],
      widths=[2.8, 3.4, 10.4],
      caption_text="Table 4. Chronological split (docs/paper/03 §6, docs/paper/11 §3). "
                   "Confidence intervals: bootstrap over whole test days, 2000 resamples, "
                   "95 % percentile intervals; differences between detectors are paired on "
                   "the same resampled days.")
H2("How it runs on the ESP32")
P("The feature code was written once in Python in a form that translates line by line into "
  "C (fixed-size ring buffers, scalar arithmetic only). Its C twin (export/c/nasim.c) and "
  "the trees, exported automatically to C, are compiled into the logger firmware. On every "
  "10 s row the ESP32 updates the features, evaluates the 50 trees, applies the alarm rule "
  "and writes p and the alarm bit to the SD card. The logger runs in **double precision**; "
  "settings reach the firmware only through generated header files, so the board cannot "
  "drift from the analysis.")
figure("fig_pipeline.png", 13.5,
       "Figure 2. What NASIM-GBT does with each 10 s sample on the ESP32. The diagram shows "
       "the processing steps only; it contains no results.")

# ================================================================ 3
H1("3. The four detectors compared")
P("All four see the same data, are tuned on the same validation days, are scored on the "
  "same test days, and stay silent for 10 min after each alarm.")
P("**(a) Temperature-threshold rule.** Alarm when the filtered module temperature has risen "
  "by at least θ_a within the last 10 min. It has no irradiance gate, so it also fires on "
  "cloud-return rises, as the definition requires. θ_a was tuned to 2.7 °C on the validation "
  "days; the fixed 3.0 °C version is also reported. This is the rule that defines a spike, "
  "applied causally, so it can only fire once most of the rise has happened.")
P("**(b) CUSUM on the temperature increment.** A cumulative-sum change detector: "
  "S = max(0, S + (T_k − T_k−1) − κ); alarm when S ≥ h. κ is the warming per sample "
  "that is ignored as normal; h is how much excess warming must accumulate. F1-tuned "
  "setting: κ = 0.02 °C per sample, h = 2.0 °C; at the matched false-alarm budgets the "
  "fastest qualifying setting is κ = 0.05, h = 0.5. (The older CUSUM on the thermal-model "
  "residual would explain cloud-return rises away by design; it is now a NASIM feature.)")
P("**(c) Irradiance-step rule** (added after reviewer point R3). Alarm when the in-plane "
  "irradiance has risen by a fraction ≥ x within 2 min — the same quantity NASIM receives "
  "as g_change_2m. Tuned x: 50 % in 2 min. It never looks at temperature. Its purpose is a "
  "fair test: if a one-line rule is as early as NASIM on cloud-return spikes, NASIM is "
  "justified only by the other spikes. It is evaluated offline only, not on the board.")
P("**(d) NASIM-GBT.** The tree model of Section 2: alarm when p ≥ 0.98 on two consecutive "
  "samples. It can combine the early irradiance signal, the shape of the temperature rise "
  "over three windows and the thermal-model residual. Detectors (a), (b) and (d) run on the "
  "ESP32 and are logged as bits of the alarm column (1 NASIM, 2 threshold, 4 CUSUM).")

# ================================================================ 4
H1("4. Results on synthetic data")
box("**SYNTHETIC.** 30 test days, 85 spikes, 340 valid daytime hours. False alarms per day "
    "= per 12 valid daytime hours. Recall = share of spikes detected; precision = share of "
    "alarms that were real spikes; F1 = their harmonic mean; delay = alarm time − spike "
    "onset; lead = time by which the alarm precedes the 3 °C crossing.", size=9.3)
table(["Detector", "TP/FN/FP", "Precision", "Recall (95 % CI)", "F1 (95 % CI)",
       "False alarms /day (95 % CI)", "Delay, min median (IQR)", "Lead, min", "Rise left at alarm"],
      [["Threshold, fixed 3.0 °C", "84/1/1", "0.99", "0.99 (0.96–1.00)", "0.99 (0.97–1.00)",
        "0.04 (0.00–0.11)", "8.0 (5.5–9.8)", "−0.7", "38 %"],
       ["Threshold, tuned 2.7 °C", "85/0/2", "0.98", "1.00 (1.00–1.00)", "0.99 (0.97–1.00)",
        "0.07 (0.00–0.18)", "7.3 (5.0–8.7)", "−0.5", "43 %"],
       ["CUSUM", "83/2/4", "0.95", "0.98 (0.94–1.00)", "0.97 (0.94–0.99)", "0.14 (0.00–0.32)",
        "5.7 (2.7–9.8)", "+0.5", "54 %"],
       ["Irradiance step", "40/45/11", "0.78", "0.47 (0.38–0.54)", "0.59 (0.50–0.66)",
        "0.39 (0.18–0.64)", "0.5 (0.3–0.8)", "+2.8", "99 %"],
       ["NASIM-GBT", "81/4/0", "1.00", "0.95 (0.91–0.99)", "0.98 (0.95–0.99)", "0.00 (0.00–0.00)",
        "1.8 (0.5–5.7)", "+3.0", "74 %"]],
      widths=[2.6, 1.6, 1.4, 2.1, 2.1, 2.0, 2.0, 1.1, 1.3], size=7.6, bold_rows=(4,),
      caption_text="Table 5. Each detector at its F1-tuned setting, test days (docs/paper/11 "
                   "§3; pipeline table2_detection). Intervals: day bootstrap. For the delay the "
                   "interquartile range (IQR) is given; delays are compared through the "
                   "paired differences below.")
P("**Paired differences, NASIM-GBT minus baseline** (mean, 95 % interval, same resampled "
  "days, F1-tuned settings):")
B("vs tuned threshold: F1 −0.01 (−0.04 to +0.02); median delay **−5.2 min (−6.3 to −3.5)**.")
B("vs CUSUM: F1 +0.01 (−0.03 to +0.05); median delay −3.0 min (−4.8 to −1.8) — an effect "
  "of tuning CUSUM for F1, **not a lead** (see the matched comparison below).")
B("vs irradiance step: F1 +0.39 (+0.32 to +0.47); recall +0.48 (+0.41 to +0.57); false "
  "alarms −0.39 per day (−0.64 to −0.18); median delay on irradiance-driven spikes "
  "+0.1 min (0.0 to +0.3), i.e. NASIM is not earlier there.")
P("The F1 differences among the three temperature-based detectors are not detectable "
  "(every interval contains zero).")

H2("Comparison at equal false-alarm budgets")
P("A detector set to alarm readily is early by construction, so delays must be compared at "
  "the same false-alarm rate. For each budget, every detector gets, on the validation days, "
  "its fastest setting that stays within the budget and still finds 90 % of the validation "
  "spikes; that setting is then scored on the test days. The realised test rate is "
  "reported, because it need not equal the budget.")
table(["Budget /day", "Detector", "Setting", "Test FA /day", "Test recall", "Delay, min",
       "NASIM − this: delay, min (95 % CI)", "NASIM − this: recall (95 % CI)"],
      [["0.05 and 0.1", "Threshold", "2.7 °C", "0.07", "1.00", "7.3", "−5.2 (−6.3 to −3.5)", "−0.05 (−0.09 to −0.01)"],
       ["", "CUSUM", "κ 0.05, h 0.5", "0.07", "0.87", "2.0", "+0.3 (−0.8 to +1.8)", "+0.08 (+0.02 to +0.15)"],
       ["", "NASIM-GBT", "p 0.98", "0.00", "0.95", "1.8", "", ""],
       ["**0.2 (primary)**", "Threshold", "2.1 °C", "0.18", "1.00", "6.0", "−4.1 (−5.2 to −2.9)", "+0.00 (+0.00 to +0.00)"],
       ["", "CUSUM", "κ 0.05, h 0.5", "0.07", "0.87", "2.0", "−0.1 (−1.0 to +0.8)", "+0.13 (+0.07 to +0.19)"],
       ["", "NASIM-GBT", "p 0.95", "0.07", "1.00", "1.8", "", ""],
       ["0.5", "Threshold", "2.0 °C", "0.64", "1.00", "5.8", "−4.2 (−5.2 to −3.2)", "+0.00 (+0.00 to +0.00)"],
       ["", "CUSUM", "κ 0.05, h 0.5", "0.07", "0.87", "2.0", "−0.4 (−1.2 to +0.3)", "+0.13 (+0.07 to +0.19)"],
       ["", "NASIM-GBT", "p 0.90", "0.32", "1.00", "1.7", "", ""]],
      widths=[2.0, 1.9, 2.0, 1.4, 1.3, 1.2, 3.4, 3.4], size=7.6,
      caption_text="Table 6. Matched false-alarm comparison, test days (docs/paper/11 §3a, "
                   "pipeline table8_matched_false_alarms). The irradiance step has no row: "
                   "no setting finds 90 % of the spikes (its recall never exceeds 0.47).")
figure("fig6_delay_vs_false_alarms.png", 15.0,
       "Figure 3. SYNTHETIC test days. Left: the shortest median delay each detector reaches "
       "with at most x false alarms per day, among its settings that still find 90 % of the "
       "spikes (open markers: the F1-tuned settings of Table 5). Right: the highest recall "
       "each detector reaches with at most x false alarms per day, any setting. The "
       "irradiance step has no line on the left (no setting reaches recall 0.9).")
P("Reading Figure 3 and Table 6: NASIM alarms **4 to 5 min before the threshold rule at "
  "every budget**. Against CUSUM tuned for speed the delay difference is not detectable at "
  "any budget, but CUSUM pays in recall: 0.87 against 0.95–1.00 for NASIM. At the primary "
  "budget that recall difference is detectable: +0.13 (+0.07 to +0.19). On the test-day "
  "curve restricted to recall ≥ 0.9, CUSUM's best delay is 3.0 min at 0.1 false alarms per "
  "day and 1.8 min at 0.9 per day; NASIM reaches 1.8 min at zero.")

H2("Results by spike cause")
table(["Detector", "irradiance (n = 46)", "non_irradiance (n = 25)", "mixed (n = 14)"],
      [["Threshold, tuned", "1.00 / 0.98 / 5.4", "1.00 / 0.96 / 8.5", "1.00 / 0.93 / 8.6"],
       ["CUSUM", "1.00 / 0.96 / 2.9", "0.92 / 0.88 / 9.8", "1.00 / 0.88 / 10.2"],
       ["Irradiance step", "0.87 / 0.82 / 0.5", "0.00 / 0.00 / –", "0.00 / 0.00 / –"],
       ["NASIM-GBT", "1.00 / 1.00 / 0.7", "0.92 / 0.96 / 5.7", "0.86 / 0.92 / 6.0"]],
      widths=[3.6, 4.3, 4.3, 4.3], size=8.5, bold_rows=(3,),
      caption_text="Table 7. Per cause, test days, F1-tuned settings: recall / F1 / median "
                   "delay in min (docs/paper/11 §3). Precision per cause uses all false "
                   "alarms, because a false alarm has no cause.")
figure("fig_per_cause.png", 12.0,
       "Figure 4. The values of Table 7 as bars (SYNTHETIC). Left: recall; right: median "
       "delay. The irradiance step finds no non-irradiance or mixed spike, so it has no "
       "delay bar there.")
P("NASIM's 4 missed spikes are all non_irradiance or mixed: with no irradiance step to see, "
  "it is less sure. That is exactly where real data will test it.")

# ================================================================ 5
H1("5. What changed after the reviewer analyses")
P("On 12 November we ran three checks that a strict reviewer would ask for. Two of them "
  "changed the claim.")
P("**R2 — compare at equal false-alarm budgets (Table 6, Figure 3).** The first run showed "
  "NASIM 3.0 min earlier than CUSUM. That came from tuning CUSUM for F1, which picked a slow "
  "setting (h = 2.0 °C). Tuned for speed within the same budget, CUSUM alarms after 2.0 min "
  "and its difference from NASIM is not detectable. **Consequence: no lead over CUSUM is "
  "claimed**; the CUSUM delay difference is always reported as a number with its interval "
  "and never called a lead. The lead over the threshold rule (4–5 min) holds at every "
  "budget.")
P("**R3 — the irradiance-step baseline.** On irradiance-driven spikes a one-line rule is as "
  "early as NASIM (0.5 min against 0.7 min). **NASIM's speed on these spikes is its "
  "irradiance feature, nothing more.** The rule finds 87 % of the irradiance-driven spikes "
  "and none of the others (47 % overall) and gives 11 false alarms (0.39 per day) where "
  "NASIM gives none: a cloud edge that does not heat the module by 3 °C still trips it. So "
  "NASIM does not beat it on lead time; it beats it on coverage (35 of the 39 "
  "non-irradiance and mixed spikes) and on false alarms. On non-irradiance spikes NASIM "
  "(5.7 min) is ahead of the F1-tuned threshold (8.5 min; −2.8 min, −3.5 to −1.8), but the "
  "speed-tuned CUSUM reaches 4.2 min there.")
P("**R6 — sensitivity of the cause labels.** Moving the limits from 1/3–2/3 to 0.25–0.75, "
  "0.40–0.60 or 0.50–0.50 changes the irradiance count only from 200 to 213 of 359; no "
  "spike of known cause ever receives the opposite label. The limits stay at 1/3 and 2/3 "
  "until controlled tests on the module say otherwise.")
box("**The claim as it now stands (decided by Asim, 12 Nov 2026).** NASIM-GBT is the main "
    "detector. The claim is that one detector **combines** an early alarm, high recall and "
    "almost no false alarms. It is **not** claimed to be the earliest detector, and no lead "
    "over CUSUM is claimed. Whether this combination is worth a learned model over "
    "“fast CUSUM + irradiance step” is the question for the field data.", size=9.8)

# ================================================================ 6
H1("6. On-board facts")
H2("Agreement between the C code and Python")
P("All 150 synthetic days (648 000 rows) were replayed through the C detector file and "
  "compared row by row with the alarms of the Python pipeline:")
table(["Build", "Detector", "Alarms Python / C", "At the same row", "Only Python / only C",
       "Test days: alarms, differences"],
      [["double", "NASIM-GBT", "583 / 583", "583", "**0 / 0**", "128, 0"],
       ["double", "Threshold", "626 / 626", "626", "**0 / 0**", "137, 0"],
       ["double", "CUSUM", "657 / 657", "657", "**0 / 0**", "137, 0"],
       ["float", "NASIM-GBT", "583 / 584", "565", "18 / 19", "128, 7"],
       ["float", "Threshold", "626 / 626", "575", "51 / 51", "137, 20"],
       ["float", "CUSUM", "657 / 657", "641", "16 / 16", "137, 4"]],
      widths=[1.6, 2.4, 3.0, 2.4, 3.0, 4.2], size=8.5,
      caption_text="Table 8. Parity of the C code with Python, 150 SYNTHETIC days "
                   "(docs/paper/11 §6, parity.json). Double, NASIM: largest |Δp| "
                   "1.1 × 10⁻¹⁶, worst feature difference 7.8 × 10⁻¹⁶ (doc 11; the re-run of "
                   "14 Nov on Linux gave 1.11 × 10⁻¹⁶ and 0, all alarm counts identical). "
                   "The board runs double; the float rows are for information.")
box("**Important qualification.** The zero-mismatch result was obtained with the logger's "
    "C source in double precision **compiled for the PC** (x86-64, gcc -O2), not with the "
    "ESP32 binary. The threshold rule and CUSUM use only add, subtract, divide and compare, "
    "which IEEE 754 fixes bit for bit, so no PC–ESP32 difference is expected (an argument, "
    "not a run). NASIM also calls math library functions whose last digit may differ. An "
    "earlier on-board replay **with the earlier model** gave 0 alarm differences in 130 000 "
    "rows and a largest |Δp| of 2.2 × 10⁻¹⁶. The on-board re-run with the current model is "
    "**pending** (needs the board connected).", fill=WARN_FILL, size=9.3)
H2("Memory and loop time")
table(["Quantity", "Value", "Source"],
      [["Board", "Freenove ESP32-WROVER (ESP32-D0WD-V3, 240 MHz, 4 MB flash, 4 MB PSRAM)",
        "firmware/PINMAP.md"],
       ["Logger flash, three detectors", "462 038 B = 35 % of the 1 310 720 B app partition",
        "build, 13 Nov 2026"],
       ["Logger global RAM", "30 792 B = 9 % of 327 680 B", "build, 13 Nov 2026"],
       ["Of which: threshold + CUSUM", "1 084 B flash, 1 584 B RAM (state 1 032 B + 552 B)",
        "build, 13 Nov 2026"],
       ["NASIM state (double)", "2 288 B (float would be 1 176 B)", "docs/paper/07 §7"],
       ["NASIM update time", "0.89 ms mean, 1.06 ms worst; 33 µs while buffers fill",
        "measured on the board, earlier model, same tree settings"],
       ["Threshold + CUSUM time", "not measured; [EST] together < 0.1 ms", "estimate"],
       ["Total detector time per row", "[EST] about 1 ms per 10 s row (0.01 % of the period)",
        "estimate"]],
      widths=[4.4, 7.4, 4.8], size=8.5,
      caption_text="Table 9. Resources on the ESP32 (docs/paper/07 §7, docs/paper/11 §6). "
                   "The 10 s row is dominated by the 750 ms DS18B20 conversion, not by the "
                   "detectors.")
P("Float precision would cut NASIM's time to about a third, but it moves alarms (Table 8), "
  "so the logger uses double. Time per row for all three detectors with the current model "
  "is still to be measured on the board.")

# ================================================================ 7
H1("7. Pre-registered acceptance criteria")
P("The criteria were written on 12 November, **before any field data exists** (the "
  "synthetic results were known; field results were not). Asim approved them "
  "provisionally; **you are asked to confirm or change them before the first field day**. "
  "From then on they are frozen, and any change is listed in the paper as a deviation. The "
  "detector result is secondary in the paper; the primary contribution is the field "
  "characterisation of spikes.")
P("**How it is tested.** Test days only, at the primary budget of **0.2 false alarms per "
  "day**, each detector at the setting chosen on the validation days by the matched-budget "
  "procedure. Budgets 0.05, 0.1 and 0.5 are reported but do not change the verdict.")
table(["", "Condition on the test days", "Synthetic result", ""],
      [["Precondition", "≥ 20 valid test spikes, ≥ 8 of them not irradiance-driven; "
                        "≥ 50 training spikes", "85 test (39 not irradiance); 193 training",
        "met"],
       ["C1 false alarms", "NASIM ≤ 0.2 per day", "0.07 per day", "PASS"],
       ["C2 recall", "NASIM ≥ 0.90", "1.00", "PASS"],
       ["C3a lead", "median lead over the 3 °C crossing > 0", "+3.8 min", "PASS"],
       ["C3b rise remaining", "median share of the rise still to come ≥ 50 %", "92 %", "PASS"],
       ["C3c vs threshold", "NASIM − threshold, median delay: 95 % CI entirely below 0",
        "−4.1 min (−5.2 to −2.9)", "PASS"]],
      widths=[3.0, 6.6, 4.8, 1.6], size=8.5, first_col_bold=True,
      caption_text="Table 10. The criteria (docs/paper/03 §10, config.py PREREG_*) and the "
                   "verdict printed by the pipeline on the SYNTHETIC test days "
                   "(verdict.json).")
P("**The claim holds** if C1, C2 and C3 are all met. **It is distinctive** if, in addition, "
  "no baseline at its own setting for the same budget matches it — a baseline matches when "
  "it meets C1 and C2 and its median delay is not detectably longer than NASIM's. On the "
  "synthetic test days: the threshold rule meets C1 and C2 but is detectably later; CUSUM "
  "is as early (−0.1 min, −1.0 to +0.8) but its recall 0.87 fails C2 (NASIM minus CUSUM in "
  "recall +0.13, +0.07 to +0.19); the irradiance step has no qualifying setting. Synthetic "
  "outcome: **“holds and distinctive” — narrowly: CUSUM misses the recall condition by "
  "three spikes in 85.** That narrowness is why no claim is made over CUSUM, and the "
  "synthetic set must not be used to adjust these numbers.")
P("**The four fixed outcome sentences.** The paper will use exactly one of these, and no "
  "stronger sentence:")
table(["Outcome", "Sentence the paper uses"],
      [["Holds and distinctive", "“At 0.2 false alarms per day the tree model was the only "
                                 "one of the four detectors that met all three conditions "
                                 "set in advance.”"],
       ["Holds, not distinctive", "“The tree model met the three conditions set in advance, "
                                  "and so did [baseline]; for this module the simpler "
                                  "detector is sufficient.”"],
       ["Does not hold", "“The tree model did not meet the conditions set in advance "
                         "([which]); [the baseline that met most of them] is the better "
                         "choice here.”"],
       ["Precondition not met", "“The number of spikes was too small to test the claim.” "
                                "The threshold and CUSUM results are then the detection "
                                "result and NASIM is exploratory."]],
      widths=[3.6, 12.9], size=8.8, first_col_bold=True,
      caption_text="Table 11. Wording fixed in advance (docs/paper/03 §10).")
P("Always reported, whatever the outcome: for each baseline, NASIM minus baseline in median "
  "delay and in recall with 95 % intervals; the realised false-alarm rate next to the "
  "budget; the per-cause table. Not allowed: choosing the budget, the recall floor or the "
  "50 % after seeing field results; replacing the test days; dropping a baseline; reporting "
  "only the budget at which NASIM looks best.")

# ================================================================ 8
H1("8. Limits, and what we need from you")
H2("Limits of these results")
B("**Synthetic data only.** Every rate, count and score above comes from our generator. "
  "The model must be retrained on real data before it is used.")
B("The synthetic spikes are large (median 6.6 °C) and most follow a deep cloud, which an "
  "irradiance feature sees at once. Real spikes may be smaller and less clear-cut.")
B("Pure-cause synthetic spikes were labelled correctly in 147 of 172 (85 %); errors fell "
  "into “mixed”, never into the opposite class. Real spikes will be less clean, and without "
  "an anemometer the cause label cannot separate a wind lull from a warm-air step.")
B("C–Python parity is shown on the PC build of the same C source; the ESP32 run with the "
  "current model and the timing of all three detectors are pending.")
B("Detectors count samples as their time base: after k skipped rows (k ≤ 4) a “10-min” "
  "window spans 10 min + k·10 s, so a rate is understated by up to ≈ 7 % (accepted; the "
  "offline analysis uses the same rule).")
H2("What the real data must show")
N(restart=True, text="θ = 3 °C must sit above normal heating: on clear periods with no event, the 99.9th "
  "percentile of the 10-min rise must be below 3 °C. If the morning warm-up alone reaches "
  "it, the analysis window is restricted rather than θ quietly raised.")
N("Enough spikes: ≥ 50 for training and ≥ 20 on the test days, ≥ 8 of them not "
  "irradiance-driven. At the generator's rates (2.3 natural spikes per day, 1.0 not "
  "irradiance-driven — not a forecast) this needs 43 days for the expected counts, 53 days "
  "for an 80 % chance and 63 days with the lower 95 % rates. With a pilot of 5 days and 4 "
  "spikes (1 not irradiance-driven) it would be 200 and 258 days, and “not reachable” in "
  "the cautious case. The campaign length is recomputed after the first pilot week.")
N("NASIM, retrained on real data, must meet C1–C3 at 0.2 false alarms per day; the verdict "
  "and its sentence are then printed by the pipeline.")
N("The on-board replay must again give 0 alarm mismatches with the retrained model, and "
  "the time per row must be measured on the board.")
H2("What we need from you")
N(restart=True, text="**Confirm or change the pre-registered criteria** before the first field day: the "
  "primary budget of 0.2 false alarms per day, recall ≥ 0.90, ≥ 50 % of the rise remaining, "
  "and the precondition counts (Section 7).")
N("**Decide whether controlled spikes count** towards the 20 test spikes and the 8 "
  "non-irradiance ones (shade-mesh, windbreak, and possibly load-off tests); the criteria "
  "text does not settle this yet.")
N("**The load-off test proposal** (docs/paper/03 §11): opening the load for 10 min should "
  "warm the module without an irradiance step, but the estimated rise is 1.9–4.7 °C [EST] — "
  "around the threshold, not clearly above it. Should we try it once?")
N("**Whether pilot logging at open circuit may start before the load exists**, so that the "
  "spike rate, and therefore the campaign length, is known early.")
N("**Approval of the anemometer**, which would make the wind-lull cause observable, and "
  "the loan of the commercial data logger for validating the ESP32 measurements.")


P("Sources in the repository: docs/paper/03-spike-definition-and-metrics.md, "
  "docs/paper/11-pipeline-and-synthetic-results.md, docs/paper/07-load-firmware-spec.md §7, "
  "config.py, detect/, export/c/; out/paper_demo/seed5/verdict.json and parity.json "
  "(regenerated with python -m analysis.demo_synth and python -m analysis.export_model). "
  "Figures 1 and 3 are the pipeline's own figures; Figures 2 and 4 are drawn by "
  "docs/reports/src/.", size=8.5, color=MUTED)

doc.core_properties.author = "Asim and the project team"
doc.core_properties.title = "NASIM-GBT detector: status report (synthetic results)"
doc.save(OUT)
print("wrote", OUT)
