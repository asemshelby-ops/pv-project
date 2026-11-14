# PV Thermal-Spike Cooling Control — Project Context

Graduation project, Mechatronics, Umm Al-Qura University (Makkah).
Title (sent by the supervisor, 2026-09-30):
  AI-Driven Smart Cooling for PV Panels Based on Real-Time Thermal Spike and
  Sudden Degradation Detection
Team: 5 — 3 Mechatronics (Asim, lead) + 2 Mechanical. Most of the software work falls
on Asim; keep tasks for teammates small, dated and with one defined output.
Supervisor: د. عبدالإله الحمياني. Lab access and purchase review: د. عبدالمجيد.

Last synced: 2026-11-10. Full status, reasons and sources: ./PROJECT_STATE.md

## CURRENT PHASE — 2026-11-10 (overrides anything below that conflicts)
- Priority: one published research paper, narrow and simple: characterize thermal
  spikes on our module in Makkah's hot-dry climate (frequency, size, cause, power
  cost via gamma measured from I-V sweeps) and detect them in real time on the ESP32
  (NASIM vs threshold vs CUSUM). Expansion (cooling, pump, diagnose-before-spray,
  reference panel, SCADA) only after the paper.
- Deferred: cooling, pump (ENABLE_PUMP 0), anemometer (HAS_ANEMOMETER 0, wind = NaN).
- Spike definition (supervisor): the rise when the sun returns after a cloud COUNTS.
  Do not require stable irradiance; label the cause (irradiance step vs. other).
- Load (decision 2026-11-11): two 1.5 ohm branches, each with its own MOSFET. Branch A
  alone holds the MPP (D ~0.46); branch B is added only during I-V sweeps (0.75 ohm
  total) so the sweep reaches ~4.4 V and Isc. Pulse ~14 A per MOSFET [EST] -> each
  MOSFET >= 30 A pulse, low R_DS(on); capacitor ripple ~6 A RMS continuous.
- Sweep settling (found 2026-11-13, simulation with 2000 uF [EST], NOT measured): the
  time constant of a duty step is tau = C / (D/R + |dI/dV|), about C*V/I below the knee,
  so it grows as the module current falls: worst tau ~3.9 ms at 1000 W/m2 (5*tau ~20 ms),
  ~10 ms at 400 (5*tau ~50 ms), ~18 ms at 200 (5*tau ~90 ms). A fixed 15-20 ms is 5*tau
  only near full sun. T_SETTLE_MS = 20 is therefore only the SHORTEST wait: the firmware
  stores a sweep point only when two INA226 readings in a row (1.1 ms conversion, x4 avg)
  agree within 0.2 % of V_oc (SWEEP_SETTLE_TOL, the stability check in
  firmware/libraries/PvDaq/src/pv_load_logic.c), and flags a sweep that could not settle
  in its slot as NOT_SETTLED (32). Details: docs/paper/07 section 12; real tau: bench B4.
- PWM: 20 kHz, 11 bit (20 kHz x 2^11 = 40.96 MHz <= 80 MHz APB; 12 bit would not fit).
- Timestamps: RTC and logs in UTC; NASIM adds +3 h internally. Run set_rtc.py first.
- The supervisor lends a commercial data logger: reference for validating the ESP32
  DAQ (temps, V, I, PYR20 analog); align with /markers.csv button presses.
- Stand: workshop-built mild-steel stand (drawings PS-01..03), tilt 20 deg, facing
  true south; PYR20 on a bracket coplanar with the module.
(copy of the claude.ai Project file; if they differ, the Project copy wins). The Excel
BOM is authoritative for part numbers, quantities and prices. Team explainer page
(Arabic, interactive, includes a 3D simulation of the rig):
https://claude.ai/artifact/H2WhpioJVv6wuYGeHgnJP6

## Supervisor direction

2026-09-30 (voice note):
- The goal is CONTROL: "can we control using AI" — based on temperature degradation.
- One panel is enough: temperature, power and irradiance data -> estimate the
  degradation -> control.
- Control idea: when panel temperature jumps about 3-4 degC within a short period
  instead of rising gradually, pump based on that jump, "optimization-like".
- The model runs ON the ESP32 (TinyML); collect data first.
2026-10-11 (meeting): buy a module; cooling is PHYSICAL, water on the front surface.
2026-11-03 (voice notes):
- No battery and no MPPT/charge controller: connect the module to a DC load matched to
  its power, cheap, and it must not heat or cool the module. = load option C below.
- Check how the ESP32 talks to the INA226 units over I2C ("an I2C converter is needed").
- Pump: the fixed-speed misting pump shown to him is rejected; he wants variable speed
  from a cheap pump via an easy method (= PWM on a brushed DC pump, see Cooling).
- Three current tasks: (1) build the models — train now on synthetic data, retrain on
  real data later; (2) deploy them on the controller; (3) water delivery and flow
  variation at the lowest cost.

## Working interpretation — PENDING the supervisor's confirmation

Treat this as the team's interpretation, never as the supervisor's words.
- Degradation = reversible, temperature-induced power loss (gone when the module cools).
- Thermal spike = a rise of about 3-4 degC within minutes; threshold and window are
  tuned from our own data (Phase 1).
- AI role = detect the spike in real time on the ESP32; the net-power rule decides
  whether and how long to pump; benchmarked against simple rules.
- Success = detection quality vs the baselines (precision/recall, detection delay,
  false alarms per day), temperature response after a trigger, and net energy.
Net power P_net = P_PV - P_pump is NOT the research question; it is the controller's
decision rule and a key metric.

## Key physics [EST] (illustrative gamma ~ -0.4 %/degC, NOT this module's value)

- A 3-4 degC spike costs only ~1.2-1.6 % of power (~1.3 W on the 100 W module at ~90 W),
  about the size of irradiance-driven swings. The temperature-induced loss CANNOT be
  read from raw power; compute it from irradiance-normalized, MPP-true power.
- Recovered power ~0.36 W per degC of cooling actually achieved (90 W, -0.4 %/degC).
- Thermal time constant ~6.3 +/- 1 min for fixed-mount PV (Herteleer et al., Solar
  Energy 2023) -> 5-10 min detection window, >= 30 samples per event at 10 s.
  Normal heating ~0.1 degC/min (Moharram et al. 2013).
- Faiman model, pvlib defaults u0 = 25, u1 = 6.84 W/(m2 K): T = Ta + G / (u0 + u1*v).
  Wind 2 -> 1 m/s at 900 W/m2: ~5 degC steady rise (~3 degC in 6 min, ~4 in 10 min).
- Heat capacity ~ tau*U*A ~ 8.7 kJ/K (A ~ 0.65 m2). Spray at 2 L/min with the water
  warming 5 degC (no evaporation) removes ~0.70 kW: ~2 L/min cools 10 degC in ~2 min.
- Pump break-even [EST, 2026-11-03]: energy recovered per second of pumping
  ~ k*q/(A*U) ~ 10 J at 2 m/s wind and ~12 J at 1 m/s (k = 0.36 W/degC, q = 0.70 kW),
  independent of heat capacity and burst length. Under the no-evaporation assumption a
  pump drawing more than ~10 W cannot pay back; spray pumps are typically ~17-33 W
  electrical at 2 L/min and 2 bar. The energy case therefore rests on measured spray
  cooling WITH evaporation and on running the pump at the lowest useful PWM duty.
- In the team simulation the 45/35 degC fixed threshold with a small pump nets far more
  energy than spike-triggered control (it cools most of the time). Frame the thesis as
  real-time detection + event-triggered control + operating envelope, not "saves more
  energy than a fixed threshold".
- After each burst the module re-heats by about the amount cooled: the detector must not
  count its own re-heating as a spike (subtract the controller's own cooling effect with
  the thermal model, or blank the detector after bursts).

## Hardware

PV module (BOUGHT, 2026-11): BANINUAN 100 W monocrystalline, 12 V class. Seller listing
(not a full datasheet): Pmax 100 W, Vmp 18 V, Imp 5.55 A, Voc 21.5 V, Isc 5.88 A,
1200 x 540 x 30 mm, 4 kg, max series fuse 10 A. Read the module label to confirm.
NO 30 W module exists (correction 2026-11-04; older notes saying one was bought were
wrong). Tilt ~21 deg (near Makkah latitude), facing the sun.
CRITICAL: temperature coefficients, NOCT and cell count are UNKNOWN. gamma is measured
from I-V sweeps. Never invent datasheet values; if a calculation needs them, say so.

Bought or ordered (orders 18-20 Oct, ~643 SAR):
- M5Stack Unit INA226-10A x2 (panel + pump channels): 5 mOhm shunt, +/-10 A, bus
  0-30 V, isolated, 5 V on the HY2.0-4P port, FIXED I2C address 0x41 -> both units go
  through the M5Stack PaHub V2.1 (PCA9548AP, 6 channels, default address 0x70), one unit
  per channel. Measure the idle SDA/SCL voltage before wiring to the ESP32; add a
  3.3 V <-> 5 V level shifter if it is near 5 V (likely the supervisor's "I2C
  converter"). Put the panel unit between the module and the load capacitor (current
  <= Isc); after the capacitor the PWM pulses reach ~14 A, above the unit's range.
- DS18B20 waterproof x10 (likely clones): read each ROM (authentic pattern
  28-xx-xx-xx-xx-00-00-xx) and cross-calibrate all in one water bath before mounting.
  Five on the back sheet behind cell centres (thermal paste, aluminium tape, insulating
  cover); spares for water/tank/heatsink. The pull-up ordered was 4.7 OHM by mistake —
  buy 4.7 kOhm locally.
- DS3231 + AT24C32 RTC (this board charges its cell -> use an LIR2032); sync to NTP.
- PYR20 pyranometer, dual output (RS485 Modbus RTU + 0-2 V), mounted IN THE PLANE of the
  panel, out of the spray. 400-1100 nm silicon sensor, accuracy 5 %, resolution
  1 W/m2. Modbus defaults per datasheet: address 1, 9600 8N1, register 0x0000 =
  irradiance (UINT16); verify the scaling on a clear day. Use changes within a window.
- MAX3485 RS485 module; 5 V 3 A buck; shielded 3-core and silicone cable; blade fuses.
- Listed as bought but not seen in the order screenshots — confirm: BME280, microSD
  module, terminal blocks.
Still missing: 12 V mains adapter (size it for pump + electronics, ~5 A [EST]), ESP32
(if none), load parts (resistors, capacitors, MOSFET + gate driver, heatsink),
enclosure, radiation shield for the BME280, 4.7 kOhm pull-up, pump MOSFET driver,
nozzles, pump (+ one spare), tubing, gutter, tank, inline filter.
Anemometer: pending supervisor approval, priority raised (wind lulls = likely spike
driver). Starting threshold well below 1 m/s, 0.1 m/s resolution, beside the module at
module height.
Controller: ESP32 (Arduino framework). Raspberry Pi: optional (logging/dashboard).

## Cooling — front-surface SPRAY (Decision 2026-11-04, Asim)

- Spray adopted over the water film. Nozzles on a pipe along the top edge, a gutter at
  the bottom edge back to a small tank (closed loop), distilled or RO water.
- Selection order: nozzles first (flow, pressure, coverage of 1200 x 540 mm glass; first
  estimate 0.7-2 L/min total), then the smallest pump that delivers that flow at that
  pressure.
- Pump [REC]: 12 V DC brushed diaphragm pump, self-priming, NO internal pressure switch
  (or bypassed), not brushless with an integrated driver. Typical source: electric
  knapsack-sprayer pumps. Buy two (one spare).
- Speed control [REC]: ESP32 PWM -> logic-level N-MOSFET (fully on at 3.3 V gate), ~100 ohm
  gate resistor, ~10 kOhm pull-down, flyback diode across the pump, fuse on the pump
  line, PWM above audible range. Not a potentiometer "motor speed controller" module.
- First tests: pump curve in a bucket (flow by volume/time, current, pressure with the
  real nozzles at 30-100 % duty, minimum running duty); then cooling per second of
  spraying on the 100 W module.
- Conditions: clean the module every morning and log it (washing raises power — separate
  washing from cooling); PYR20 out of the spray; spray in SHORT BURSTS; the controller
  decides when and how long.
- Closed loop, water temperature (2026-11-06): the cooling power depends on T_module - T_water, not T_module - Ta. Put a spare DS18B20 in the tank (T_water column). The decision rule must use measured T_water (dTw = min(5, T_module - T_water)), so it refuses to pump when the water is too warm. [EST] one burst for a 3 degC spike removes ~26 kJ; a 20 L tank needs ~84 kJ per degC, so ~0.3 degC per burst - the bigger risk is sun on the tank, gutter and pipes: tank in the shade under the module, white or insulated, covered; 20-30 L. Log the water level or weigh the tank daily (evaporation + drift loss = water-efficiency metric).
- The supervisor will buy a data logger: a reference instrument, not a replacement for
  the ESP32 DAQ.

## PV measurement load — option C ADOPTED (2026-10-01, Asim; matches the supervisor's 3 Nov request)

PWM-switched resistors + input capacitor, ESP32 control. Module -> shunt -> capacitor ->
resistor bank switched by a MOSFET at ~20 kHz; the module sees R_eff ~ R / D. Hold the
Vmp of the latest sweep; sweep every 1-2 min (D 0 -> 1 over 1-2 s, ~20 ms per step);
flag sweep samples and exclude them from energy totals.
Sizing [EST] for the 100 W module: R_mp ~3.24 ohm; bank ~1.5 ohm -> D ~0.46 at STC;
sweep-start current ~Voc/1.5 ~14.3 A. C >= 2000 uF low-ESR >= 35 V (several in
parallel); MOSFET >= 60 V, >= 15 A pulses; gate driver with 3.3 V-logic input fed from
the DAQ supply, not the PV line. Resistors on a heatsink in the shade, downwind of the
module and the air sensor. Protection: freewheel diode or non-inductive resistors, TVS,
tight loop, reverse polarity, heatsink over-temperature trip (D = 0).
Bench plan: test first on a current-limited lab DC supply (~21 V, limit ~5.9 A) — good
for switching, INA226 readings and sweep logic, not MPP accuracy — then the 100 W module.

## Detection signal

1. Measure gamma from I-V sweeps (verify the IEC procedure before citing it).
2. P_25 = P_mp,meas / [1 + gamma * (T_cell - 25)];  dP_T = P_25 - P_mp,meas.
3. A spike = a rapid rise of T_cell and dP_T while irradiance-normalized P_25 stays
   steady. A power drop or temperature rise NOT explained by a wind lull (irradiance
   step, shading, soiling, fault, own cooling) must NOT trigger the pump.
Clear-sky index: divide in-plane irradiance by clear-sky irradiance TRANSPOSED to the
panel's tilt and azimuth, never by horizontal GHI.

## TinyML on the ESP32

- Role: real-time spike detection on each 10 s sample; the net-power rule sits on top.
- Baselines the AI must beat: (a) temperature-rise threshold rule with a 3-sample median
  filter and an irradiance gate (|dG/G| < 5 % over the window); (b) CUSUM on the
  residual between measured module temperature and a weather-driven thermal model
  (Faiman + first-order lag).
- Model: XGBoost / LightGBM; no DL/LSTM. Export to C with m2cgen (pin versions; set
  base_score explicitly in XGBoost) or emlearn. Run the held-out set through Python and
  the ESP32 and compare outputs.
- Event metrics: precision, recall/F1, detection delay, false alarms per day; class
  imbalance; chronological split by whole days, never random samples.
- Features, all computable on the ESP32: temperature rise over several windows, dG/dt,
  clear-sky index, dP_T, wind (if approved), solar geometry. Feature parity is mandatory.
- Size budget: limit n_estimators x max_depth; measure on the board.

Simulation parameters used on the team page (illustrative; keep synthetic data
consistent with them): A = 0.648 m2, C = 8.7 kJ/K, Faiman u0 = 25, u1 = 6.84; detector:
rise >= 2.5 degC within 10 min, irradiance gate 5 %, 10-min silence after an alarm;
decision: expected gain = 0.004 * 100 W * (G/1000) * rise * 378 s vs pump energy over the
burst (burst length = C * rise / q); fixed strategy on at 45, off at 35 degC; spike
scenario = wind -1 m/s for 20 min; ambient = water = 30 degC (labelled assumption).

## Sensor buses

I2C (via PaHub) - INA226 x2 (0x41 each, separate channels), BME280, RTC
1-Wire          - DS18B20 T1..T5 on the back sheet (+ water/tank)
RS485           - PYR20, Modbus RTU (study frames, function codes and CRC before coding)
SPI             - SD card
PWM             - measurement load and pump

## Data format

One CSV row every 10 s. Base schema:
date, time, T1, T2, T3, T4, T5, T_ambient, humidity, irradiance, voltage, current, power
Proposed additions (confirm before coding): pressure, wind, D, sweep_flag, test_marker
(a button marking controlled spike tests), pump_V, pump_I, pump_P, pump_duty, flow,
T_water. I-V sweep points go to a separate file (timestamp, D, V, I). Log raw values.
Synthetic data must use the same schema so the same code retrains on real data.

## Phases — do not skip ahead

Gate A (NOW): module bought (done); supervisor confirms the interpretation (and whether
  rises after returning irradiance count as spikes); inform him of load C; anemometer
  approval; reference instrument.
Phase 0 (next): DAQ build and validation + PWM load (lab DC supply, then 100 W) +
  uncertainty budget + cleaning protocol + timestamp check.
Phase 1: bare-panel baseline + spike characterization; controlled spike tests from the
  first week (~20 % shade mesh over module AND PYR20 for ~20 min; windbreak ~20 min;
  negative controls; never an opaque cover). Target >= 50 labelled events for training
  and >= 20 held out; irradiance-driven rises are a separate class.
Phase 2: AI spike detector — baselines -> gradient-boosted trees; deployed and validated
  on the ESP32. (Supervisor tasks 1-2 start NOW on synthetic data.)
Gate B: RESOLVED — physical; spray adopted 2026-11-04.
Phase 3P: build the spray loop, measure the pump curve and cooling per burst, run the
  control strategies on the real panel.
Phase 4: evaluation and thesis.
Strategies compared: no control / temperature-rise rule / AI with the net-power rule /
fixed threshold 45-35 degC (Moharram et al. 2013; also report the best setpoint).

## Digital twin — on main since 2026-11-14, built on the retired branch twin-sim (post-paper work, SIMULATION only)

What exists (2026-11-13/14): `twin/` = single-diode model of our module (De Soto, fitted to
the seller listing) + lumped Faiman thermal model with spray, evaporation, pump and tank;
120 copies form a simulated rooftop (6 strings x 20, a spray zone and valve per string,
shared pump and tank); five controllers (none, continuous, fixed 45/35, NASIM spike-
triggered, predictive); fit + validation hooks for real logger files; the SCADA rooftop
page as a SIMULATION source. All 34 physical parameters are [LIT-PLACEHOLDER] in ONE file,
`twin/params.py`; measured values enter via `twin/params_measured.json` (written by
`twin.fit --real`, tag [MEASURED]) and `twin/data/pump_curve_R385_measured.csv` — no code
edits. Docs: docs/twin/README.md (run commands), simulation-results.md, ranking.md,
refs.md (references checked 2026-11-14), WHAT-DECIDES-AR.md (team note).
Run: `python -m twin.validate --demo`; `python -m twin.compare` (needs the NASIM model
from `python -m analysis.demo_synth`); `python -m twin.rank` (one-at-a-time ranking).
What it revealed (inside the model, placeholders; docs/twin/ranking.md): the SIGN of the
net-energy result is set mainly by two quantities nobody has measured — evaporation (up to 9.2
points of net energy; fixed 45/35 goes +0.81 % -> -8.37 % without it) and the pump's
electrical power (x3 = a typical spray pump: fixed 45/35 -> -4.4 %, predictive -> 0).
Next, ~2 points each: water flow / glass coverage of our nozzles, Faiman u0/u1, gamma.
Wind ~0.9 points on energy, but it is the likely spike driver and, without an
anemometer, the twin's temperature error stays 1.2-2.5 degC. Heat capacity and tank loss
hardly matter (< 0.05). Measuring order: (1) pump curve with the real nozzles — power AND
flow vs duty, minimum duty (bucket test, no module needed); (2) cooling per second of
spraying and evaporation on the module in sun (post-burst decay + tank weight loss; the
placeholder uses McAdams' coefficient, which includes radiation and free convection -> an
upper bound; Watmuff's form alone costs 1.4 points); (3) wind at the module (anemometer).
gamma and u0 come free with the paper's sweeps and logged days. Never quote a twin
number without "simulation"; never write "the system saves x %".

## Working rules

- Graduation project defended orally. Explain what you write; small reviewed steps.
- Do not fabricate sensor specs, datasheet values, standard contents, research results
  or measurement results. Mark estimates [EST]. Flag what needs experimental checks.
- Describe the project by the title and research question, not "net-energy
  optimization"; use "degradation" only in the working-interpretation sense.
- Novelty only "to the authors' knowledge", citing the closest works (PROJECT_STATE.md
  section 1.6).
- Scope test for new ideas: same research question? same measurement system? no
  reopening of Phase 0? fits the time? sharpens the contribution?
- Pin library versions; keep code reproducible (fixed random seeds, requirements file).
