# Paper draft (IEEE conference template)

| File | Content |
|---|---|
| `main.tex` | the draft: Introduction, Related Work, System, Methodology and Uncertainty written in full; Results, Discussion and Conclusion as placeholders tied to the analysis outputs |
| `refs.bib` | 38 references, each checked through its DOI or publisher page (`docs/paper/08-literature-review.md`) |
| `check_draft.py` | static check for machines without LaTeX: citations, labels, environments, pulled-in files |
| `results/` | not in the repository; a copy of the analysis output made from REAL data |

## Build

No LaTeX is installed on the development PC, so **the draft has not been compiled**.
Upload `main.tex` and `refs.bib` to Overleaf (the `IEEEtran` class is available there), or:

```bash
pdflatex main && bibtex main && pdflatex main && pdflatex main
```

Expect small fixes on the first build. Wide items now span both columns: tables 1, 2,
4, 5 and 8 are written as `table*` (list `WIDE_TABLES` in `analysis/report.py`, pulled in
with `\resulttablewide`), and figures 1, 2 and 6 are placed as `figure*` at `\textwidth`
(`WIDE_FIGURES`, `\resultfigwide`). Table 3 was reshaped to one row per cause and
detector so that it fits one column. All tables are set in `\footnotesize`. None of this
has been compiled: check the widths on the first Overleaf build (table 8 has 9 columns
and is the one most likely to need a shorter header).

## Filling in the results

```bash
.venv/Scripts/python -m analysis.pipeline --data <SD copy> --out paper/results
.venv/Scripts/python paper/check_draft.py
```

The figures and tables then appear in place of the boxes. Every `\todo{...}` (red in the
PDF) is a sentence that needs a real number or a decision. Nothing from the synthetic run
goes into Sections VI–VII; the one place synthetic numbers appear is Section IV-F, which
says what they are.

## Framing (decision by Asim, 2026-11-12)

The detector comparison is about **detection lead time and false alarms**, not about a
higher F1: with a spike defined by a threshold, every reasonable detector finds nearly
all of them. This came from the synthetic run and is a hypothesis for the field data, not
a finding.

Check of that framing on the synthetic run (2026-11-12, `docs/paper/11` §3a–§3b): the
tree model's lead over the threshold rule holds at equal false-alarm budgets, its lead
over CUSUM does not, and an irradiance-step rule is as early on irradiance-driven
spikes.

**Decision (Asim, 2026-11-12, after that check).** NASIM-GBT stays the main detector,
with a narrower claim: one detector that combines an early alarm, high recall and almost
no false alarms, compared with every baseline at equal false-alarm budgets. No lead over
CUSUM is claimed anywhere. The field characterisation is the paper's primary
contribution; the detector test is secondary. What the field data must show, and the
four sentences the paper may use, are fixed in `docs/paper/03` §10 and in the draft's
"Acceptance criteria fixed in advance" subsection, written before any field data.
