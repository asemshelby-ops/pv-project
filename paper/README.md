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

Expect small fixes on the first build. Known ones: Tables 2, 3 and 8
(`table2_detection.tex`, `table3_per_cause.tex`, `table8_matched_false_alarms.tex`) are
wide; in the two-column layout they will need `table*` or fewer columns.
`fig6_delay_vs_false_alarms` has two panels and is drawn for the full text width
(`figure*`, `\textwidth`); at column width its labels are small.

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
spikes. The draft therefore no longer says the learned model is "earlier"; Section IV-F
and the Results TODOs ask what it adds in recall and false alarms. Whether it stays the
main detector is open until field data exist.
