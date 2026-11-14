# Twin references: what was checked (2026-11-14)

Four sources of the twin were first written from memory (model-equations.md, params.py,
spray.py). They were checked on 2026-11-14 against the publisher's landing page, the ADS
record or the text of the source where it could be opened. Entries: [refs.bib](refs.bib).
The other twin sources (Faiman, King, Herteleer, Moharram) are in `paper/refs.bib` and were
checked on 2026-11-11.

| Source | Status | What was checked | Not checked |
|---|---|---|---|
| De Soto, Klein, Beckman, Solar Energy 80(1) (2006) 78-88 | **correct as cited**; add the erratum | title, volume, issue, pages, DOI on the publisher page | - |
| Erratum, Solar Energy 81(1) (2007) 150 | **new** | the erratum's text: Eq. (6a), dP/dV = 0 at the maximum power point, had a sign error | - |
| McAdams, Heat Transmission, 3rd ed., McGraw-Hill (1954) | **publication data correct; use of the correlation needs a caveat** (below) | edition, publisher and year as listed in Duffie & Beckman's references; the correlation 5.7 + 3.8 V and its revision in Palyvos (2008) | the book itself (not seen); the page; the section and equation number in Duffie & Beckman |
| "Incropera et al., Fundamentals of Heat and Mass Transfer" | **author order corrected**: the 7th edition is Bergman, Lavine, Incropera, DeWitt, Wiley (2011) | edition, authors, year, ISBN; section 6.7.1 "The Heat and Mass Transfer Analogy" (p. 410) and 6.7.2 "Evaporative Cooling" (p. 413) from the table of contents | the equation numbers; the exponent n = 1/3 in the text; the property tables behind Le = 0.87 |
| Alduchov, Eskridge, J. Appl. Meteorol. 35(4) (1996) 601-609 | **correct as cited** | volume, issue, pages, DOI (ADS); the coefficients in the paper's text | - |

## What the checks change

1. **De Soto erratum: no change to the code.** The erratum corrects the sign in the
   condition dP/dV = I + V dI/dV = 0 at the maximum power point. `twin/fit.py`
   (`electrical_from_listing`) uses the correct form, I_mp - V_mp g / (1 + g R_s) = 0 with
   g = dI/dV of the diode and shunt branch. Cite the erratum next to the paper.

2. **McAdams: the twin's evaporation is an upper bound for a second reason.** The
   correlation h = 5.7 + 3.8 V (W/(m2 K), V in m/s) comes from wind-tunnel measurements on
   a plate (Palyvos 2008 lists it for parallel flow, V < 5 m/s), and it **includes the
   effects of radiation and free convection**, not forced convection alone. Watmuff,
   Charters and Proctor (1977) revised the same data to exclude them: h = 2.8 + 3.0 V
   (as tabulated by Palyvos 2008; Watmuff's report itself was not seen). The twin feeds
   5.7 + 3.8 V into the Chilton-Colburn analogy as if it were a pure convection
   coefficient, so the mass-transfer coefficient, and with it the evaporation, is about
   1.5 times the Watmuff value (11.4 against 7.3 W/(m2 K) at 1.5 m/s). The placeholder is
   **kept** so the results of 2026-11-13 stay reproducible; `python -m twin.rank` runs the
   Watmuff form as its own row, and the evaporation must be calibrated on the module
   anyway (placeholders.md). Wording for the thesis: "the McAdams coefficient as quoted
   by Duffie and Beckman (2013), which includes radiation and free convection [Palyvos
   2008]; an upper bound for the evaporation".

3. **Incropera: cite as Bergman et al. (2011).** Same book; the 7th edition's first author
   is Bergman. Before citing an equation number, open the book: the analogy used is
   h / h_m = rho c_p Le^(1-n) with n = 1/3 (Chilton-Colburn), and Le = alpha_air /
   D_water-air ~ 0.87 near 300 K, from the appendix tables of air and of binary diffusion.

4. **Alduchov and Eskridge: no change.** Their recommended AERK form, Eq. (21),
   e_w(t) = 6.1094 exp(17.625 t / (243.04 + t)) hPa for -40 to 50 degC over water, is what
   `twin/spray.py` uses (610.94 Pa). Their Eq. (22), the enhancement factor for moist
   air, is left out (about +0.5 % at sea level); this is stated in assumption C3.

## Rules

- Cite only entries of [refs.bib](refs.bib) or `paper/refs.bib`. A `note` in refs.bib
  names what still has to be opened before a page or equation number is quoted.
- McAdams and Watmuff were not seen: cite them through Duffie & Beckman or Palyvos, as
  above, never as if read.
