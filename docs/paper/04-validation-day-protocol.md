# Validation day: ESP32 DAQ against the commercial data logger

Status: protocol for **generic channels**, 2026-11-11. The data-logger model is unknown,
so everything about the logger is `[ASSUMPTION]` and is revised when the model and its
manual arrive. The logger is the reference instrument; the ESP32 DAQ is the instrument
under test.

## 1. What the day must show

That the ESP32 DAQ measures the quantities the paper uses — temperature **rises**,
irradiance steps, V, I and time — with errors inside the uncertainty budget
(`01-uncertainty-budget.md`). One clear day is the minimum; a second day with clouds or
a mesh test adds the dynamic case.

## 2. Assumed data-logger channels `[ASSUMPTION]`

| Need | Assumed input | If the logger lacks it |
|---|---|---|
| 3 back-sheet temperatures | thermocouple or RTD inputs | use fewer points; T3 (centre) first |
| 1 air temperature | thermocouple or RTD input | skip; BME280 is context only |
| PYR20 analog | DC voltage, 0–2 V range | skip; compare G against the modelled clear-sky curve only |
| Module voltage | DC voltage up to 25 V | resistor divider, measured ratio |
| Module current | shunt-voltage (mV) input or a current input | handheld clamp or multimeter spot checks at 5 operating points |
| Logging interval | ≤ 10 s; 1 s preferred | use its fastest setting |
| Clock | settable to the second; export with timestamps | align by sync events only (§5) |

Points to read in the logger's manual before the day: accuracy of each input type,
input isolation (are the channels isolated from each other and from ground?), input
impedance on the voltage ranges, thermocouple types supported, export format.

## 3. Two cautions that decide what the comparison can prove

1. **Temperature.** A thermocouple channel is often less accurate in absolute terms than
   a DS18B20 (±0.5 °C, datasheet). Check the logger's own figure. If it is worse than
   ±0.5 °C, the logger validates temperature **changes and timing**, not absolute values.
   To make the comparison meaningful, the reference probes go into the **same water bath**
   as the DS18B20 probes first (step P2), so both systems share one offset correction.
2. **Irradiance.** The logger reads the PYR20's analog output; the ESP32 reads the same
   sensor over Modbus. Agreement proves the ESP32 read-out and timing. It says **nothing
   about the accuracy of the PYR20 itself**: both paths share the sensor. The paper must
   word this correctly.

## 4. Preparation (the day before)

| Step | Action | Record |
|---|---|---|
| P1 | Run the BRINGUP "Before logging" checklist. Set the ESP32 RTC to UTC (`set_rtc.py`). Set the logger clock to UTC from the same PC, in the same minute. | both clock offsets against the PC, in seconds |
| P2 | Water bath: all DS18B20 probes and the reference probes together, stirred, at 2–3 temperatures between ≈ 20 and ≈ 60 °C, 10 min each. | offsets of every probe against the bath mean (or against a reference thermometer, if one exists) |
| P3 | Mount reference probes on the back sheet **beside** T1, T3 and T5: within 2 cm, same paste, tape and cover. Do not move the DS18B20 probes. | photo of each position |
| P4 | Wire the PYR20 analog output to the logger (0–2 V input), ground at the PYR20 supply ground. | wiring sketch |
| P5 | Voltage: logger input across the module terminals at the junction-box connectors. Current: logger shunt input on its own shunt in the **negative** lead, or the method the manual recommends. Keep the INA226 unit where it is. | wiring sketch; shunt value |
| P6 | Grounding check: with everything connected and the module covered, measure the DC voltage between ESP32 ground and logger ground; it should be ≈ 0. If the logger inputs are not isolated, connect grounds at **one** point only. | reading |
| P7 | Dry run indoors for 10 min: both systems log, export both files, open them. | both files load; column mapping written down |

The M5 INA226 unit is isolated (M5Stack docs), which makes a ground loop through the
current measurement unlikely; step P6 still checks it.

## 5. The day

Times in local time; solar noon is computed for the date, not assumed to be 12:00.

| Time | Action |
|---|---|
| Before sunrise + 1 h | Clean module and PYR20 (daily protocol). Start both systems. Fill in the log sheet. |
| Start | **Sync event S1:** press the marker button, then cover the PYR20 fully with a card for 5 s, uncover for 5 s; repeat 3 times. The square pulses appear in both records. |
| Morning to afternoon | Normal logging with the load holding the MPP and sweeping (once the load exists). Before that: logging at open circuit is still valid for temperature, irradiance and time. |
| ≈ 10:00 | **Mesh test:** shade mesh over module **and** PYR20 for 20 min, marker at on and off. Gives a known step in G and a temperature fall and rise. |
| Solar noon ± 30 min | Hands off: the steady section used for noise and bias. |
| ≈ 14:00 | **Sync event S2** (same as S1). Optional second mesh test. |
| Sunset − 1 h | **Sync event S3.** Stop both systems. Copy both files the same evening; photograph the log sheet. |

Electrical operating points (once the load exists): during one quiet 10-min section the
firmware steps the load through five duties (near V_oc, two points on the slope, near
MPP, near the lowest voltage) and holds each for 60 s, so V and I are compared at steady
values and not only during fast sweeps.

## 6. Analysis

1. Load both files; convert both to UTC; resample the logger to the ESP32's row times
   (linear interpolation; if the logger is slower than 10 s, compare at its own times).
2. **Time alignment.** Find the lag that maximises the cross-correlation of the two G
   records around S1, S2 and S3 separately. Report the three lags. Their mean is the clock
   offset; their change over the day is the relative drift. Apply the mean lag before the
   comparisons below.
3. For each channel pair compute, on the daytime section:
   - bias (mean difference) and its 95 % limits of agreement (Bland–Altman);
   - RMSE;
   - slope and intercept of an ordinary regression, ESP32 on logger.
4. **Temperature rises.** For every 10-min window, compute the rise in both systems
   (same definition as `03-spike-definition-and-metrics.md` §2). Compare the two rises:
   bias and 95 % limits. This is the number that matters for spikes.
5. **Irradiance steps.** For the mesh on/off and the sync pulses: step size in both
   systems, and the time of each edge.
6. Write the result table (§7) and keep the script, the two raw files and the log-sheet
   photo together.

## 7. Acceptance criteria `[ASSUMPTION]` — proposed, to confirm with the supervisor

| Quantity | Criterion | Reason |
|---|---|---|
| Rise over 10 min, each compared sensor | bias within ±0.1 °C; 95 % limits within ±0.3 °C | ten times below the 3 °C spike |
| Temperature, absolute | bias within ±0.5 °C after the shared bath correction | DS18B20 datasheet limit |
| G, Modbus against analog | bias within ±1 % of reading above 200 W/m²; edges within ±2 s | same sensor: only read-out error is allowed |
| V | bias within ±0.3 % after cable correction | budget: 0.11 % + reference |
| I | bias within ±1 % (before calibration), ±0.5 % after applying one gain factor | budget: shunt tolerance |
| P | within ±1 % | follows from V and I |
| Time | lag ≤ 2 s at S1; change of lag S1→S3 ≤ 1 s | budget §6 |
| Data completeness | ≥ 99 % of ESP32 rows present between S1 and S3 | logger robustness |

If a criterion fails: find the cause, fix it, and repeat the day. A constant gain or
offset may instead be adopted as a calibration factor, if it is stable across the day
and recorded in the paper. A failed criterion is never hidden by widening it afterwards.

## 8. What goes into the paper

One table (bias, limits of agreement, RMSE per quantity, with the reference's stated
accuracy), one figure (the two records of temperature and G over the day, with the mesh
test enlarged), and the sentence about the limits of the irradiance comparison (§3).

## 9. To update when the logger model is known

Channel table (§2), reference accuracies in §7, wiring of V and I (P5), export format
and time handling (§6 step 1).
