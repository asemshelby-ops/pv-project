# Literature Research & Resulting Design Decisions

**Companion document to `grad-project-overview.md`**
**Compiled:** August 2026
**Scope:** Findings from a structured literature search on PV cooling, predictive control, and net-energy optimization — and the design decisions that follow from them.

> **How to read this document.** Sections marked **[LIT]** report what published literature states. Sections marked **[REC]** are engineering recommendations derived from that literature — they are input for supervisor review, not decisions already taken. No specification, datasheet value, or numerical result in this document is invented; every figure is attributed to a named source in the Reference List at the end.

---

## 1. Prior Art — Novelty Position Must Be Narrowed

### 1.1 What already exists [LIT]

A targeted search for predictive / model-based / ML-driven control of PV cooling returned directly overlapping published work. The project's novelty claim must account for these:

| Work | Venue | What it does |
|---|---|---|
| *A novel two-step optimization approach for film water cooling of a PV module in real ambient conditions* | Applied Thermal Engineering (2024) | **Closest prior art.** Two-step optimization: (1) optimal water flow rate computed iteratively as a nonlinear function of ambient conditions, (2) optimal activation time interval. Explicit objective is **maximizing net energy generation**. |
| *Model Predictive Control for Active Cooling of Photovoltaic Panels* | IFAC-PapersOnLine (2025) | MPC for active PV cooling exploiting forecasts. Reported ~20 °C temperature reduction and ~8 % increase in daily energy produced. |
| *Adaptive Model Predictive Control of Module Temperature in Photovoltaic Systems* | Ind. Eng. Chem. Res. (ACS) | Adaptive MPC applied to PV module temperature regulation. |
| *Comprehensive performance analysis of a novel closed-loop hydronic cooling of PV panel with a controlled intermittent flow strategy* | Renewable Energy (2024) | Controlled Intermittent Flow (CIF) in a closed loop, evaluated on **net** power output. |
| *Deep regression analysis for enhanced thermal control in photovoltaic energy systems* | Scientific Reports (2024) | Deep regression models for PV thermal control. |
| *A dynamic multi-objective optimization procedure for water cooling of a photovoltaic module* | (2021) | Multi-objective optimization of PV water cooling. |

**Conclusion:** "AI/predictive control for PV cooling" and "net-energy objective" are **both already claimed** in the literature. A first-ever claim would not survive examination.

### 1.2 The gap that does remain [LIT]

What the search did **not** find: a **physical outdoor experimental testbed** that compares all four control strategies (no cooling / continuous / threshold / predictive) head-to-head on **directly measured** net energy, with real voltage and current instrumentation.

The MPC works above are predominantly simulation or numerical; the two-step optimization is analytical-numerical. Experimental validation is the open slot.

Additionally, a 2026 review of next-generation PV cooling strategies explicitly lists **"AI-based predictive cooling control"** among open research gaps, and introduces **"adaptive"** as a fourth tier of PV cooling alongside passive, active, and integrated. This is a citable, current statement of the gap.

### 1.3 Recommended novelty wording [REC]

> *"While predictive control of PV cooling has been addressed analytically and numerically [refs], the literature lacks outdoor experimental validation comparing control strategies on the basis of directly measured net energy. This work provides that comparison."*

Novelty claimed in **experimental validation**, not in the idea. This is defensible even if further similar work surfaces.

This refines — it does not replace — the novelty positioning already in `grad-project-overview.md` §"Project Novelty", which correctly avoids overclaiming. The addition is that the claim can now be made *specific and cited* rather than general.

---

## 2. The Critical Finding: Expected Effect Size Is 1–5 %, Not 15–30 %

### 2.1 Net-energy results in the literature [LIT]

Headline efficiency gains reported in PV-cooling reviews (commonly 15–30 %) are **gross** figures that ignore pump consumption. When parasitic power is subtracted:

| System | Net result after pump/fan power |
|---|---|
| Closed-loop hydronic, hot arid region (Applied Energy 2024) | **+4.55 %** net power (with 11.3 °C max temperature reduction) |
| Controlled Intermittent Flow vs continuous (Renewable Energy 2024) | **+0.96 %** annual net power output |
| Saxena et al. (Renewable Energy 2018), open-rack / insulated modules | **+6 % and +8 %** net |
| PWM-controlled cooling pump | **+9.94 %** net energy gain |
| Air cooling, 2.71 W fan | **Net negative** — energy cost exceeded thermal gain |
| Hybrid system, ~150 W auxiliaries vs ~110 W PV output | **Strongly net negative** |

### 2.2 Why this reshapes the whole project

The differences this project must resolve — between threshold control and predictive control — plausibly sit in the **1–5 %** band. Possibly under 1 %, given the CIF result above.

This is a **measurement-precision problem before it is a control problem.** If the true difference between two strategies is ~1 % and the instrument chain carries ~3 % uncertainty, the experiment measures nothing regardless of how good the ML model is.

Everything in Sections 3–5 follows from this single fact.

### 2.3 Consequences [REC]

- The **uncertainty budget must be computed before data collection begins**, not reported afterward. If propagated uncertainty exceeds the expected effect size, the instrumentation must change before any experiment is run.
- Direct V and I measurement (INA226) is not an enhancement — it is a **precondition**. Note that at least one recent Elsevier paper on PV cooling (Alrashidi et al. 2025, hydrogel) reports "efficiency enhancement" values that were **calculated from temperature via Evans' equation**, never measured electrically. Direct measurement is a genuine methodological differentiator for this project.
- Expected-results framing in the report must be calibrated to single-digit net gains. Promising 20 % improvement sets up a visible failure.

---

## 3. Three Confounding Variables That Can Invalidate the Experiment

### 3.1 Soiling — the largest threat [LIT]

Documented Saudi-specific soiling data:

- Dhahran: a module left uncleaned for **6 months lost more than 50 % of power output**
- A **single sandstorm** reduced module power by **20 %**
- Dust surface density in Dhahran reached **6.2 g/m² over 10 months**
- PV systems without a cleaning schedule in Saudi Arabia and India degraded **more than 30 % after 3–6 months**

**The problem:** target effect is 1–5 %. Soiling drift is 10–50 %. Soiling is an order of magnitude larger than the phenomenon being measured.

**The specific trap:** with **front-surface** cooling, the cooling water *washes the cooled panel* while the reference panel continues to soil. The experiment then measures **cleaning, not cooling** — and produces impressively large, entirely invalid results. (Independent support: a Saudi PV carport testbed using optimized water jets raised array output **over 27 %** through soiling removal alone.)

**Recommendation [REC]:**

1. **Prefer back-surface cooling for the primary experimental campaign.** It cleanly isolates the thermal effect from the cleaning effect. This is the single highest-value experimental design decision available.
2. If front-surface cooling is required by the supervisor: apply an identical, scheduled cleaning protocol to **both** panels, log every cleaning event, and exclude post-sandstorm days from analysis.
3. Either way, a written cleaning protocol is a Phase 0 deliverable.

### 3.2 Panel-to-panel mismatch [REC]

Two nominally identical 30 W modules are not identical; manufacturing tolerance is typically on the order of ±3 % — comparable to or larger than the target effect.

**Required Phase 0 procedure:**

1. Run both panels side by side with **no cooling** for 3–5 days under matched irradiance.
2. Derive a correction factor between them.
3. Apply that correction to all subsequent comparative analysis.
4. **Swap panel positions between runs** to cancel position/shading asymmetry.

Cost: time only. This step is routinely omitted in student work and routinely invalidates it.

*(Note: this presumes supervisor approval of the second reference panel, still pending. Without a second panel, simultaneous baseline comparison is impossible and the experiment reduces to sequential day-to-day comparison, which cannot control for weather variation. The mismatch argument strengthens the case for approving the second panel.)*

### 3.3 Wind speed [LIT]

Three independent lines of evidence now converge:

1. Alrashidi et al. (2025): cooling benefit fell from **7 % at 0 m/s to 2.8 % at 3 m/s** — wind removed roughly 60 % of the cooling advantage.
2. Standard forced-convection correlations used in PV thermal models are explicit functions of wind speed (e.g. `h_c,forced = 3.3w + 6.5` windward, `2.2w + 8.3` leeward).
3. **IEC 61724-1:2021** lists wind speed and direction sensors among the instruments for PV plant performance monitoring.

**Recommendation [REC]:** wind speed logging moves from "possibly" (as listed in `grad-project-overview.md` §Measurements) to **required in Phase 0**. Data not logged now cannot be recovered later, and Phase 3 model validation will need it.

---

## 4. Proposed Theoretical Framing: The Cold-Water Budget

**[REC] — this is a proposed framing, not an established result.**

In a **closed-loop** system, water is recirculated. Over the day the reservoir progressively warms and its cooling capacity decays. The system therefore holds a **finite, depleting cold reserve** that must be allocated across the day.

This reframes the control question:

- **From:** "should the pump run right now?" (a reactive question a threshold controller can answer)
- **To:** "when during the day should the limited cooling capacity be spent?" (a resource-allocation problem over a horizon)

This is precisely the class of problem where predictive control has a structural advantage over reactive control: an ON-OFF controller **cannot** reserve cooling capacity for the afternoon peak, because it has no model of the future. A predictive controller can.

**Implementation requirement:** one additional DS18B20 in the reservoir. Reservoir temperature becomes a **state variable** in the controller and a feature in the ML model.

**Recommendation:** adopt this as the theoretical justification for predictive control in the thesis introduction. It gives a *physical* reason why prediction should outperform reaction, replacing the weaker generic claim that "AI is better."

**Supporting context [LIT]:** closed-loop recirculating designs are independently recommended for water-scarce regions, and a closed-loop hydronic system designed for hot arid regions reported 11.3 °C temperature reduction and +4.55 % net power. Closed loop is also the correct choice for Saudi water constraints and avoids scaling from hard water on the panel surface.

---

## 5. Proposed BOM Additions (pending supervisor approval)

| # | Item | Justification | Priority |
|---|---|---|---|
| 1 | **Anemometer** (wind speed) | First-order variable (§3.3); required by IEC 61724-1; three independent sources | **Critical** |
| 2 | **Additional DS18B20** (reservoir) | Enables cold-water-budget framing (§4); state variable for controller | **High** |
| 3 | **MOSFET driver module** (replacing plain relay) | Enables **PWM** pump speed control | **High** |
| 4 | Retain INA226 + MPPT controller | Direct V/I measurement is the methodological differentiator (§2.3) | Confirmed |

**On item 3 — PWM pump control [LIT + REC]:** PWM control of a cooling pump is reported to deliver **+9.94 % net energy gain**, among the strongest net figures found. Methodologically it matters more than the cost suggests: PWM gives the optimizer a **continuous action space** rather than a binary on/off decision, which substantially enriches the optimization problem. This aligns with the variable-flow control already specified in `grad-project-overview.md` §"Control Strategies to Compare" (strategy 4 selects among flow levels) — a MOSFET driver is what makes that specification physically achievable. Low cost, high return.

---

## 6. Machine Learning Guidance for Phase 3

### 6.1 Model selection [LIT]

Evidence on PV module temperature prediction:

- **XGBoost achieved Test R² = 0.9967**, followed by Random Forest and ANN; Linear Regression underperformed due to inability to capture nonlinear interactions.
- **LSTM performed notably worse** in comparable tasks (test MAE 3.230 °C, R² 0.526 for CNN-LSTM on air temperature).
- Gradient-boosted trees consistently outperform deep learning on tabular PV/meteorological data.

**Recommendation [REC]:** the progression already planned in `grad-project-overview.md` (Linear → Polynomial → Random Forest → XGBoost) is **correct and evidence-supported**. Do not add LSTM or deep learning. Deep architectures would add complexity without accuracy on this data type.

### 6.2 Feature set [LIT]

Published AI models for PV operating temperature commonly use: ambient temperature, irradiance, humidity, and **wind speed**. This independently confirms §3.3.

A 2026 study specifically addresses ML-based PV module temperature prediction **under desert conditions including soiling impact** — directly relevant to both the Saudi context and the soiling problem in §3.1. Recommended as priority reading before Phase 3.

### 6.3 Required Phase 1 measurement not yet in the plan [REC]

**Measure the pump power curve empirically:** `P_pump` as a function of (flow rate, PWM duty).

Do **not** rely on the theoretical relation `P_pump = Q·H_total·g·ρ / η_pump` or on the pump's datasheet. Small DC pump efficiency is poor and highly variable — commonly in the 20–40 % range — and using an assumed efficiency propagates directly into the net-energy result that the entire thesis rests on.

**This measured curve is the cost function of the optimizer.** Without it, Phase 3 optimizes against an assumed number.

---

## 7. Recommended Reframing of the Research Question

**[REC]**

**Avoid:** "Does AI-based predictive cooling outperform conventional control?"
Risk: the answer may be "no" or "not significantly" (cf. the +0.96 % CIF result), which makes a sound project look like a failure.

**Prefer:** "Under what operating conditions is active cooling energetically justified, and which control strategy is optimal in each regime?"

**Deliverable becomes an operating envelope / decision map:** net energy gain plotted against ambient temperature, irradiance, and wind speed, identifying where cooling pays and where it does not.

Advantages:

1. **Guaranteed result.** Even if predictive control ties with well-tuned threshold control, the envelope itself is an engineering contribution.
2. "Predictive control did not outperform threshold control under these conditions" becomes a **publishable finding**, not a failure.
3. Genuinely useful to industry, and directly responsive to the open gap identified in the 2026 review.

This is a reframing of presentation and analysis, not a change of scope — the four-strategy comparison and net-energy objective in `grad-project-overview.md` remain intact.

---

## 8. Assessment of Two Source Papers Reviewed

### 8.1 Hamed, Sharkawy, Hamdan & Maghrabie (2024), IJRCS — *Automated Water Cooling and Solar Tracking: A Systematic Review*

**Use as an index to primary sources; do not cite for numerical values.**

Useful content:
- **Section 3.7 (pp. 1830–1832)** contains a complete steady-state thermal model: energy balance, free/forced convection, radiation view factors, water-film heat transfer, and **pump power (Eq. 25) with Hazen-Williams head loss (Eq. 27)**. This is the physical basis of the project's net-energy objective function and is worth extracting.
- **Table 2** documents ~20 control studies, overwhelmingly ON-OFF; only two use PID, one fuzzy. Useful for establishing that reactive control dominates the field.
- Concrete published setpoints usable as threshold-controller starting values: 35 °C (Winardi et al., DS18B20), 40 °C on / 38 °C off hysteresis (Kusuma et al.), 50 °C (Azmi et al.).

Documented defects (verify before any use):
- **Eq. (6) states η_ref = 0.015.** This is implausible for a silicon module (typical 12–20 %) and is most likely a decimal error for 0.15. Do not reuse; verify against the cited source.
- Abstract conflates **overall (thermal + electrical)** efficiency of 51.4 % with electrical efficiency.
- Body text and summary tables disagree (e.g. Hasan et al. reported as 11.7→14 % in text vs "2.3 points / 19.69 % / 20 W" in Table 1).
- Reference numbering errors (Duan cited as [59] in text and [66] in Table 1; [94] and [95] duplicated across different entries in Table 2).
- Journal is low-tier. Cite primary sources, never the review, for any figure.

### 8.2 Alrashidi, Abdo, Abdelrahman, Altohamy & Elsemary (2025), *Case Studies in Thermal Engineering* 75:107125 — hydrogel passive cooling

**Methodologically important as a contrast case.**

- **Electrical efficiency was never measured.** Instrumentation (Table 1) comprises thermocouples, a solar power meter, a data logger, and an environmental meter — **no voltage or current measurement**. All reported "efficiency enhancement" values are derived from temperature via Evans' equation (Eq. 3) using an *assumed* η_stc = 17.1 % and β = 0.0045 °C⁻¹. This is the strongest available justification for this project's direct-measurement approach.
- **K-type thermocouple accuracy is ±5 °C** while the measured effect is 5.4–14.3 °C — instrument uncertainty is of the same order as the phenomenon. This supports the DS18B20 (±0.5 °C) selection over the originally specified K-type.
- **Internal inconsistencies:** peak enhancement at 50 °C reported as 8.84 % (abstract), 8.5 % (Table 2), and 8.9 % (conclusion). Wind-speed values in the abstract (6.06 / 2.23 / 2.71 %) do not match Table 4 (7 / 3.4 / 2.8 %); one paragraph on p.15 contradicts itself. **Use table values, not body text.**
- Indoor halogen-lamp testing drove the uncooled panel to 92.8 °C against an NOCT-based expectation near 75 °C (acknowledged by the authors), so temperature reductions are inflated relative to outdoor conditions.
- Environmental analysis assumes **PR = 1** and a coal baseline for a Saudi grid that is ~99 % gas/oil — upper-bound figures, acknowledged.
- Data availability declared "confidential" in a CC-BY open-access paper.

**Genuinely useful:** the wind-speed result (§3.3), the ambient-temperature trend (benefit rises 5 % → 8.5 % from 30 °C → 50 °C, favourable for Makkah conditions), the side-by-side reference panel design, ΔT-above-ambient as a normalized metric (Eq. 2), and the uncertainty propagation method (Eq. 6).

**Strategic note:** hydrogel is a **passive** competitor with zero parasitic power — 5–8.5 % gain with no pump. It should be acknowledged in the literature review and explicitly excluded from scope with justification. It should **not** be added as a fifth experimental arm; that is scope creep and violates the project's phase-sequencing discipline.

---

## 9. Action Items

**Before finalizing purchases:**

1. Present anemometer + reservoir DS18B20 + MOSFET/PWM driver to supervisor (§5)
2. Resolve back-surface vs front-surface cooling, presenting the soiling confound explicitly as the technical argument (§3.1)
3. Reinforce the case for the second reference panel using the mismatch argument (§3.2)

**Add to Phase 0:**

4. Panel-matching characterization run, 3–5 days, no cooling (§3.2)
5. Written cleaning protocol (§3.1)
6. Uncertainty budget computed **before** data collection (§2.3)

**Add to Phase 1:**

7. Empirical `P_pump` vs (flow, PWM duty) curve (§6.3)

**For the report:**

8. Adopt the narrowed novelty wording (§1.3)
9. Adopt the cold-water-budget framing as theoretical justification (§4)
10. Reframe the research question toward an operating envelope (§7)

---

## Reference List

**Prior art — predictive / optimization control**
- *A novel two-step optimization approach for film water cooling of a photovoltaic module in real ambient conditions*, Applied Thermal Engineering (2024). https://www.sciencedirect.com/science/article/abs/pii/S1359431123019270
- *Model Predictive Control for Active Cooling of Photovoltaic Panels*, IFAC-PapersOnLine (2025). https://www.sciencedirect.com/science/article/pii/S2405896325000990
- *Adaptive Model Predictive Control of Module Temperature in Photovoltaic Systems*, Ind. Eng. Chem. Res. https://pubs.acs.org/doi/10.1021/acs.iecr.0c03787
- *Deep regression analysis for enhanced thermal control in photovoltaic energy systems*, Scientific Reports (2024). https://www.nature.com/articles/s41598-024-81101-x

**Net energy / parasitic power**
- *Comprehensive performance analysis of a novel closed-loop hydronic cooling of photovoltaic panel with a controlled intermittent flow strategy*, Renewable Energy (2024). https://www.sciencedirect.com/science/article/pii/S0960148124022535
- *A hydronic closed-loop photovoltaic cooling system designed for hot and arid regions*, Applied Energy (2024). https://www.sciencedirect.com/science/article/pii/S0306261924013825
- *Laboratory based Experimental Investigation of Photovoltaic (PV) Thermo-control with Water*, Renewable Energy (2018). https://www.sciencedirect.com/science/article/abs/pii/S0960148117307838
- *An efficient pulsed-spray water cooling system for photovoltaic panels*, Renewable Energy (2021). https://www.sciencedirect.com/science/article/abs/pii/S0960148120314294

**State of the art**
- *Next-generation cooling strategies for photovoltaics: A review of pathways toward high efficiency, low carbon, and climate resilience*. https://www.sciencedirect.com/science/article/pii/S2590123025046006

**Machine learning**
- *Photovoltaic module temperature prediction using various machine learning algorithms: Performance evaluation*, Applied Energy. https://www.sciencedirect.com/science/article/abs/pii/S0306261924004471
- *Machine learning-based approach for predicting PV module temperature: multi-technological assessment including soiling impact under desert conditions*, Case Studies in Thermal Engineering (2026). https://www.sciencedirect.com/science/article/pii/S2214157X2600002X

**Soiling — Saudi / GCC**
- *The Impact of Soiling on PV Module Performance in Saudi Arabia*, Energies. https://doi.org/10.3390/en15218033
- *Soiling of photovoltaic panels in the Gulf Cooperation Council countries and mitigation strategies*. https://www.sciencedirect.com/science/article/abs/pii/S0927024821003457

**Standards**
- IEC 61724-1:2021, *Photovoltaic system performance – Part 1: Monitoring*. https://webstore.iec.ch/en/publication/65561

**Source papers assessed**
- Hamed, Sharkawy, Hamdan & Maghrabie, *Automated Water Cooling and Solar Tracking for Efficiency Improvement of PV Systems: A Systematic Review*, IJRCS 4(4) 2024, pp. 1819–1861.
- Alrashidi, Abdo, Abdelrahman, Altohamy & Elsemary, *Investigating the effectiveness of hydrogels for PV cooling across different operational conditions*, Case Studies in Thermal Engineering 75 (2025) 107125.

**Additional context**
- *Experimental evaluation of a hybrid evaporative and groundwater cooling system for enhancing photovoltaic efficiency in arid climates*, Scientific Reports (2025). https://www.nature.com/articles/s41598-025-22704-w
