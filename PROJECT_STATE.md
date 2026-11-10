# Graduation Project — Master State File

**Last updated:** 2026-11-10
**Governs:** all *status* questions (what is decided / pending / open). When this file conflicts with any other project file, chat, memory note, the team explainer page or the supervisor deck on status, **this file wins**.
**Does not replace:** `literature-research-and-design-decisions.md` (its literature findings remain valid; §10 lists which of its status items and framings are overridden). The **Excel BOM** remains authoritative for part numbers, quantities, and prices.

**Tags used below**
- `[DECIDED]` — decision taken by the team or supervisor
- `[SUPERVISOR]` — current supervisor direction
- `[PENDING]` — awaiting a decision from a named party
- `[OPEN]` — technical question not yet worked
- `[REC]` — Claude recommendation, input for review — **not a decision**
- `[LIT]` — published literature (sources in the literature doc)
- `[EST]` — first-pass engineering estimate on assumed inputs; replace once real values are known

**Changes on 2026-09-30 (supervisor voice note + new title):**
- New project title sent by the supervisor (§1).
- The research question moves from *"when does cooling pay (net-energy optimization)"* to the supervisor's *"can we control cooling using AI, based on temperature degradation"* (§1.1–1.3).
- One panel is confirmed for the control goal; a two-panel set-up belongs only to a performance-improvement framing (§2).
- The physical cooling loop becomes conditional on cost; a model-based cooling study is acceptable to the supervisor (§3, Gate B).
- Net power `P_PV − P_pump` is no longer the research question itself; it stays as the controller's decision rule and a key metric (§1.4).
- Phase plan rewritten around spike characterization → AI detection on the ESP32 → physical or modeled control (§3).
- A working interpretation of the note awaits one-message confirmation from the supervisor (§1.2).
- Later on 2026-09-30 (clear-sky chat): §7 and the Phase 0 timestamp criterion refined for the in-plane pyranometer — clear-sky reference transposed to the panel plane, orientation-aware timestamp check `[REC]`.
- Later on 2026-09-30 (targeted literature search + parts review): first-pass prior-art result (§1.6); spike physics and measurement limits (§5.1); parts notes — PYR20 is a silicon-range sensor, DS18B20 counterfeit screening, BME280 radiation shield, anemometer spec, per-degree pump break-even (§2); controlled spike-test method and event target (§3); stronger baselines and exporter notes (§6); a new Gate A question on irradiance-driven rises (§9). No part list overhaul: targeted changes only `[REC]`.
- Later on 2026-09-30: a fixed absolute-temperature threshold, as used in the literature, added to the control comparison as strategy (4) — `Decision:` by Asim (§3).

**Changes on 2026-10-01:** PV measurement load option C (PWM-switched resistors) adopted by the team — `Decision:` by Asim (§4). Gate A item 2 becomes informing the supervisor rather than asking for approval (§9).

**Changes on 2026-10-11 (supervisor meeting; source: a teammate's voice-note summary, secondhand and partly unclear):**
- The borrowed 100 W module is not available → **buy a module** `[SUPERVISOR]`; rating to confirm (§2).
- Cooling is **physical**, by **front-surface water spray** with garden-type nozzles, chosen over a back-surface "radiator" channel → Gate B resolved (§2, §3).
- The supervisor asked the team to **calculate the flow (L/min)** the module needs, and to find a controllable pump (preferred type unclear in the transcript) (§2).
- The supervisor said he will **buy a data logger**; model unknown (§2).
- Spray conditions recorded `[REC]`: washing confound, water quality, gutter, pyranometer kept out of the spray, a pump-channel shunt sized to the pump current (§2).


**Changes on 2026-11-03 (supervisor voice notes relayed to Asim; purchase list):**
- **Module bought:** BANINUAN 100 W, 12 V class (§2).
- **First purchase batch `[DECIDED]`** (green rows of `LAST_Project_Purchase_List.xlsx`, 504.4 SAR excluding the module at 126.1 SAR): DS18B20 waterproof pack of 5 (AliExpress), 4.7 kΩ pull-up, BME280, PYR20 RS485 Modbus, MAX3485 RS485 module, **M5Stack Unit INA226-10A ×2** (panel + pump channels), DS3231, microSD module, terminal blocks, 5 V DC-DC supply, shielded 3-core cable. Battery and charge controller **not** bought.
- **Supervisor `[SUPERVISOR]`:** no battery and no MPPT/charge controller — connect the module to a **DC load** matched to the module's power, as cheap as possible (even something from home); the load must not heat or cool the module (a fan or LED lamp must be kept away from it). This matches load option C (§4): power resistors on a heatsink, placed away from and downwind of the module.
- **Supervisor `[SUPERVISOR]`:** check how the ESP32 connects to the INA226 units over I2C ("an I2C converter is needed").
- **Supervisor `[SUPERVISOR]`:** he found a cheaper alternative to a ≈ 1000 SAR pump and will study it against the module size and voltage.
- **Three tasks from the supervisor `[SUPERVISOR]`:** (1) build the models (train now on synthetic data, retrain on real data later); (2) learn how to deploy them on the controller; (3) the water delivery method (pipes or spray), flow variation, at the lowest cost.
- **Team capacity:** Asim reports that most of the work will fall on him; tasks to teammates should be small, dated and with a defined output `[REC]`.
- **Team page version 19 (evening):** new chapter 1 «المنظومة وهي تشتغل» — a 3D model of the planned rig and an illustrative simulation of spike-triggered control vs the fixed threshold (§11); chapters renumbered 1–14; heading font changed from Kufi to IBM Plex Sans Arabic; the pump variable-speed note added to chapters 5, 9, 10 and 14.

**Decision 2026-11-04:** `Decision: water delivery by front-surface spray, not the water film (2026-11-04, decided by Asim for the team).` Consequences `[REC]`: (a) choose the nozzles first — flow, pressure and coverage of the 1200 × 540 mm glass — then the smallest brushed 12 V pump that delivers that pressure, speed-controlled by PWM through a MOSFET (supervisor task 3); (b) the energy case now rests on spray cooling with evaporation: under the documented no-evaporation assumption a pump above ≈ 10 W cannot pay back, so the first water test measures the real cooling per second of spraying and the pump's electrical power; (c) the spray conditions in §2 apply (washing confound, distilled or RO water, gutter and tank, PYR20 kept out of the spray, pump-channel shunt sized to the pump current). Team page updated (version 26): spray is the default in the simulation, with a manual spray button for demonstrations; the film stays only as a comparison.

**Correction 2026-11-04 (Asim):** the team has **no 30 W module** — earlier records that one was bought were wrong. Bench work moves `[REC]`: (a) the PWM resistor load and DAQ are first tested on a **current-limited lab DC power supply** (≈ 21 V, current limit ≈ 5.9 A, borrowed from the university lab) — an approximation of the module's I-V curve that is good enough to test switching, the INA226 readings and the sweep logic, not MPP accuracy — then on the 100 W module; (b) the **pump curve** (flow and power vs PWM duty) is measured in a bucket and needs no module; (c) **cooling tests** (spray vs film) run on the 100 W module itself once the Phase 0 sensors work. Buying a 30 W module is not needed. Team page updated (version 24).

**Orders placed 18-20 Oct 2026 (AliExpress screenshots, total ≈ 643 SAR at 3.75 SAR/USD) `[DECIDED]`:** M5Stack INA226-10A ×2 (received); **M5Stack PaHub V2.1 (PCA9548AP, 6 channels)** — resolves the 0x41 address clash, one INA226 per channel; HY2.0-to-DuPont leads; DS18B20 waterproof ×10; DS3231 + AT24C32; MAX3485; PYR20 dual output (RS485 + 0-2 V); 5 V 3 A buck; shielded 3-core 24 AWG 10 m; silicone 3-core 2 m; blade-fuse kit and two inline holders (10 A). **Error `[REC]`:** the pull-up resistor ordered is 4.7 Ω ("4.7R"), not 4.7 kΩ — buy 4.7 kΩ locally. Not seen in the screenshots: BME280, microSD module, terminal blocks — confirm. Still missing: 12 V mains adapter, ESP32 (if none), load parts, enclosure, radiation shield.

**Pump speed `[SUPERVISOR]` (3 Nov):** the pump shown to him is fixed-speed (a misting type) — rejected; he wants variable speed and says a cheap pump can be made variable by an easy method the team should find. `[REC]`: PWM speed control of a cheap brushed 12 V DC pump through a logic-level MOSFET (or a ready PWM motor-speed module) driven by the ESP32, with a flyback diode; avoid pumps with an internal pressure switch or an integrated brushless driver that does not accept PWM on its supply; measure flow and power at several duty cycles (pump curve) and the minimum duty at which it still runs.

**Pump power that can pay back, under the documented cooling assumption `[EST]` (3 Nov):** with 2 L/min of water warming by 5 °C and no evaporation (§2 flow estimate), cooling removes ≈ 0.70 kW. With the Faiman model at pvlib's default coefficients (U = 25 + 6.84·v W/(m²·K)), A ≈ 0.65 m² and ≈ 0.36 W per °C at 900 W/m², the energy recovered per second of pumping is ≈ k·q/(A·U) ≈ 10 J at 2 m/s wind and ≈ 12 J at 1 m/s, independent of the module's heat capacity and of the burst length. So a pump drawing more than ≈ 10 W cannot pay for itself under this assumption; a 17–33 W spray pump pays only if evaporation raises the cooling rate several-fold — measure it on the 100 W module before choosing spray. Simulation results (team page chapter 1; ambient and water 30 °C, an assumption): one 2→1 m/s spike at 900 W/m² is detected after ≈ 5 min; the film pump at 1.5 W gets a 31 s burst and nets ≈ +5 W·min; the 25 W spray pump is refused by the net-power rule. Over 60 min without spikes, the 45/35 °C fixed threshold with a 1.5 W pump nets ≈ +240 W·min against 0 for spike-triggered control, and with a 25 W pump it loses ≈ −420 W·min. Implications `[REC]`: (a) with a cheap low-power pump, strategy (4) will probably win on energy, so the thesis should be framed as real-time detection + event-triggered control + the operating envelope (when pumping pays), not as "saves more energy than a fixed threshold"; (b) the net-power rule earns its place when pumping is expensive (spray); (c) after each burst the panel re-heats by about the amount cooled, so the detector must not count its own re-heating as a spike — subtract the controller's own cooling effect with the thermal model, or blank the detector after bursts; (d) with water at ≈ 31.5 °C or warmer the 45/35 °C rule cannot reach 35 °C at 900 W/m² without evaporation, so the fixed threshold needs cool water or a different off-setpoint.

**M5Stack Unit INA226-10A facts `[DS — m5-docs]`:** 5 mΩ shunt, ±10 A, bus 0–30 V, isolated power and I2C, 5 V supply on the HY2.0-4P (Grove) port, **fixed I2C address 0x41** (no address option documented). Consequences `[REC]`: (a) two units on one bus collide → put each on its own ESP32 hardware I2C bus (two controllers available) or use a TCA9548A multiplexer; (b) the I2C logic level on the Grove side is not documented → measure the idle SDA/SCL voltage on arrival and add a 3.3 V↔5 V level shifter if it is near 5 V (likely the supervisor's "I2C converter"); (c) place the panel unit between the module and the load capacitor, where current stays ≤ Isc (5.88 A) — after the capacitor the PWM pulses reach ≈ 14 A, above the unit's ±10 A; (d) 5 mΩ gives ≈ 0.32 mA resolution, ample for a 1 % signal.

---

## 1. Project identity

- **Title `[SUPERVISOR]` (sent 2026-09-30):** *AI-Driven Smart Cooling for PV Panels Based on Real-Time Thermal Spike and Sudden Degradation Detection*
  - Previous title *AI-Based Predictive Cooling and Net Energy Optimization of a Photovoltaic System* is superseded.
  - **Wording note `[REC]`:** in PV literature "degradation" means long-term, permanent loss, so examiners may read "Sudden Degradation" that way. Suggested wording, to raise only **after** the supervisor confirms §1.2: *…Real-Time Thermal Spike and Sudden Power-Loss Detection*. The supervisor decides.
- **Program:** Senior design, Mechatronics Engineering, Umm Al-Qura University.
- **Team:** 5 — 3 Mechatronics (Asim, lead) + 2 Mechanical.
- **Supervisor:** د. عبدالإله الحمياني ("الدكتور").
- **Lab `[DECIDED]`:** د. عبدالمجيد granted lab access (ask his permission before each visit; the project can be kept in the lab) and offered to review parts for quality and price before purchase, after the team consults the supervisor. Lab equipment is mostly old, so parts are bought new. At the lab visit the supervisor cautioned the team not to raise expectations; what the lab can lend is unconfirmed.

### 1.1 Supervisor direction — voice note, 2026-09-30 `[SUPERVISOR]`

1. A **control** project is different from a **performance-improvement** project. Performance improvement needs two panels whose performance is nearly identical; if they differ, work on one panel.
2. **Our goal is control:** *"Can we control using AI or not"* — this is the bottom line.
3. The target he was aiming for: *"Can we control using temperature degradation"* together with AI. If achieved, he considers it new and a strong result for the team.
4. **One panel is enough:** collect temperature, power and solar (irradiance) data from it → estimate the degradation → control based on it.
5. **Control idea:** at times the panel temperature rises about **3–4 °C within a certain period** in one step instead of gradually. When that jump happens, the pump is commanded to pump **based on the jump**, in an optimization-like way.
6. **Physical vs modeling:** the physical implementation may be costly. If a physical experiment is feasible, do it; if not, **modeling alone is sufficient**.

### 1.2 Working interpretation `[REC]` — `[PENDING — supervisor confirmation]`

Inferred from §1.1; to be confirmed in **one message** that states this understanding and asks "is this what you mean?", rather than as open questions.

| Term | Working interpretation | Reasoning |
|---|---|---|
| **Degradation** | Reversible, temperature-induced power loss — it disappears when the module cools | The supervisor ties it to a sudden temperature jump and to pumping in response; long-term degradation is invisible over minutes and cannot be reversed by a pump |
| **Thermal spike** | A rise of about 3–4 °C **within minutes**; exact threshold and window tuned from the team's own data (Phase 1) | Normal heating is ≈ 0.1 °C/min (Moharram et al. 2013, §1.6), i.e. 3 °C in about half an hour; 3–4 °C within a few minutes is the jump he describes |
| **AI role** | Detects the spike in real time on the ESP32 and decides to pump **only if the expected power gain exceeds pump consumption**; benchmarked against a simple threshold rule | "Can we control using AI" needs a non-AI baseline to answer; "optimization-like" points to a gain-vs-cost decision |
| **Modeling** | Real data measured on the panel + a model of the cooling effect | He wants data pulled from the real panel first; only the cooling side may be modeled |
| **Success criteria** | Detection quality vs the threshold rule (precision/recall, detection delay, false alarms per day), temperature response after a trigger, and net energy `P_PV − P_pump` compared with a conventional fixed-temperature threshold (§3) | Proposed by the team for the supervisor to approve |

Until confirmed, documents describe these as the team's interpretation, not as the supervisor's words.

### 1.3 Research question `[REC]`

> *Can an AI model running in real time on a microcontroller detect thermal spikes — and the sudden, temperature-induced power loss they cause — on an outdoor PV module, and use that detection to trigger cooling that recovers more power than the pump consumes?*

Sub-questions:
1. **Characterization:** how often do thermal spikes occur on a module in Makkah conditions, how large are they, and what drives them (irradiance steps, wind lulls)?
2. **Detection:** does the AI detector beat a simple temperature-rate threshold on precision, recall and delay?
3. **Control:** does spike-triggered cooling produce a positive net power `P_PV − P_pump`, physically or in the model, and how does it compare with a conventional fixed-temperature threshold?

**Framing so that every outcome is reportable `[REC]`** (carried over from the literature doc §7 logic): if the AI ties with the threshold rule, report under which conditions and why; if spikes turn out to be rare, the spike statistics and the break-even pump draw are themselves results.

### 1.4 Role of net power `[REC]`

`P_net = P_PV − P_pump` was the project's core objective (formerly `[DECIDED]`). Under the supervisor's direction it is **no longer the research question**; it becomes:
- the **decision rule** inside the controller — pump only when the expected recovered power exceeds pump draw, which matches the supervisor's "optimization-like" wording;
- a **key evaluation metric** (§1.2, success criteria). Water consumption is tracked if the physical path is taken.

### 1.5 Division of labor

- **Mechanical:** module thermal behavior and the **thermal model of the cooling effect** (the core deliverable on the modeling path), cooling channel, hydraulics, pump sizing, module mounting.
- **Mechatronics:** sensors, DAQ, PV measurement load, spike characterization, AI detection model and TinyML deployment, control logic, data analysis, integration.

### 1.6 Novelty position `[REC]` + `[OPEN]`

- Do **not** claim AI-based or predictive PV cooling, or net-energy optimization, as new: both exist in the literature `[LIT]` (literature doc §1.1).
- Candidate contribution: **real-time, on-device detection of thermal spikes from measured data on an outdoor module, driving event-triggered, net-energy-aware cooling**, validated physically or with a model calibrated on real data, in Makkah conditions, with direct V/I measurement.
- **First-pass targeted search (2026-09-30) `[LIT]`:**
  - Closest prior art: Novak & Landkamer (2026), *Energy Conversion and Management: X* 30:101743, DOI 10.1016/j.ecmx.2026.101743. ESP32 edge nodes and a Raspberry Pi fog layer (MQTT) switch a cooling pump when panel temperature exceeds a fixed threshold (value not published), with activation modulated by barometric-pressure gradients and lightning detection. Measured 6.0 W pump on the cooled branch of a 600 Wp installation split into a cooled and an uncooled reference branch (≈ 300 Wp each); 5 s logging; mean daily gain 5.84 % over 52 valid days. On a representative high-irradiance day the pump ran ≈ 8.8 h (≈ 52.7 Wh) and ROI_E = E_gain / E_pump = 1.07, i.e. the gain barely covered the pump energy.
  - Moharram et al. (2013), *Ain Shams Eng. J.* 4(4):869–877: cooling starts at a maximum allowable temperature of 45 °C and returns the panels to 35 °C; panel heating rate 6 °C/h (0.1 °C/min).
  - Not found: cooling triggered by a detected rapid rise of module temperature, a detector running on the microcontroller, or event-level evaluation of a cooling trigger against a threshold baseline. PID and fuzzy controllers may react to the rate of temperature change, so the defended difference is event detection on the MCU with event-level evaluation, not "use of dT/dt" `[REC]`.
- **Wording `[REC]`:** state the contribution "to the authors' knowledge" and cite the works above; the search was budget-limited.
- `[OPEN]` Read the full texts of Novak & Landkamer (2026) and Bahaidarah et al. (2013), *Energy* 59:445–453 (back-surface water cooling in Dhahran, model and experiment; reference for path 3M) before writing the literature chapter.

---

## 2. Hardware — current state

**PV module — `[SUPERVISOR]` (meeting 2026-10-11): buy a new module.** The borrowed 100 W module is not available. Rating `[OPEN — confirm with the supervisor]`. Recommendation `[REC]`: a **100 W, 12 V-class (36-cell) monocrystalline** module with Voc below about 25 V, whose datasheet states the temperature coefficients and NOCT — it keeps the load, shunt and INA226 sizing in this file valid, and the datasheet coefficients become a reference for the measured γ. (No 30 W module exists — correction of 2026-11-04 above.)
- **Module bought `[DECIDED]` (reported 2026-11-03): BANINUAN 100 W monocrystalline, 12 V class.** Seller listing `[DS — listing, not a full datasheet]`: Pmax 100 W, Vmp 18 V, Imp 5.55 A, Voc 21.5 V, Isc 5.88 A, max system voltage 750 V DC, max series fuse 10 A, 1200 × 540 × 30 mm (area ≈ 0.65 m²), 4 kg. No temperature coefficients or NOCT stated → γ measured from I-V sweeps. Checks `[EST]`: Voc 21.5 V < 36 V INA226 limit (margin for cold mornings); 10 mΩ shunt full scale ≈ 8.2 A > 1.25 × Isc ≈ 7.4 A; R_mp = Vmp/Imp ≈ 3.24 Ω → with the 1.5 Ω bank D ≈ 0.46 at STC; sweep-start current ≈ Voc/1.5 Ω ≈ 14.3 A (MOSFET ≥ 15 A pulses holds). The §4 sizing stands; order the load parts now. Read the label on the module itself to confirm the listing.
- **Label check `[REC]`:** Voc must stay below **36 V**, the INA226 bus-voltage limit (TI datasheet). Isc sets the shunt value (§4).
- Temperature coefficients are **not available** for the bought module and must **not be invented**: γ is measured from I-V sweeps (§4, §5).

**Single panel `[SUPERVISOR]`, reaffirmed 2026-09-30:** the control goal needs one panel. Two panels belong only to a performance-improvement comparison, and only if their performance is nearly identical. The late-September idea of buying a second panel (raised on د. عبدالمجيد's advice) is closed unless the supervisor reopens it.

**What the signal size implies `[REC]` / `[EST]`** (illustrative γ ≈ −0.4 %/°C, a typical crystalline-silicon value, **not** this module's coefficient):
- **A 3–4 °C spike costs only ≈ 1.2–1.6 % of power** — about **1.3 W** on a 100 W module delivering ~90 W. That is comparable to, or smaller than, power swings caused by irradiance changes over the same minutes. The temperature-induced loss therefore **cannot be read from raw power**; it must be computed from **irradiance-normalized, MPP-true power** (§5). This raises the importance of the PWM resistor load (§4) and of mounting the PYR20 in the plane of the array.
- **Pump budget:** a pump that draws more than ≈ 1.3 W cannot pay for itself on a 3–4 °C spike unless it pulls the temperature down by more than the spike. Rough noon gain for a full 10 °C of cooling ≈ 3.6 W (100 W × 0.9 × 0.4 %/°C × 10 °C). For a **clearly positive** net, keep pump draw at or below about **1 % of module rating ≈ 1 W** — Claude's inference from pump-to-panel ratios in the literature review, not a published rule. Select any pump on *measured* draw at operating flow, not advertised maximum flow.
- **Break-even per degree `[EST]`:** recovered power ≈ |γ| · P_mp ≈ 0.36 W per °C of cooling actually achieved (90 W, −0.4 %/°C). A 1 W pump breaks even at ≈ 2.8 °C, 2 W at ≈ 5.6 °C, 6 W at ≈ 17 °C. Target ≈ 1 W measured draw, upper limit ≈ 2 W `[REC]`. For comparison `[LIT]`, Novak & Landkamer's pump was ≈ 2 % of the cooled capacity and reached ROI_E 1.07 on a representative day with a fixed-threshold trigger (§1.6).
- **Net may still be negative.** That is a reportable result, provided gross gain and the measured (or datasheet-flagged) pump curve are reported separately and the **break-even point** is computed.
- **The load must absorb everything the module produces** — about 80 W through a typical midday, up to about 100 W → a linear MOSFET load is not suitable as the continuous load (§4).
- **INA226 shunt:** 10 mΩ, in the + lead on the module side of the load capacitor (§4).

| Function | Part | Status |
|---|---|---|
| Back-sheet temperature | DS18B20 waterproof ×5 (1-Wire) | In BOM — buy from authorized distributors only: in a 2019 survey of > 1000 probes and chips from > 70 vendors, all probes bought on eBay and AliExpress contained counterfeit sensors `[LIT]` (Petrich, github.com/cpetrich/counterfeit_DS18B20). Before mounting: check the ROM pattern 28-xx-xx-xx-xx-00-00-xx and cross-calibrate all five in one water bath; mount behind cell centres, away from the frame and junction box, with thermal paste, aluminium tape and a small insulating cover `[REC]` |
| Ambient T / RH / P | BME280 (I2C) | In BOM — inside a ventilated radiation shield, away from the module and the load heatsink `[REC]` |
| Irradiance | PYR20 pyranometer (RS485 Modbus RTU + 0–2 V) | In BOM — mount in plane of array `[REC]`. Infwin datasheet `[DS]`: 400–1100 nm, 0–2000 W/m², accuracy 5 %, resolution 1 W/m²; no ISO 9060 class or response time stated. The spectral range is that of a silicon sensor, not a thermopile → use it for irradiance steps and normalization through changes within a window, not as an absolute reference; check the register-to-W/m² conversion on a clear day `[REC]` |
| PV voltage / current | INA226 ×2 (I2C) | In BOM — 10 mΩ shunt for 100 W (§4); most breakout modules ship a 0.1 Ω (R100) shunt that saturates at ≈ 0.82 A, so it must be replaced |
| Microcontroller | ESP32 — **variant to confirm** (§6) | In BOM — any variant suffices for a small tree model run every 10 s; no purchase needed for this reason `[REC]` |
| Host computer | Raspberry Pi | In BOM — role to be re-evaluated under TinyML (§6) |
| Battery | QSSUN AGM 12 V 18 Ah | In BOM — **not a measurement load** (§4); not needed for the thesis |
| Charge controller | Anern 30A MPPT (139 SAR); EPEVER XTRA1206N rejected on price | In BOM — **not a measurement load** (§4) |
| PV measurement load | PWM-switched resistor bank + capacitor + MOSFET/gate driver | `[DECIDED]` 2026-10-01, team (§4); supervisor to be informed at Gate A |

**Proposed additions `[PENDING — supervisor]`** (literature doc §5):
- **Anemometer — priority raised.** Clouds are rare in Makkah, so wind lulls are the most likely driver of thermal spikes; wind speed is needed both to explain spikes and as a detector feature `[REC]`. Key specs `[REC]`: starting threshold well below 1 m/s and 0.1 m/s resolution, because module temperature is most wind-sensitive below about 3 m/s (Faiman model, §5.1); mount beside the module at module height so it sees the same wind.
- Reservoir DS18B20 and MOSFET driver for PWM pump control — now needed: the physical spray path was chosen on 2026-10-11.

**Small items for the team `[REC]`:** neutral shade mesh (≈ 20 %) and a windbreak board for controlled spike tests (§3); a radiation shield for the BME280; a sensor mounting kit (thermal paste, aluminium tape, insulating covers); an RTC such as the DS3231 if not already in the BOM, so timestamps survive Wi-Fi drop-outs.

**Cooling hardware `[SUPERVISOR]` (meeting 2026-10-11):** physical cooling by **front-surface water spray** through garden-type nozzles — confirmed by the team on 2026-11-04 over the water-film alternative `[DECIDED]`. A back-surface "radiator" channel was discussed and set aside because it would need a tank and a way to cool the water. Pump: the supervisor prefers a type recorded in the transcript as "بي دي بي", easy to control but possibly expensive; otherwise any pump whose flow can be controlled `[OPEN — confirm the type with him]`. Task from the supervisor: **calculate the flow (L/min) the module needs**.
- **First flow estimate `[EST]`** (module area ≈ 0.65 m² assumed until the module is chosen; τ ≈ 6.3 min; Faiman U ≈ 35 W/(m²·K) at 1.5 m/s): heat capacity ≈ τ·U·A ≈ 8.7 kJ/K, so cooling by 10 °C removes ≈ 87 kJ, and holding the module 10 °C cooler removes ≈ 230 W. If the water warms by 5 °C: ≈ 2 L/min cools 10 °C in 2 min and ≈ 0.7 L/min holds it; evaporation lowers both. Heat removal is therefore not the binding constraint — **uniform coverage of the glass by the nozzles is**. Choose the nozzles first (flow and pressure from their datasheet), then a pump that delivers that flow at that pressure.
- **Spray pump power and burst control `[EST]`:** spray needs pressure; e.g. 2 L/min at 2 bar is ≈ 7 W hydraulic, ≈ 17–33 W electrical at the 20–40 % efficiency typical of small pumps (literature doc §6.3). The ≈ 1–2 W budget above was for continuous low-pressure pumping and does not carry over: the spray must run in **short bursts**. Gain per burst ≈ 0.36 W/°C × ΔT_achieved × τ, because the module re-heats over ≈ 6 min: a 10 °C burst is worth ≈ 23 W·min, so a 25 W pump breaks even at ≈ 55 s of spraying. The controller's decision becomes **how long to spray** — the supervisor's "optimization-like" control. Measure the real pump power and flow in a bucket first (pump curve), then the cooling on the 100 W module.
- **Conditions for spray `[REC]`:** (1) washing confound (literature doc §3.1): clean the module every morning before tests and log it; measure the washing effect alone once by spraying early in the morning, when the module is near ambient temperature, so a power jump then is cleaning, not cooling; (2) distilled or RO water — tap-water minerals leave deposits on the glass; (3) a gutter at the lower edge returning water to a small tank (no water-cooling system), which saves water and lets consumption be measured; a reservoir DS18B20 logs the water temperature; (4) keep the PYR20 out of the spray and the runoff — droplets on the sensor distort the irradiance reading; (5) the second INA226 (pump channel) needs a shunt sized to the pump current: the stock 0.1 Ω saturates at ≈ 0.82 A, below the ≈ 1.4–2.8 A of a 17–33 W pump at 12 V — choose it once the pump is chosen.
- **Precedent `[LIT]`:** Moharram et al. (2013) cooled panels with water in bursts, starting at 45 °C and stopping at 35 °C (§1.6).

**Data logger `[SUPERVISOR]` (meeting 2026-10-11):** the supervisor said he will buy a data logger; model and channels `[OPEN]`. It does not replace the ESP32 DAQ — the detector must read its own sensors in real time — but it may serve as the reference instrument for validation (§9).

---

## 3. Phase plan — this numbering supersedes all earlier numberings

Aligned to the supervisor's 2026-09-30 direction `[REC]`. Sequencing rule unchanged: no phase's *build* starts before the previous phase's exit criteria are met; paper design (e.g. the Mechanical team's thermal model) may run in parallel.

| Step | Content | Status |
|---|---|---|
| **Gate A** | Supervisor confirms §1.2 (one message) · module rating confirmed, module bought, datasheet and label recorded · supervisor informed of the load decision, option C (§4) · lab reference instrument asked for · anemometer approval asked for | **NOW** |
| **0** | DAQ build & validation + PWM resistor load (bench-tested on a current-limited lab supply, then on 100 W) + uncertainty budget + cleaning protocol + timestamp check | Next |
| **1** | Baseline data on the bare panel + **thermal-spike characterization**: frequency, size, drivers; spike definition fixed from data; temperature-induced loss computed; γ measured | Planned |
| **2** | **AI spike detector**: threshold-rule baseline → gradient-boosted trees; trained offline, deployed on the ESP32 (TinyML), validated on-device | `[SUPERVISOR]` TinyML direction |
| **Gate B** | Resolved 2026-10-11 `[SUPERVISOR]`: **physical (3P)**, front-surface spray; still open: nozzle and pump selection from the flow calculation (§2) | Done (parts open) |
| **3P** | Build the loop, measure `P_pump(flow, PWM)`, run spike-triggered control on the real panel | If chosen |
| **3M** | Thermal model with a cooling term, calibrated on Phase 1 data; simulate control driven by the real measured time series | If chosen |
| **4** | Evaluation and thesis: detection metrics, temperature response, net energy, break-even, spike statistics for Makkah | Planned |

**Control strategies compared in 3P or 3M:** (1) no control, (2) threshold-triggered pumping (temperature-rise rule), (3) AI-triggered pumping with the net-power decision rule `[REC]`; (4) **fixed absolute-temperature threshold as used in the literature** — e.g. on at 45 °C, off at 35 °C (Moharram et al. 2013, §1.6) `[DECIDED]`. Continuous cooling is an optional reference.
- `Decision: add strategy (4) to the control comparison (2026-09-30, decided by Asim).` Reason: the value of cooling depends on the temperature level, not on its rate of rise, so examiners will ask why the controller reacts to spikes; comparing against the literature's level trigger answers that directly. Either outcome is reportable: if spike-triggered pumping wins, that is the contribution; if it loses, the conditions and reasons are the result.
- How to run it `[REC]`: in 3M, sweep the setpoint and report the best fixed threshold, not only 45 °C, so the spike trigger is compared against the strongest version of the level trigger; where possible run it with and without the net-power rule, so the effect of the trigger (level vs rate) is separated from the effect of the decision rule.
- Supporting physics `[LIT]`: the benefit of cooling falls sharply with wind — 7 % at 0 m/s vs 2.8 % at 3 m/s in Alrashidi et al. (2025), an indoor passive-hydrogel study (literature doc §3.3, §8.2) — which is the argument for pumping during wind-lull spikes.

**Supervisor direction (Sep 2026) `[SUPERVISOR]`:** integrate ML with the microcontroller — the model runs **on the ESP32 itself (TinyML)**. Collect data first, then decide next steps. The title's "Real-Time" fits this.

**Phase 0 exit criteria:**
- Sensors installed correctly; readings physically plausible; noise levels characterized
- Timestamps verified: RTC synced to NTP, and on clear days the measured in-plane irradiance curve lines up with the modeled in-plane clear-sky curve for the panel's actual tilt and azimuth. For a due-south mount the peak falls at the **computed solar noon** (in Makkah between about 12:04 and 12:35 local time depending on the season), not at 12:00; each degree of azimuth error moves it by about 1 min (§7)
- Reliable logging at ~10 s sampling
- PWM resistor load holding the MPP and sweeping I-V on the 100 W module (§4); validated against a reference instrument if one can be borrowed
- Uncertainty budget computed **before** data collection and shown to be smaller than the effect to be detected (≈ 1–2 % of power per spike; literature doc §2.3)
- Written cleaning protocol (literature doc §3.1)
- Wind speed logged, if the anemometer is approved

**Phase 1 exit criteria `[REC]`:**
- Spike definition (rise threshold + time window) fixed from the data and justified against normal daily heating rates
- Enough labeled spike events to train and test a detector — planning target ≥ 50 for training and ≥ 20 held out `[REC]`; with fewer, the classical baseline is reported as the primary result and the trees as exploratory
- **Controlled spike tests from the first week `[REC]`** (natural spikes may be rare under Makkah's mostly clear skies): (1) a neutral shade mesh (≈ 20 % attenuation) over the module **and** the PYR20 for ≈ 20 min, then removed — ≈ 3 °C within 6 min and ≈ 4 °C within 10 min `[EST]`, labelled irradiance-driven; (2) a windbreak board upwind for ≈ 20 min on a breezy day — wind-driven, flat irradiance; (3) negative controls that must not trigger — shading under 1 min, cleaning. Each test is logged with a button on the ESP32. A fully opaque cover is avoided: it produces a step above 20 °C `[EST]`. The shade tests also give the module's thermal time constant for the 3M model
- Rises caused by returning irradiance (after cloud or dust) labelled as a separate class until the supervisor decides (§9)
- Irradiance-normalization method fixed (§5); temperature coefficient γ measured from I-V sweeps

**Team goal of 2026-09-26** ("first achieve cooling under control and AI, then decide the direction") is absorbed into this plan now that the supervisor has given his direction.

---

## 4. PV measurement load — option C `[DECIDED]`

**Status:** recommended 2026-09-29; `Decision: adopt option C, PWM-switched resistors with ESP32 control, as the PV measurement load (2026-10-01, decided by Asim for the team).` The supervisor is informed at Gate A; any objection from him reopens this section. Part numbers remain `[OPEN]` (list below). Under the new direction it matters even more: the temperature-induced loss is estimated from power, and a load that holds the module off its MPP distorts exactly that estimate.

**Why the load choice matters `[REC — physics-based]`:** module power depends on the operating point on the I-V curve. A fixed resistor matches the MPP only near one condition; on the illustrative model it captures about 63 % of available power at 500 W/m² and 45 °C cell temperature. At low irradiance the operating point sits in the current-source region, where power barely depends on temperature, so the thermal effect appears near zero; on hot middays it is exaggerated. A battery with a PWM controller pins the module near 13.5 V, and its captured fraction *improves* as the module heats, which inverts the effect being measured. **The earlier recommendation of a fixed resistive load at Vmp is superseded.**

**Options at 100 W `[REC]`:**

| Option | Holds MPP | I-V sweep | Where the heat goes | Verdict |
|---|---|---|---|---|
| A. Linear MOSFET load + op-amp loop | Yes | Yes | In the MOSFETs, about 80 W through midday in the sun | Fine at 30 W, **not at 100 W** |
| B. Commercial MPPT + battery | Only while the battery charges; closed algorithm | No | Into the battery | **Not a measurement load** |
| **C. PWM-switched resistors + input capacitor, ESP32 control** | **Yes, our own algorithm** | **Yes, same circuit** | Resistors on a heatsink; the MOSFET only switches | **Adopted (2026-10-01)** |
| D. Fixed resistors + separate sweep circuit | No | Yes | In the resistors | Fallback; compute everything from sweep Pmp only |
| E. Programmable DC load from the lab | Yes (CV mode) | Yes | Inside the instrument | Best if it can be borrowed for months; otherwise the validation reference |

Sources `[LIT]`: Durán et al., IEEE PVSC 2008 (families of I-V measurement methods); Machado et al., Applied Sciences 15:8186 (2025) (MOSFET loads need analog control and large heatsinks); Esram & Chapman, IEEE Trans. Energy Conversion 22(2), 2007 (MPPT methods incl. perturb-and-observe).

**How option C works:** module → shunt (INA226, + lead) → large input capacitor → power resistor bank switched by a MOSFET at about 20 kHz. The module sees an effective resistance **R_eff ≈ R ÷ D**. This is the ideal average: wiring inductance with a freewheel diode shifts it by a few percent, which does not matter because the ESP32 regulates D on the *measured* voltage. The ESP32 holds the module at the Vmp from the latest sweep; perturb-and-observe between sweeps is optional. Every 1–2 minutes it steps D from 0 to 1 over 1–2 s and logs the I-V points. With R ≈ 1.5 Ω the sweep reaches about 9 V at STC (Isc × R); the flat region below that is not needed for Pmp.
- Gives true Pmp plus I-V parameters (Voc, Vmp, Imp, FF; Isc by extrapolation).
- Enables **experimental measurement of the module's temperature coefficients** (fills the datasheet gap).
- Bench plan (revised 2026-11-04, no 30 W module): build and test on a current-limited lab DC supply first, then connect the 100 W module.
- Independent review (2026-09-29, second Claude agent, switch-level simulation on the page's I-V model): R/D within 0.4 %; D, dissipation, ripple, MOSFET loss and sweep floor below reproduced; its corrections are included here.

**First-pass sizing for 100 W `[EST]`** (assumes 18 V / 5.5 A at MPP; recompute from the label):

| Item | First value | Reason |
|---|---|---|
| Resistor bank | ≈ 1.5 Ω total, absorbing up to ≈ 100 W; rating chosen from the maker's derating curve at Makkah temperatures (a 25 °C nameplate is not enough); heatsink in shade, downwind of the module and the air sensor | Holds MPP at D ≈ 0.46 (≈ 0.51 on a hot noon) |
| Input capacitor | ≥ 2000 µF total, low ESR, ≥ 35 V, several in parallel | Ripple current up to ≈ 6 A RMS (≈ I_peak/2 near D = 0.5), so the parallel set must exceed it with margin. Voltage ripple ≈ 0.075 V p-p from capacitance plus 12 A × ESR; negligible effect on power at the MPP |
| INA226 shunt | **10 mΩ**, 4-terminal, ≥ 1 W, low TCR; high side (+ lead) **between the module and the capacitor**; VBUS tied to the module side (IN+) | Shunt full scale 81.92 mV (TI datasheet) → ≈ 8.2 A, which covers the usual 1.25 × Isc allowance for bright sun (13 mΩ would leave only ≈ 7 % headroom). After the capacitor the current pulses to 12–15 A (V/R) |
| Switch | MOSFET V_DS ≥ 60 V, rated for ≈ 15 A pulses (22 V ÷ 1.5 Ω at sweep start); gate driver with a 3.3 V-logic input, fed from the DAQ supply (not the PV line, which falls to a few volts during a sweep); gate pull-down; avoid ESP32 strapping pins; ≈ 20 kHz | Estimated loss ≈ 0.7 W, assuming ≈ 7 mΩ hot R_DS(on) and ≈ 100 ns total switching time. ESP32 LEDC PWM gives about 11-bit duty resolution at 20 kHz, enough here |
| Protection | Non-inductive resistors or a freewheel diode across the bank; TVS across the MOSFET and a tight capacitor–MOSFET–resistor loop; reverse-polarity protection (polarized connectors, polarity check, input clamp); heatsink over-temperature trip that sets D = 0 | Inductive turn-off spikes destroy MOSFETs; the diode does not cover wiring inductance. A single module cannot deliver more than Isc, so a fuse matters only if a battery or supply is ever connected |
| Grounding & sensing | INA226 and ESP32 ground at the capacitor negative, outside the pulsed-current loop; sense voltage at the module terminals, or subtract a measured cable resistance | Cable drop (≈ 0.5–1.3 % of Vmp for 2–5 m runs of 4 mm² cable) grows with current and slightly distorts the I-V curve and γ |
| Sweep & logging | Step D and let it settle (~20 ms) before each reading, or use the shortest INA226 conversion time; **flag sweep samples and exclude them from energy totals** | The INA226 converts shunt and bus voltage in sequence, not simultaneously. During a sweep the module averages ≈ 2/3 of Pmp, which would bias logged energy low by ≈ 0.3–1.1 %, the same order as a single spike's effect |

`[OPEN]` for the Mechatronics team: exact MOSFET, gate driver, TVS and capacitor part numbers; resistor type and rating from the derating curve; heatsink size for ≈ 100 W; reverse-polarity circuit; cable length and voltage-sensing method; INA226 conversion-time and averaging settings (verify from the datasheet).

**Decoupling note:** net power is an accounting equation. The pump can run from any supply, provided its power is measured. The battery is not required for the thesis.

**Reference check `[REC]`:** borrow a programmable DC electronic load or I-V curve tracer from the college lab for at least one validation day, and compare the DIY sweep against it.

---

## 5. Single-panel method — the expected-power model `[REC]`

One model now serves two purposes: it produces the **detection signal** (the temperature-induced loss) and, on the physical path, the **counterfactual** for a cooled panel.

**5.1 Temperature-induced loss (the "degradation" signal of §1.2):**
1. Measure γ (Pmp temperature coefficient) from I-V sweeps (§4). The applicable IEC procedure for determining temperature coefficients must be verified before it is cited.
2. At each timestep, correct the measured power to 25 °C: `P_25 = P_mp,meas / [1 + γ · (T_cell − 25)]`; the temperature-induced loss is `ΔP_T = P_25 − P_mp,meas`.
3. A **spike** shows as a rapid rise of `T_cell` and of `ΔP_T` while irradiance-normalized power at 25 °C stays steady. A power drop **not** explained by temperature (irradiance step, shading, soiling, fault) is a different event and must **not** trigger the pump — the detector needs irradiance and, if approved, wind as inputs to tell them apart.
4. **Spike physics `[LIT]` / `[EST]`:** the module thermal time constant is ≈ 6.3 ± 1 min for fixed-mount PV (Herteleer et al., *Solar Energy* 2023; 24 datasets from 15 sites) → a 5–10 min detection window, and ≥ 30 samples per event at 10 s logging. Normal heating ≈ 0.1 °C/min (Moharram et al. 2013). With Faiman's default coefficients in pvlib (U0 = 25 W/(m²·K), U1 = 6.84 W·s/(m³·K)), a wind drop from 2 to 1 m/s at 900 W/m² raises module temperature ≈ 5 °C at steady state — ≈ 3 °C within 6 min and ≈ 4 °C within 10 min; the module's own coefficients are fitted in Phase 1.
5. **Measurement limits `[REC]`:** the PYR20's absolute accuracy (5 %) exceeds a spike's effect, so detection uses changes within the window, not absolute normalized power. Cell temperature exceeds back-sheet temperature by ≈ 3 °C at 1000 W/m² for open-rack glass/polymer-backsheet modules (Sandia array model, King et al. 2004) `[LIT]`; the offset cancels in detection but enters the loss magnitude.

**5.2 Counterfactual under cooling (physical path 3P):**
1. **Uncooled-temperature model** trained on no-cooling data using **exogenous inputs only**: plane-of-array irradiance (with lagged/rolling values for thermal inertia), ambient temperature, wind speed, humidity, solar geometry. **Never** use measured panel temperature as an input — cooling changes it.
2. At each timestep under cooling:
   `P_uncooled,est = P_mp,meas × [1 + γ · (T_pred,uncooled − T_meas)]`
   `Cooling gain = P_mp,meas − P_uncooled,est`; `Net gain = Cooling gain − P_pump`
3. Same panel, same moment → irradiance, soiling state, and panel-to-panel mismatch cancel out.

**Baseline on the physical path `[REC]` (spray, 2026-10-11):** spray hardware leaves the rear of the module open, so there is no passive channel penalty and the bare panel is the baseline. The confound is washing instead (§2, conditions for spray). The earlier channel-specific requirements (a pump-off channel baseline, a removable channel) no longer apply.

**Uncertainty (illustrative only, not this module's spec):** with a typical c-Si γ of roughly −0.3 to −0.5 %/°C, a 1.5 °C temperature-model error ≈ 0.5–0.75 % of power (≈ 0.5–0.7 W at ~90 W). A single spike is worth ≈ 1–2 %, so model error and measurement uncertainty must be kept well below that. The real model RMSE and measured γ must enter the uncertainty budget.

**Validity limit:** models are valid only within their training conditions. Collect baseline data over a wide range of conditions and schedule periodic no-cooling days to detect drift.

**Precedent:** analogous to regression-baseline methods used in energy savings measurement & verification (M&V).

---

## 6. TinyML on ESP32 — `[SUPERVISOR]` direction, implementation `[REC]`

- **Model role (new):** real-time **spike detection** on each sample; optional short-horizon spike **prediction** later (§8). The pump decision applies the net-power rule of §1.4 on top of the detector output.
- **Workflow:** train offline in Python on PC → export model to C → inference on the ESP32 at each sample (10 s interval is not time-critical).
- **Baseline first:** a temperature-rate threshold rule (ΔT over a window) is the benchmark the AI must beat; it is also strategy (2) in §3.
- **Strongest baselines `[REC]`:** (a) the threshold rule gated on flat irradiance over the window (e.g. |ΔG/G| < 5 %); (b) CUSUM on the residual between measured module temperature and a weather-driven thermal model, which fires only when temperature rises more than irradiance and wind explain. Split train/validation/test by whole days, never by random samples.
- **Model family:** gradient-boosted trees (XGBoost / LightGBM) preferred; no DL/LSTM `[LIT]` (literature doc §6). Tree ensembles compile to plain if/else C, well suited to microcontrollers.
- **Event metrics, not average error:** precision, recall/F1, detection delay, false alarms per day; handle class imbalance (spikes are rare events); chronological train/test split with no leakage.
- **Candidate features** (all computable on the ESP32 from its own data stream): temperature rise rates over several windows, irradiance steps (dG/dt), clear-sky index, `ΔP_T` from §5.1, wind speed if approved, solar geometry.
- **Export tool candidate:** m2cgen (supports XGBRegressor / LGBMRegressor → C). It has known open issues with newer XGBoost versions and reported C-vs-Python output mismatches → **pin library versions and verify**. With XGBoost set `base_score` explicitly. Alternative exporter `[REC]`: emlearn for scikit-learn tree ensembles.
- **Mandatory deployment check:** run the held-out test set through Python and through the ESP32; outputs must agree within a stated float tolerance. Report this in the thesis.
- **Feature parity:** every model feature must be computable on the ESP32. Train/serve skew is the main failure mode.
- **Size budget:** limit `n_estimators × max_depth` to fit flash/RAM; measure on the target board.
- `[OPEN]` **ESP32 variant** (classic / S3 / other) — determines memory and available acceleration. Confirm from the actual board.
- `[OPEN]` **Raspberry Pi role** — may shrink to logging/dashboard/training host, or be replaced by SD logging + PC. Decide after the variant is confirmed.

---

## 7. Solar Analysis Tool (Asim, Apr 2025) — reuse as a module `[REC]`

- Extract the computation core (solar position, clear-sky irradiance, tilt transposition) from Flask into a standalone Python module. Port the needed parts to C later for ESP32 features.
- **Validate** solar position against pvlib-python (NREL SPA) before use; document maximum deviation.
- **Uses:** Phase 0 pyranometer plausibility and the **solar-noon timestamp check**; clear-sky index for comparing different days and for separating irradiance steps from thermal spikes; solar-geometry ML features.
- **Clear-sky reference in the panel plane `[REC]` (2026-09-30):** the PYR20 is mounted in the plane of the array (§2), so the clear-sky index must divide by the tool's clear-sky irradiance **transposed to the panel's actual tilt and azimuth**, not by horizontal clear-sky GHI. At a 21° tilt (illustrative), in-plane clear-sky irradiance is 12–50 % above GHI between October and February, and the ratio drifts during the day (in mid-December from 1.25 to 1.50 between 08:00 and 16:00) — a slow false trend the detector would otherwise learn.
- **Timestamp check depends on orientation `[REC]` (2026-09-30):** the in-plane peak coincides with solar noon only for a due-south mount; each degree of azimuth error shifts it by about 1 min (±10° → ±10 min). So: (1) sync the RTC to NTP over the ESP32's Wi-Fi, so the clock is checked independently; (2) align the whole measured clear-day curve with the modeled in-plane clear-sky curve instead of picking the flat peak. A remaining offset then measures orientation error, not clock error.
- *Basis of the two numbers above `[EST]`:* pvlib 0.16.1, Ineichen clear sky with pvlib's Linke-turbidity climatology, isotropic transposition, Makkah (21.42° N, 39.83° E), four days between mid-October and mid-February.
- The clear-sky model is a **plausibility ceiling only** (dust/aerosols not explicit). Calibration reference: the PYR20's own calibration certificate (check whether one was supplied) plus comparison against the nearest accessible reference station, if any (to verify).
- `[OPEN]` which clear-sky model the tool implements. Path `[REC]`: identify it from the tool's source; compare its clear-sky output with pvlib's Ineichen model on the same timestamps; if it cannot be identified or disagrees beyond a stated tolerance, use pvlib's model, which is documented and citable.
- **Not** project deliverables: Flask UI, sun-path diagrams, tilt tables.

---

## 8. Extensions — only after the core works

The supervisor has now given his direction (§1.1), so the earlier "direction shortlist" is no longer a proposal for him; these are **extensions**, screened with the five questions below and started only after Phase 4's core results exist `[REC]`:
1. **Spike prediction** a few minutes ahead (weather-forecast or trend inputs) — a natural next step from detection.
2. **Soiling and fault detection with the same expected-power model** — separating non-thermal power loss from thermal loss is already needed in §5.1; flagging it as a cleaning alert is almost free.
3. **Cold-water budget / reservoir sizing** (physical path) — literature doc §4.
4. **Heat recovered, a simple PVT view** — `Q = ṁ·cp·ΔT` from the flow and water-temperature sensors (physical path).
5. **Air-conditioner condensate as a free water source** — Shah et al., Sustainability 15:15431 (2023) `[LIT]`; it also cleans the glass, so it must be a separate test.

**Not recommended within the timeline `[REC]`:** reinforcement learning on the ESP32; solar tracking; a battery management system; a passive (e.g. hydrogel) cooling arm; any plant-scale claim from one module.

**Closed-loop water temperature — Decision 2026-11-06 (Asim) `[REC]`:** cooling depends on T_module − T_water. A spare DS18B20 in the tank logs T_water; the decision rule uses measured T_water instead of ambient, so it refuses to pump warm water. `[EST]` a burst for a 3 °C spike removes ~26 kJ; a 20 L tank needs ~84 kJ/°C → ~0.3 °C per burst; the bigger risk is solar gain on tank, gutter and pipes → tank shaded under the module, white or insulated, covered, 20–30 L. Daily water-level or tank weight logs give the water loss (evaporation + drift) for the water-efficiency metric.

**Two make-or-break risks and early de-risking — 2026-11-06 `[REC]`:**
1. Natural ≥ 3 °C spikes may be rare in Makkah (the hard synthetic data already showed this). Does not kill the project: controlled spikes give training events and spike frequency is itself a result. De-risk: install outdoors and log temperature + irradiance from day one, before the spray loop; after one week we know the natural spike rate.
2. (Most serious) The pump may cost more than it recovers: the no-evaporation simulation says > ~10 W cannot pay back; spray pumps are often 17–33 W. Does not kill the project: evaporation in Makkah may multiply cooling (unknown until measured), low PWM duty cuts draw, and a refusing decision rule is a correct result → operating envelope. De-risk: on the pump's arrival day, bucket test (real draw) + 30 s spray on the module in sun (measured temperature drop).
Smaller risks: 1.3 W measurement signal (load + irradiance correction), short logging window (controlled spikes), no anemometer (NASIM works, weaker).

**Scalability and limitations framing — Decision 2026-11-06 (Asim):** the rig is a single-module proof of concept, not a plant system. Say so before the examiners ask; it is a limitation, not a flaw, unless the team over-claims.
- Does NOT transfer as-is: spraying every module (plants have hundreds of thousands of modules; water is scarce and costly in KSA; a spike costs only ~1–2 % of power `[EST]`, so water, pumps and maintenance may exceed the gain); per-module temperature sensors.
- DOES transfer (the project's real value): the NASIM detector runs on data plants already have (met station + string/inverter power) and separates thermal (temporary) loss from soiling (clean) and faults (repair) — useful for O&M; the net-energy decision rule ("cool only when it pays") can time any existing cooling or cleaning system; the operating envelope answers when water cooling is worth it.
- Full system fits: commercial rooftops, hotels, farms with irrigation networks and PV.
- Defence wording: "The rig is a single-module proof of concept. What transfers to plants is the detection algorithm and the decision rule, because they run on data the plant already has. The spray itself suits small and medium systems where water is available." The report must have explicit Limitations and Scalability sections; never claim "improves the efficiency of the Kingdom's PV plants".

**Upgrade candidates — proposed 2026-11-05 `[REC]`, to be discussed in detail later (not started; core first):**
1. Digital twin: identify C, u0, u1 for our module from the first two weeks of data; run the model on the ESP32 beside the real module (fixes CUSUM's model-error weakness).
2. Optimal burst control (MPC-lite): with the twin, choose pump duty and duration for maximum net energy and minimum water — the supervisor's "optimization-like".
3. Live dashboard over ESP32 WiFi (SCADA-style); can run in parallel, e.g. a teammate.
4. Water efficiency: cheap flow sensor; litres per watt recovered.
5. Conference paper if real results are good.
Order: 1 after two weeks of data; 2 and 4 after the pump works; 3 any time in parallel; 5 last.

**Supervisor meeting outcome — 2026-11-10 (reported by Asim) `[STATED]`:** this supersedes the order of work above.
- Priority now: publish one research paper with a narrow, specific, simpler scope; expansion (cooling, pump, diagnose-before-spray, reference panel, extra parts) comes after the paper — the supervisor is open to any expansion then.
- Cooling and pump are set aside for now. No anemometer in this phase. Stick to the agreed specs, no over-engineering.
- Start measurements now: finish the DAQ, install outdoors, log, analyse.
- Definition change: the supervisor counts the temperature rise when the sun returns after a cloud as a thermal spike. Detector labels and features must no longer require stable irradiance; label spikes by rapid temperature rise, and record the driver (irradiance step vs. wind/other) as a separate field.
- Open: exact paper topic, target venue and deadline — to confirm with the supervisor.
- The supervisor will buy a data logger and lend it to the team (reported 2026-11-10; model unknown). Planned use: reference instrument to validate the ESP32 DAQ (temperatures, V, I, PYR20 analog) and a parallel record for the paper; ESP32 stays for the PWM load, I-V sweeps and real-time NASIM.

**Engineering review notes — 2026-11-10 (from a review Asim ran with Gemini and an engineer; assessed by Claude) `[REC]`:**
- Load resistance: 1.5 Ω clips the sweep at D = 1 to V ≥ ≈ 8.8 V (5.88 A × 1.5 Ω), so Isc is not measured and shading steps can hide. Recommend ≈ 0.75 Ω (2 × 1.5 Ω parallel): V_min ≈ 4.4 V, MPP at D ≈ 0.23, start-of-sweep pulse ≈ 29 A → MOSFET pulse rating ≥ 40 A, low R_DS(on). Average resistor dissipation unchanged (≤ panel power). Isc estimated from the flat region. Set in firmware config on 2026-11-10 (R_LOAD = 0.75); team confirms before buying parts.
- Sweep settling: τ ≈ C × (R_eff ∥ panel dynamic resistance); worst case near Isc ≈ 2000 µF × 1.5 Ω ≈ 3 ms → wait 5τ ≈ 15–20 ms per D step, then INA226 read (≈ 1.1 ms conversion, 4-sample average) → ≈ 30 ms/step, ≈ 1.5 s for 50 steps. Verify by logging V every 1 ms after one step.
- Freewheel: fast Schottky across the resistor bank (cathode to +), ≥ 60 V, rated for the pulse current, plus TVS on the MOSFET; confirm in the BOM.
- Placement: resistor bank on a separate shaded stand, downwind, ≈ ≥ 1.5 m from the module and the BME280 (engineering judgment; verify with air-temperature readings with the load on vs off).
- Deferred with cooling (per supervisor): add evaporation to the twin (m'' = h_m(ρ_sat(T_panel) − RH·ρ_sat(T_air)), q = m''·h_fg, calibrate h_m from post-spray decay; humidity from BME280, not assumed); bucket test adds D_min for a full spray cone; thermal-shock guard in the decision rule as a ΔT(panel − water) limit taken from manufacturer cleaning guidance or a stated conservative assumption; reference panel upwind, ≈ ≥ 2 m lateral, verified with water-sensitive paper.
- Framing: utility plants generally don't water-cool by day (thermal-shock, salts, cost; often night cleaning) — common practice, cite a source before the report. Target segments: (1) medium systems (hotels, hospitals, commercial, farms) = full system; (2) utility plants = NASIM software only (inverter/met data, cost-based cleaning scheduling); excluded: single-module systems (e.g. street lights).
- Terms: digital twin = calibrated physics model running on the ESP32; physical twin = the second reference panel.
- Future (post-paper) SCADA simulation: 120 modules, 6 strings × 20, one spray zone/valve per string, NASIM schedules zones; shows all six cases at once. Corrections: 120 × 100 W ≈ 12 kW (≈ 50 kW needs ≈ 400 W commercial modules, scaled from our curve, stated as an assumption); string length is set by inverter max voltage and cold-day Voc, not a fixed standard of 20.

**Team proposal for the 2026-11-09 supervisor meeting — "diagnose before spraying" + reference panel `[DEFERRED — after the paper, per the 2026-11-10 outcome]` (2026-11-06):** kept for the expansion phase; nothing bought now.
- NASIM becomes a multi-cause classifier from I-V sweeps + temperatures + irradiance: thermal spike → cooling burst (only if net gain > pump + water cost); soiling (Isc/G drops, curve shape normal) → one cleaning spray; partial shading (bypass-diode step) → no spray, alert; series-resistance fault (FF drop) → no spray, maintenance alert; cloud → nothing. Fits the title's "Sudden Degradation Detection"; events are produced on demand (windbreak, uniform shade mesh, cardboard on a cell, small series power resistor) → solves rare-spike risk.
- Second identical 100 W module (126.1 SAR) as an untouched reference → cooling gain measured as ΔP between panels; 3–5 day matching run; both panels wiped every morning (front spray cleans); swap roles mid-campaign. Needs a 2nd PWM load + 3rd INA226 (PaHub ch 2).
- Final comparison: no action / thermostat / NASIM on alternating days vs the reference — net Wh, pump Wh, litres, L/kWh, wrong-spray count, per-class F1 and delay.
- Extra cost `[EST]` ≈ 280–700 SAR for the team (optional MLX90640 thermal camera included at the top end). Soiling & fault detection moves from upgrade to core; spike prediction dropped.
- Novelty: I-V-based fault diagnosis exists in the literature; claim only the coupling with a cost-aware on-device spray decision, "to the authors' knowledge".

**Five-question filter for any new idea `[REC]`:**
1. Does the research question stay the same?
2. Does the measurement system stay the same?
3. Can it be done without reopening Phase 0?
4. Does it fit the time left before submission?
5. Does it sharpen the contribution, or dilute it?

Record each evaluated idea as `Decision: … (date, decided by …)`.

---

## 9. Consolidated open items

**Resolved by the supervisor on 2026-11-10:** returning-irradiance rises count as spikes; anemometer deferred; pump and cooling deferred until after the paper; he lends a data logger (model to confirm). Items on interpretation, anemometer approval and pump type are closed for this phase.

**Pending — supervisor**
1. Paper: exact topic, target venue and deadline
2. Data logger model and specs (channels, input types, logging rate) — it is the reference instrument for the validation day
3. After the paper is agreed: the title wording suggestion (§1)

**Pending — team (paper phase)**
- Confirm the 0.75 Ω load (already set in firmware config, R_LOAD = 0.75) and buy the load parts sized for ~29 A pulses
- Stand: send drawings PS-01..03 to the workshop after measuring the module frame holes and the PYR20 base
- Finish the DAQ: solder BME280, PYR20 in sun + Modbus, DS18B20 water-bath calibration
- Run the BRINGUP "Before logging" checklist (set_rtc.py to UTC, marker test), then install outdoors and log
- Literature: read the full texts of the closest works for the paper (§1.6)

**EWA — optional, not on the critical path `[DECIDED]`**
- Gave approval in principle, then stopped responding (as of 2026-09-29). The team proceeds independently and keeps the channel open for specific steps (e.g. PYR20 calibration comparison, O&M session on soiling, facility visit).

**Open — technical**
- Spike definition (threshold + window) and detection window length (§3, §6)
- How often spikes occur in Makkah; controlled spike tests from the first week (§3)
- Irradiance-normalization method and the thermal/non-thermal separation logic (§5.1)
- Thermal model form for path 3M (Mechanical team) and how it is calibrated on Phase 1 data
- Load parts and settings (§4 `[OPEN]` list)
- ESP32 variant; Raspberry Pi role (§6)
- Uncertainty budget — before data collection
- Written cleaning protocol
- PYR20 plane-of-array mounting; deep study of Modbus RTU (frame structure, function codes, CRC) before implementing it
- Clear-sky model identity (§7); panel tilt and azimuth measured at installation (§7)
- Phase 3: nozzle and pump selection on measured draw, flow and pressure; spray burst length; washing-effect test; water quality (distilled or RO)

---

## 10. What this file supersedes

- **This file's version of 2026-09-29:** the old title; `P_net` as the core research question (now the decision rule and metric, §1.4); the phase plan with the four-strategy comparison and the predictive net-energy optimizer (now §3); the §8 direction shortlist as a proposal to the supervisor (now extensions); "Scope `[PENDING]`: cooling under control and AI" (resolved by §1.1).
- **`claude_grad-project-overview.md` — entirely** (as before).
- **`literature-research-and-design-decisions.md` — framings and status items only** (literature findings stay valid):
  - §7 research-question reframing (operating envelope) → replaced by §1.3; its "every outcome is reportable" logic is kept
  - §1.3 novelty wording (four-strategy comparison on measured net energy) → replaced by §1.6
  - §3.2 panel-to-panel mismatch and §9 second-panel action item → not applicable with one panel
  - §5 item 4 "Retain INA226 + MPPT controller — Confirmed" → INA226 retained; MPPT is not a measurement load (§4)
  - §6 "Phase 3" label → now Phase 2 (detector) and Phase 3P/3M (control)
- The late-September plan to buy a second panel (§2).
- The earlier recommendation of a **fixed resistive load at Vmp (~10.8 Ω)**, and the **ESP32-controlled linear MOSFET load** as the main load (acceptable only on the 30 W bench) (§4).
- The **30 W module decision** of 2026-09-26 → borrowed 100 W, near-final (2026-09-29) → **buy a module** (supervisor, 2026-10-11).
- The **back-surface cooling channel** as the preferred physical method (literature doc §3.1) → **front-surface spray** (2026-10-11); the soiling confound is handled by the spray conditions in §2.
- The **≈ 1–2 W continuous pump budget** as the pump-selection rule → burst-based net energy for a spray pump (§2).
- **Any phase numbering** other than §3.

---

## 11. Working documents

- **This file** — status authority.
- **Literature doc** — findings still valid: §1.1 prior art, §2 effect size, §3.1 soiling, §3.3 wind, §4 cold-water budget (as an extension), §5 BOM additions, §6 ML guidance.
- **Team explainer page** (Arabic, interactive, 14 chapters): https://claude.ai/artifact/H2WhpioJVv6wuYGeHgnJP6 — **updated 2026-09-30 (version 13) to match this file:** new title and direction, spike decision balance, threshold-vs-AI detection demo, single-panel loss signal with the in-plane clear-sky index, the §1.2 interpretation table, and the phase plan Gate A → 0 → 1 → 2 → Gate B → 3P/3M → 4. **Version 14 (same day)** adds the targeted-search results, the spike physics and the parts review. Versions 15–18 (Sep 30 – Nov 3) track the decisions above. **Version 19 (2026-11-03)** adds chapter 1 «المنظومة وهي تشتغل»: a three.js model of the planned rig (100 W module at 21°, perforated pipe or spray nozzles, gutter, tank and pump, controller box with detector and pump lamps, irradiance sensor at panel tilt, five back-sheet sensors, anemometer, air sensor in a shield, PWM resistor load on a shaded heatsink; cables grey and water pipes in the water colour, as in the system chapter) with hotspots giving each part's role and its toolkit status, and an illustrative simulation: Faiman thermal model with an uncooled twin, spike button (wind −1 m/s for 20 min), detector (rise ≥ 2.5 °C within 10 min, irradiance gate 5 %, 10-min silence, own-cooling compensation), the net-power burst rule and the 45/35 °C fixed threshold running in parallel on the same weather, a film/spray toggle, energy HUD, 60-min chart and counters. Every number comes from the page or this file except one labelled assumption (ambient and water 30 °C).
- **Supervisor deck "قراران قبل الشراء"**: https://claude.ai/artifact/RVVFqQoC4WcQVHVzjDYvxH — the load decision (option C, adopted by the team on 2026-10-01) is still current; its direction shortlist is superseded by §1.1 and §8.
- **Arabic PV Design course guide** (`pv-design-ar.html`, 2026-09-30) — team learning resource on the EWA course; its project-link section follows this file's direction.

---

## 12. Working rules for Claude (Project chats and Claude Code)

- Never invent datasheet values, standard contents, or research results; mark unverified numbers as such (`[EST]`).
- Source priority: manufacturer datasheets, IEC 61724-1:2021, JCGM 100:2008 (GUM), Faiman 2008 / Duffie & Beckman for cell temperature. Training manuals are personal-learning material, not citable.
- **Treat §1.2 as the team's interpretation until the supervisor confirms it**; do not present it as his words.
- In new documents, describe the project by the new title and research question (§1.3), not as "net-energy optimization"; use "degradation" only in the §1.2 sense and say so.
- State the §1.6 contribution only with the "to the authors' knowledge" wording and cite the closest works; the first-pass search is done (2026-09-30), full texts still to be read.
- Scope-change test: the five questions in §8.
- Tag statements as DECIDED / SUPERVISOR / PENDING / OPEN / REC / LIT / EST.
- New decisions are recorded as: `Decision: … (date, decided by …)`.
- Hackathons and other side projects are tracked outside this file and are not part of the graduation project.

---

## Sync instructions

1. **Project knowledge:** replace `claude/PROJECT_STATE.md` with this file; keep the literature doc; `claude_grad-project-overview.md` stays deleted.
2. **Claude Code:** in the project `CLAUDE.md`, replace the old objective, phases and open-decisions sections with §1 (title, §1.1–1.4), §3, §4 (status line and options table), §5.1, §6, §9 and §12 of this file.
3. **Team explainer page:** update to match this file (see §11).
4. On every new decision: update this file first, then the explainer page, then re-sync `CLAUDE.md`.
