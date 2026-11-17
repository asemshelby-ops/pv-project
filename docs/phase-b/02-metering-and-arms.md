# Phase B, part 2 — metering each event, comparing the two arms, cleaning or cooling

Status: prepared 2026-11-17, **software only, not adopted**, tested on synthetic files
only. Firmware part behind `PHASE_B_ENABLED` (`pv_config.h`), analysis part behind
`PHASE_B_ENABLED` (`config.py`); both off.

Code: `pv_spray_logic.c` (metering), `analysis/phase_b.py` (tables),
`analysis/fake_phase_b.py` (synthetic rig files). Tests: `tests/test_spray_logic.py`,
`tests/test_phase_b_analysis.py`.

## 1. What is metered

| Column of the 10 s row | Unit | How it is made |
|---|---|---|
| `pump_duty` | 0–1 | mean of the PWM duty over the 10 s before the row (the time integral of the duty, divided by the interval) |
| `flow_Lpm` | L/min | flow pulses counted in those 10 s ÷ `FLOW_PULSES_PER_L` ÷ the interval |
| `water_L_cum` | L | all pulses since boot ÷ `FLOW_PULSES_PER_L`; starts at 0 after a restart |
| `P_pump_W` | W | mean electrical power of the pump rail over those 10 s; empty if it could not be measured (status bit `PUMP_FAIL`) |

- **Flow** is counted by the ESP32's hardware pulse counter (PCNT), not by an interrupt
  routine, so no pulse is lost while the logger writes to the card. The count is read
  every loop pass; the counter wraps at 32 767 and the difference is taken modulo that.
- **Pump power** comes from a second INA226 on the pump rail, read every 250 ms as a
  64-sample average of voltage and current. Between two readings the power is integrated
  as a trapezoid; the last reading is held to the row boundary. A row whose readings
  failed has an empty field, never a zero.
- All three are **means over the row's interval**, not values at the instant of the row.
  That is deliberate: `P_pump_W × 10 s` summed over an event is the pump's energy,
  `flow_Lpm × 10 s / 60` summed is its water, and `pump_duty × 10 s / PUMP_DUTY` summed
  is the pump-on time. A test checks that the rows add up to what the logic counted.
- **"Its own I2C address" for the pump INA226.** The two M5Stack INA226-10A units that
  were bought have the same fixed address, 0x41 (`CLAUDE.md`, `PINMAP.md`). Two of them
  on one bus is impossible. The code therefore reaches the pump unit through its own
  PaHub channel (`PUMP_INA226_PAHUB_CH = 1`), as the pin map already planned; a different
  breakout with another address works too (`PUMP_INA226_PAHUB_CH = -1`,
  `PUMP_INA226_ADDR`). Both are `[PLACEHOLDER]`s. The M5 unit is rated ±10 A; the pump's
  start-up current has to stay inside that.

## 2. One row per event

`event_table()` reads the rows that carry an event's `spike_id` and the ten minutes
before its alarm, and computes, in the same way for both arms:

| Field | Meaning |
|---|---|
| `assign`, `assign_expected`, `assign_ok` | the logged arm, the arm the seed gives for this event number, and whether they agree |
| `spray_s`, `litres`, `pump_Wh` | pump-on time, water, pump energy over the window |
| `E_module_Wh`, `H_kWh_m2` | module energy and in-plane irradiation over the window |
| `yield_W_per_kWm2` | their ratio: module energy **normalized by irradiance** |
| `yield0_W_per_kWm2` | the same over the baseline, the 10 min before the alarm |
| `gain_rel` | `yield / yield0 − 1`: the event compared with itself just before |
| `dT_after_spray_C` | back-sheet mean 30 s after the longest possible spray, minus at the alarm |
| `delivered` | the arm was followed: water flowed in a spray event, none in a no-spray event |
| `valid`, `note` | counted in the comparison or not, and why not |

An event is **not valid** if less than 95 % of its window or baseline rows have power and
irradiance, if its arm changed inside the window, if the logged arm differs from the
sequence, or if it overlaps an excluded period (cleaning, a controlled test). Validity
never depends on the arm or on the outcome.

Why normalize by irradiance: a window with a passing cloud delivers less energy whatever
the spray does. Dividing by the irradiation of the same rows takes the sun's part out.
Why the baseline as well: modules differ from hour to hour (angle of the sun, temperature
level, soiling); comparing each event with its own ten minutes before removes most of
that, which makes the spread between events much smaller (`docs/paper/03` §12).

## 3. The two arms side by side

`arm_comparison()` gives, for each quantity, the number of events, the mean and the
spread in each arm, and **spray minus no-spray** with

- a 95 % interval (Welch: the two arms may scatter differently), and
- a p-value from a **permutation test that respects the blocks**: the arms are
  re-shuffled inside each block of four, 20 000 times, exactly as the logger could have
  drawn them, and the share of shuffles with a difference at least as large is counted.
  It needs no assumption about the shape of the data.

Two rows are there as a check and should show no difference: the mean irradiance of the
window and the module temperature at the alarm. If they differ, chance put different
kinds of events into the arms, and the result has to be read with that in mind.

Two analysis sets are reported:
- **as assigned** (the main result): every valid event in the arm it was drawn for, even
  if the pump failed. This is the honest answer to "what happens if the controller
  decides to spray";
- **as delivered**: the same without events whose arm was not followed.

Dry-run events are never mixed with wet ones.

## 4. Energy of one spray event: gain, pump, net, litres per kWh

```
gain   = (mean gain_rel of the spray arm − mean of the no-spray arm) × energy a window would have delivered
pump   = mean pump_Wh of the spray arm − mean of the no-spray arm
net    = gain − pump
L/kWh  = 1000 × litres per spray event / gain      (gross)      or / net
```

- The gain is also given without the baseline (difference of the normalized energies ×
  the irradiation of a window): the same expectation, more spread.
- Pump energy and litres are taken as a **difference between the arms**, so a small
  offset of the current sensor or a creeping flow count cancels.
- Intervals are bootstrap intervals over events, inside each arm.
- **L/kWh is printed only when the energy it divides by is above zero.** With a net gain
  of zero or less there is nothing to divide by, and the table says so instead of showing
  a huge or negative number.

What is not in this balance: the energy to pump water back if the loop loses height,
water lost by evaporation and drift over the day (the tank's weight, `CLAUDE.md`), and
anything after the window.

## 5. Cleaning or cooling

Water on the glass can raise the power in two ways: a cooler module, or a cleaner (or
wetter) front that lets more light through. The second is not what the project studies
and must not be booked as cooling.

`cleaning_check()` uses the I-V sweeps: the short-circuit current follows the light that
reaches the cells and hardly changes with temperature, so **I_sc / G** is a measure of
how much light gets through. For every event:

| Sweep | Which one |
|---|---|
| before | the nearest usable sweep at or before the alarm |
| after | the nearest at or after alarm + `SPRAY_MAX_S` + `CLEAN_AFTER_DELAY_MIN` (default 0): the first sweep once the longest spray has ended. The glass may still be wet |
| late | the last sweep of the window: the module has re-heated and the glass is dry |

each within `CLEAN_SWEEP_WINDOW_MIN` (10 min) of its target time. The times are fixed by
the alarm and by `config.py`, never by the arm, and **the no-spray arm gets the same
treatment**: I_sc / G also drifts with the sun's angle over half an hour, and that drift
shows in the no-spray arm. The reported number is the change in the spray arm minus the
change in the no-spray arm, with its interval. A difference at the *late* sweep is
cleaning; a difference only at the *after* sweep is the wet film.

Limits: it needs the measurement load and its sweeps (a logger without the load has no
I_sc); the pyranometer's own error enters through G; no sweep is taken while the pump
runs, so "after" is tens of seconds after the spray at the earliest.

## 6. Spray events and the paper's spike statistics

A wet spray changes the module's temperature, and the re-heating that follows could be
counted as a new spike. With the switch on, the pipeline treats every wet spray window
plus its lock-out as an excluded period, like a cleaning (`docs/paper/03` §4): no spike
is counted there and no detector is scored there. Dry-run events exclude nothing.

## 7. Example on SYNTHETIC files

`python -m analysis.demo_phase_b --days 20` (seed 5): 20 synthetic days, 85 events, 42
sprayed and 43 not. The generator (`analysis/fake_phase_b.py`) puts in a cooling of
0.0805 °C per second of spraying that decays with τ = 378 s, a pump of 20 W, a flow of
1.5 L/min, and a cleaning step of +0.6 % in the light reaching the cells at the first
spray of each day (later sprays of that day find clean glass). **All of these are
invented.** The example shows that the analysis finds them again, and how wide the
intervals are with this many events. It says nothing about a real spray.

| Quantity, spray minus no-spray | Put in by the generator | Found, SYNTHETIC (95 % interval) |
|---|---|---|
| Module temperature 30 s after the spray | ≈ −4.0 °C (4.3 °C at the end of the spray, then 30 s of re-heating) | −4.01 °C (−4.32 to −3.71) |
| Pump-on time, water, pump energy | 60 s, 1.5 L, 0.333 Wh | 60 s, 1.50 L, 0.333 Wh |
| Energy gain relative to the baseline | ≈ +0.7 % of a window's energy (cooling 0.42 % + the cleaning step in 20 of the 42 sprays) | +0.89 % (+0.35 to +1.43), p = 0.002 |
| The same without the baseline | the same effect | +1.02 Wh per kWh/m² (+0.02 to +2.01), p = 0.054 |
| Gain per spray event | ≈ 0.21 Wh | +0.260 Wh (+0.112 to +0.413) |
| Net = gain − pump | ≈ −0.12 Wh | −0.074 Wh (−0.221 to +0.080) |
| Litres per kWh, gross | — | 5 780 L/kWh; net: not given (the net energy is not above zero) |
| Change of I_sc / G at the last sweep of the window | ≈ +0.29 % on average (+0.6 % in 20 of 42) | +0.26 % (+0.09 to +0.44) |
| Balance checks: irradiance of the window; temperature at the alarm | no difference | −47 W/m² (−115 to +20); −0.8 °C (−3.6 to +1.9) |

What the example shows:
- every injected quantity lies inside the interval that was found;
- the baseline matters: with it the gain is clear (p = 0.002), without it the same data
  give p = 0.054;
- even with 85 events, the interval of the **net** energy includes zero. Whether a spray
  pays for its pump cannot be told from a trial of this size when the gain and the pump
  energy are this close; this is the subject of `docs/paper/03` §12;
- the cleaning check finds a step of a quarter of a percent in I_sc / G. Without it,
  that part of the gain would have been booked as cooling.
