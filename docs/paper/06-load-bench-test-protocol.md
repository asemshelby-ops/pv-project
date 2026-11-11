# PWM resistor load — bench-test protocol

Status: protocol, 2026-11-11, written before the parts arrive. The load is option C of
PROJECT_STATE §4 with `R_LOAD = 0.75 Ω` (team decision 2026-11-10; supervisor approved the
load in principle). Order of work: this protocol on a lab DC supply, then the 100 W
module. The firmware it tests is specified in `07-load-firmware-spec.md`.

All electrical numbers below come from the seller listing of the module (V_mp 18 V,
I_mp 5.55 A, V_oc 21.5 V, I_sc 5.88 A) and ideal switching. They are `[EST]` and are
re-measured in the steps named.

## 1. Stresses the parts must survive `[EST]`

The module sees `R_eff = R / D`. With R = 0.75 Ω:

| Quantity | Formula | At MPP (STC listing) | Worst case |
|---|---|---|---|
| Duty at MPP | `D = R · I_mp / V_mp` | 0.23 | ≈ 0.26 on a hot noon (lower V_mp) |
| Current pulse in MOSFET and resistors | `V / R` | 24 A | 28.7 A at sweep start (V = V_oc) |
| MOSFET RMS current | `(V/R) · √D` | 11.5 A | — |
| **Capacitor ripple current, RMS** | `(V/R) · √(D(1 − D))` | **10.1 A** | ≈ 7–10 A all day |
| Resistor pulse power | `V² / R` | 432 W during the on-time | 616 W at sweep start |
| Resistor mean power | = module power | ≤ 100 W | — |
| Ripple voltage, capacitive part, C = 2000 µF | `I_mp (1 − D) / (f C)` | 0.11 V p-p | — |
| Ripple voltage, ESR part | `(V/R) · ESR` | 0.24 V p-p for 10 mΩ total | — |
| Lowest sweep voltage | `I_sc · R` | 4.4 V | + wiring and MOSFET drop |

**New finding to settle before buying `[REC]`.** PROJECT_STATE §4 sized the capacitor
for ≈ 6 A RMS; that figure was for 1.5 Ω. Halving the resistance to 0.75 Ω raises the
continuous ripple current to ≈ 10 A RMS. Two ways to handle it:
- (a) keep one 0.75 Ω bank and buy a capacitor bank rated for ≥ 10 A RMS ripple at
  20 kHz and at Makkah enclosure temperatures, with margin (several low-ESR parts in
  parallel; take the ripple rating from each part's datasheet, not from capacitance);
- (b) two 1.5 Ω branches, each with its own MOSFET. Branch A alone holds the MPP
  (D ≈ 0.46, pulses 12 A, capacitor ripple ≈ 6 A RMS). Branch B is switched in only
  during sweeps, giving 0.75 Ω and the 4.4 V sweep floor for 1–2 s a minute. It costs one
  more MOSFET and driver channel and one GPIO.

Both meet the measurement need. (a) is fewer parts; (b) halves the continuous stress.
The team chooses; the firmware spec supports both with one compile-time switch.

## 2. Equipment

Lab DC supply, ≥ 25 V and ≥ 6 A, adjustable current limit · two multimeters (one as
ammeter; note model and accuracy) · oscilloscope if the lab has one · thermocouple meter
or IR thermometer · the ESP32 DAQ with the panel INA226 unit · inline fuse 10 A in the
supply lead · safety glasses.

Safety: the resistors and heatsink get hot enough to burn. Capacitors can vent if
reversed: check polarity twice. Switch the supply off before touching the power wiring.
Never leave the load running unattended on the bench.

## 3. Steps

Each step has a pass criterion. A failed step is fixed before the next one starts.
Criteria marked `[ASSUMPTION]` are proposals.

| Step | What | How | Pass |
|---|---|---|---|
| **B0** Parts check | resistors, capacitors, MOSFET, driver, diode, TVS | Measure each resistor and the assembled bank (4-wire, or 2-wire with the lead resistance subtracted). Read the MOSFET, capacitor and resistor datasheets: pulse current, ripple rating, derating curve. | Bank within ±5 % of 0.75 Ω; MOSFET pulse rating ≥ 40 A; capacitor ripple rating ≥ the §1 value with margin; resistor power rating at 50 °C ambient ≥ 100 W from its derating curve |
| **B1** Gate drive, no power | PWM and reset behaviour | Scope (or multimeter on DC) at the gate with the power stage unpowered: duty 0, 25, 50, 100 %. Then hold the ESP32 in reset and re-flash it. | 20 kHz; gate stays LOW through reset and flashing (pull-down works); duty 0 at boot |
| **B2** Low power, meter check | INA226 against the multimeters | Supply 12 V, current limit 2 A. Step the duty so the current is ≈ 0.5, 1, 1.5, 2 A. At each: INA226 V and I, multimeter V at the supply terminals **and** at the unit, multimeter I. | I gain factor (INA / meter) constant within ±0.3 % over the four points → record it as the shunt calibration. V at the unit within ±0.3 % of the meter there. Cable resistance = (V_supply − V_unit) / I recorded. Also record which terminal the unit senses V_BUS on (open TODO in BRINGUP) |
| **B3** Supply as a crude module | R_eff = R / D | Supply 21 V, limit 5.9 A (short tests) or 4.7 A (long tests, ≈ 100 W). Sweep D from 0 to 1 slowly by hand command. Below the limit the supply holds 21 V and `I = 21·D/0.75`; above it the current holds and `V = I·0.75/D`. | Measured (V, I) within ±5 % of these formulas over the whole range; no oscillation at the knee (if the supply hunts between its two modes, note the duty range and avoid it) |
| **B4** Settling time | wait needed after a duty step | Step D between two values in the current-limited region; log V with the fastest INA226 setting (140 µs conversion, no averaging) for 50 ms. Repeat near V_oc and near the lowest voltage. | Time to stay within 0.2 % of the final value → `t_settle`; the firmware uses 1.5 × the worst case. Compare with the 15–20 ms estimate of PROJECT_STATE §9 |
| **B5** Heat run | thermal design | 30 min at ≈ 100 W (21 V, 4.7 A limit, duty in the current-limited region). Every 5 min: temperature of resistor body, heatsink, MOSFET, capacitors, diode. | All below their datasheet limits with ≥ 20 °C margin at bench ambient (outdoors will be ≈ 15–20 °C hotter `[ASSUMPTION]`); bank resistance drift recorded; then the over-temperature trip test: warm the heatsink sensor → duty goes to 0 and the trip is logged |
| **B6** Ripple and interference | measurement quality | With the load at the MPP-like point: scope the voltage at the INA226 input (ripple p-p). Then run the full logger for 10 min with PWM off and 10 min with PWM on. | INA226 readings with the chosen averaging: standard deviation ≤ 0.05 % of V and of I `[ASSUMPTION]`. Modbus CRC errors, DS18B20 bad readings and I2C errors: no more with PWM on than with PWM off |
| **B7** Faults | safe failure | (1) Reset the ESP32 while the load runs. (2) Unplug the INA226 cable while holding. (3) Pull the supply connector under load. (4) Check the reverse-polarity protection with the supply current-limited to 0.2 A. | (1) load turns off, restarts cleanly. (2) duty freezes, error logged, no runaway. (3) nothing fails; scope shows the turn-off spike below the MOSFET and TVS ratings. (4) no current flows, nothing heats |
| **B8** First sun | the real module | Clear midday. Connect the module. Ten sweeps back to back, then 10 min of hold with a sweep every 60 s. Multimeter V_oc at the module connectors. | V_oc (INA, cable-corrected) within ±0.3 % of the meter. P_mp of the ten sweeps: standard deviation ≤ 0.3 % `[ASSUMPTION]` at steady G. Sweep reaches below 0.5 · V_oc. Hold voltage within ±1 % of the last sweep's V_mp. P_mp plausible against the listing scaled by G (a sanity check only: the listing has no temperature data) |
| **B9** Reference check | independent I-V | If an electronic load or I-V tracer can be borrowed, or with the data logger on the validation day: same module, same minutes. | P_mp within ±2 % `[ASSUMPTION]`; differences explained |

## 4. Records to keep

For every step: date, who, supply and meter models, the raw readings (a CSV or a photo of
the notebook page), pass or fail, and what was changed after a fail. The numbers that
feed other documents:

| Number | From | Goes to |
|---|---|---|
| Shunt calibration factor and its spread | B2 | uncertainty budget §5 |
| Cable resistance, V_BUS sense terminal | B2 | uncertainty budget §5; analysis correction |
| `t_settle` | B4 | firmware parameter |
| Heatsink trip temperature | B0 (derating curve), B5 | firmware parameter |
| INA226 averaging setting | B6 | firmware parameter |
| Sweep repeatability of P_mp | B8 | γ method §4 |

## 5. What the lab supply does not test

A supply with a current limit is a rectangle, not an I-V curve: it has no rounded knee
and no real maximum-power point. Steps B3–B7 prove the switching, the readings and the
firmware logic. MPP accuracy and sweep quality are proven only on the module (B8, B9).
