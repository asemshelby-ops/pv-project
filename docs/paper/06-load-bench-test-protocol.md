# PWM resistor load — bench-test protocol

Status: protocol, 2026-11-11, written before the parts arrive. The load is option C of
PROJECT_STATE §4 in its **two-branch form** (decision by Asim, 2026-11-11): two identical
1.5 Ω resistor branches, each with its own MOSFET. Branch A alone holds the MPP all day;
branch B is switched in only during I-V sweeps, which makes 0.75 Ω in total. Order of
work: this protocol on a lab DC supply, then the 100 W module. The firmware it tests is
specified in `07-load-firmware-spec.md`.

All electrical numbers below come from the seller listing of the module (V_mp 18 V,
I_mp 5.55 A, V_oc 21.5 V, I_sc 5.88 A) and ideal switching. They are `[EST]` and are
re-measured in the steps named.

## 1. Stresses the parts must survive `[EST]`

The module sees `R_eff = R / D`, with R = 1.5 Ω in hold (branch A) and R = 0.75 Ω in a
sweep (A and B, same duty).

| Quantity | Formula | Hold, branch A alone (all day) | Sweep, A + B (≈ 1.6 s per minute) |
|---|---|---|---|
| Duty at MPP | `D = R · I_mp / V_mp` | 0.46 (≈ 0.51 on a hot noon) | 0.23 |
| Current pulse in each MOSFET and branch | `V / 1.5 Ω` | 12 A | up to 14.3 A at sweep start (V = V_oc) |
| Current pulse drawn from the capacitor | `V / R` | 12 A | up to 28.7 A |
| MOSFET RMS current | `(V/1.5) · √D` | 8.2 A (A) | short, ≤ 8 A each |
| **Capacitor ripple current, RMS** | `(V/R) · √(D(1 − D))` | **6.0 A, continuous** | up to 10.1 A for less than 1 s |
| Resistor pulse power, per branch | `V² / 1.5 Ω` | 216 W during the on-time | 308 W at sweep start |
| Resistor mean power | share of module power | A: ≤ 100 W | B: ≈ 1.3 W averaged (≈ 5 W in γ mode, a sweep every 15 s) |
| Ripple voltage, capacitive part, C = 2000 µF | `I_mp (1 − D) / (f C)` | 0.075 V p-p | 0.11 V p-p |
| Ripple voltage, ESR part | `(V/R) · ESR` | 0.12 V p-p for 10 mΩ total | 0.24 V p-p |
| Lowest sweep voltage | `I_sc · 0.75 Ω` | — | 4.4 V + wiring and MOSFET drop |

What the two branches buy: one 0.75 Ω bank would load the capacitor with ≈ 10 A RMS all
day. With branch A alone in hold it is ≈ 6 A RMS, the value PROJECT_STATE §4 was sized
for, and each MOSFET sees pulses of about 14 A and not 29 A. The 4.4 V sweep floor is
kept. Cost: a second MOSFET, a second gate-driver channel, a second 1.5 Ω bank and one
GPIO.

Parts to buy `[REC]`:
- 2 × resistor bank, 1.5 Ω. Bank A: ≥ 100 W at Makkah ambient from the maker's derating
  curve, on the heatsink. Bank B carries little mean power; the same part as A is the
  simple choice, and any smaller part must have a pulse rating for 308 W over ≈ 2 s.
- 2 × N-MOSFET, V_DS ≥ 60 V, pulse current ≥ 30 A, low R_DS(on).
- Gate driver with two channels and 3.3 V logic inputs (or two single drivers).
- 2 × 10 kΩ resistor: one pull-down from each driver input to ground (branch A on
  GPIO33, branch B on GPIO14), fitted at the driver, so both gates stay low while the
  ESP32 boots, resets or is being flashed.
- Capacitor bank ≥ 2000 µF, ≥ 35 V, rated for ≥ 6 A RMS ripple at 20 kHz with margin at
  enclosure temperature (several low-ESR parts in parallel; take the ripple rating from
  each part's datasheet, not from the capacitance).
- Freewheel Schottky diode across each bank; TVS at each MOSFET.

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
| **B0** Parts check | resistors, capacitors, MOSFET, driver, diode, TVS | Measure each resistor and the assembled bank (4-wire, or 2-wire with the lead resistance subtracted). Read the MOSFET, capacitor and resistor datasheets: pulse current, ripple rating, derating curve. | Each bank within ±5 % of 1.5 Ω and the two within 2 % of each other; each MOSFET pulse rating ≥ 30 A; capacitor ripple rating ≥ 6 A RMS with margin; bank A power rating at 50 °C ambient ≥ 100 W from its derating curve; bank B pulse rating as in §1 |
| **B1** Gate drive, no power | PWM and reset behaviour | Scope (or multimeter on DC) at **both** gates with the power stage unpowered: duty 0, 25, 50, 100 % in hold mode (A only) and in sweep mode (A and B). Then, with the scope on the **driver input and the gate of branch B (GPIO14)**, set to single-shot trigger: power-cycle the ESP32 five times, press reset five times, and re-flash it once. Repeat on branch A. | 20 kHz; gate B stays LOW in hold mode and follows gate A in sweep mode. With the 10 kΩ pull-downs fitted: both **gates** stay LOW through every power-up, reset and flashing. GPIO14 itself may show boot-time pulses at the driver input; record their height and length. If any pulse reaches the gate, record its length (it must be a few ms at most) and fit a stronger pull-down or an RC filter on that input before going on; duty 0 at boot |
| **B2** Low power, meter check | INA226 against the multimeters | Supply 12 V, current limit 2 A. Step the duty so the current is ≈ 0.5, 1, 1.5, 2 A. At each: INA226 V and I, multimeter V at the supply terminals **and** at the unit, multimeter I. | I gain factor (INA / meter) constant within ±0.3 % over the four points → record it as the shunt calibration. V at the unit within ±0.3 % of the meter there. Cable resistance = (V_supply − V_unit) / I recorded. Also record which terminal the unit senses V_BUS on (open TODO in BRINGUP) |
| **B3** Supply as a crude module | R_eff = R / D | Supply 21 V, limit 5.9 A (short tests) or 4.7 A (long tests, ≈ 100 W). Sweep D from 0 to 1 slowly by hand command, first with branch A alone (R = 1.5 Ω), then with both (R = 0.75 Ω). Below the limit the supply holds 21 V and `I = 21·D/R`; above it the current holds and `V = I·R/D`. | Measured (V, I) within ±5 % of these formulas over the whole range, for both cases; the two branches share the current within ±5 % (clamp meter, or the voltage across each bank); no oscillation at the knee (if the supply hunts between its two modes, note the duty range and avoid it) |
| **B4** Settling time | wait needed after a duty step | Step D between two values in the current-limited region; log V with the fastest INA226 setting (140 µs conversion, no averaging) for 50 ms. Repeat near V_oc and near the lowest voltage. | Time to stay within 0.2 % of the final value → `t_settle`; the firmware uses 1.5 × the worst case. Compare with the 15–20 ms estimate of PROJECT_STATE §9 |
| **B5** Heat run | thermal design | 30 min at ≈ 100 W (21 V, 4.7 A limit, duty in the current-limited region). Branch A alone, as in hold. Every 5 min: temperature of resistor body, heatsink, MOSFET, capacitors, diode. Then 5 min with a sweep every 15 s (γ mode) and read bank B and MOSFET B. | All below their datasheet limits with ≥ 20 °C margin at bench ambient (outdoors will be ≈ 15–20 °C hotter `[ASSUMPTION]`); bank resistance drift recorded; then the over-temperature trip test: warm the heatsink sensor → duty goes to 0 and the trip is logged |
| **B6** Ripple and interference | measurement quality | With the load at the MPP-like point: scope the voltage at the INA226 input (ripple p-p). Then run the full logger for 10 min with PWM off and 10 min with PWM on. | INA226 readings with the chosen averaging: standard deviation ≤ 0.05 % of V and of I `[ASSUMPTION]`. Modbus CRC errors, DS18B20 bad readings and I2C errors: no more with PWM on than with PWM off |
| **B7** Faults | safe failure | (1) Reset the ESP32 while the load runs. (2) Unplug the INA226 cable while holding. (3) Pull the supply connector under load. (4) Check the reverse-polarity protection with the supply current-limited to 0.2 A. | (1) load turns off, restarts cleanly. (2) duty freezes, error logged, no runaway. (3) nothing fails; scope shows the turn-off spike below the MOSFET and TVS ratings. (4) no current flows, nothing heats |
| **B8** First sun | the real module | Clear midday. Connect the module. Ten sweeps back to back, then 10 min of hold with a sweep every 60 s. Multimeter V_oc at the module connectors. | V_oc (INA, cable-corrected) within ±0.3 % of the meter. P_mp of the ten sweeps: standard deviation ≤ 0.3 % `[ASSUMPTION]` at steady G. Sweep reaches below 0.5 · V_oc. Hold voltage within ±1 % of the last sweep's V_mp, with no visible jump when branch B drops out at the end of a sweep. P_mp plausible against the listing scaled by G (a sanity check only: the listing has no temperature data) |
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
