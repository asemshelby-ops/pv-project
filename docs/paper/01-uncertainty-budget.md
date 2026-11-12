# Uncertainty budget — ESP32 DAQ, paper phase

Status: first version, 2026-11-11, **before any outdoor data**. Method: JCGM 100:2008 (GUM).
Every datasheet limit `±a` is treated as a rectangular distribution, `u = a/√3`; a
resolution step `q` gives `u = q/√12`. Expanded uncertainty uses `k = 2`.

Tags: `[DS]` datasheet value checked on 2026-11-11 (sources at the end); `[EST]` first-pass
estimate; `[ASSUMPTION]` chosen value with no source yet; `[MEASURE]` must be replaced by a
bench or field measurement, with the step that produces it.

Our DS18B20 probes are probably clones (BRINGUP step 5). The Maxim limits below apply to
genuine parts only, so they are `[ASSUMPTION]` for our probes until the water-bath
calibration is done.

## 1. The result in one table

| Quantity | Absolute, k = 1 | Change within ≤ 10 min, k = 1 | Dominant term |
|---|---|---|---|
| T_module (mean of T1..T5, back sheet) | 0.14–0.31 °C + mounting `[MEASURE]` | ≈ 0.03–0.05 °C `[EST]` | sensor accuracy (absolute); noise (change) |
| T_cell (from T_module) | adds ≈ 0.5 °C at 900 W/m² `[ASSUMPTION]` | adds only when G changes | cell-to-back offset |
| T_air | ≈ 0.65 °C `[ASSUMPTION]` | ≈ 0.1 °C `[ASSUMPTION]` | shield and self-heating |
| G_POA | ≈ 3.4 % of reading, or ≈ 6.7 % at 900 W/m² (see §4) | `[MEASURE]`, resolution floor 0.03 % | "5 %" accuracy statement |
| V | ≈ 0.11 % at 18 V + cable drop `[MEASURE]` | ≈ 0.01 % | gain drift; cable |
| I | ≈ 0.59 % at 5.5 A | ≈ 0.01 % | shunt tolerance `[ASSUMPTION]` |
| P = V·I | ≈ 0.60 % | ≈ 0.02 % + ripple `[MEASURE]` | shunt tolerance |
| Time | ≤ 1.2 s after setting; + ≤ 0.30 s/day drift | ≤ 1.1 s between channels in one row | read order; RTC drift |

**What this means for the paper.**
1. A spike (≥ 3 °C in ≤ 10 min) is measured with a signal-to-uncertainty ratio of about
   60 (3 °C against ≈ 0.05 °C). Detecting and sizing spikes is not limited by the sensors.
2. The electrical chain resolves a relative power change of ≈ 0.02 %, far below the
   ≈ 1.2–1.6 % a spike costs `[EST, CLAUDE.md]`. But the power also follows irradiance, and
   G is known to only a few percent. So the power cost of a spike is **not read from
   measured power**. It is computed as `ΔP/P = γ · ΔT_cell`, with γ measured separately
   (`02-gamma-from-iv.md`). Its uncertainty is then set by γ (target ≤ 10 % relative, k = 1).
3. Absolute irradiance is the weakest quantity. The paper uses G for three things only:
   the size of irradiance steps, the stability check during I-V sweeps, and the clear-sky
   index. None of them needs better than a few percent.

## 2. Module temperature

Signal: `T_module` = mean of the five calibrated back-sheet sensors.

| Source | Limit | u (°C) | Basis |
|---|---|---|---|
| Sensor accuracy, −10 to +85 °C | ±0.5 °C | 0.29 | `[DS]` DS18B20 |
| Drift | ±0.2 °C | 0.12 | `[DS]` DS18B20 |
| Resolution, 12 bit | 0.0625 °C | 0.018 | `[DS]` DS18B20 |
| Noise per reading | — | 0.05 | `[ASSUMPTION]` (config.py) → `[MEASURE]` water bath, 10 min at one temperature |
| Mounting (contact, cover, cable conduction) | — | `[MEASURE]` | validation day: reference probe beside T1, T3, T5 |

- One uncalibrated sensor: `u = √(0.29² + 0.12² + 0.018²) ≈ 0.31 °C`.
- Mean of five: 0.14 °C if the five errors are independent, 0.31 °C if they are fully
  correlated (same production batch). The truth lies between; the water-bath
  cross-calibration removes the differences between sensors, and only a reference
  thermometer removes the common part. The reference thermometer and its accuracy are not
  chosen yet `[ASSUMPTION: none available → quote 0.31 °C]`.
- **A rise within one event.** Offsets, drift and mounting error are constant over
  10 min and cancel: `u(ΔT) = √2 · √(0.018² + 0.05²) / √5 ≈ 0.034 °C` `[EST]`. The
  3-sample median lowers it slightly. Use `U(ΔT) ≈ 0.1 °C` (k = 2).
- **Spatial spread.** The standard deviation between T1..T5 at each sample is logged as a
  result (temperature non-uniformity of the module), not as an error.
- **Sensor response time.** The probes are steel capsules taped to the back sheet. Their
  lag is unknown `[MEASURE: step response in the water bath, time to 63 %]`. It delays
  the detected onset by its own time constant; it does not change the size of a rise that
  lasts minutes (module time constant ≈ 6.3 min `[LIT, CLAUDE.md]`).

Cell temperature: `T_cell = T_module + ΔT_cb · G/1000`, with `ΔT_cb = 3 °C` for an
open-rack glass/cell/polymer-sheet module (King et al. 2004, as recorded in PROJECT_STATE
§5.1). Uncertainty of `ΔT_cb`: ±1 °C `[ASSUMPTION]` → 0.58 °C at 1000 W/m², 0.52 °C at
900 W/m². For a rise at steady irradiance the term cancels. For an irradiance-driven rise
from G₁ to G₂ it adds `ΔT_cb · (G₂ − G₁)/1000` to the cell-temperature rise.

## 3. Air temperature and humidity

| Source | Limit | u | Basis |
|---|---|---|---|
| BME280 temperature, 0–65 °C | ±0.5 °C | 0.29 °C | `[DS]` rev 1.24 |
| Resolution | 0.01 °C | 0.003 °C | `[DS]` |
| Board heating and radiation-shield error | ±1 °C | 0.58 °C | `[ASSUMPTION]` → `[MEASURE]` against a shaded, ventilated reference thermometer |
| Humidity, 20–80 %RH at 25 °C | ±3 %RH | 1.7 %RH | `[DS]` |

Combined air temperature: ≈ 0.65 °C. The Bosch datasheet states that the reading depends
on PCB temperature and self-heating and "is typically above ambient temperature" `[DS]`,
so the sign of the error is known: T_air is biased high. T_air enters the paper only as a
feature and as context, not in the power-cost calculation.

## 4. In-plane irradiance

| Source | Limit | u | Basis |
|---|---|---|---|
| Accuracy | "5 %" | 2.9 % of reading, **or** 58 W/m² if it means 5 % of the 2000 W/m² range | `[DS]` INFWIN; basis (reading or full scale) not stated |
| Directional error, 0–70° incidence | ±3 % of reading | 1.7 % | `[DS]` INFWIN |
| Resolution | 1 W/m² | 0.29 W/m² | `[DS]` |
| Coplanarity with the module, ≤ 1° | 1.0 % at 30° incidence | 0.58 % | `[ASSUMPTION]`: `tan θ · δ`; check with an inclinometer at installation |
| Temperature dependence, response time, long-term stability | not stated | — | `[DS]` silent → `[MEASURE]` where possible |

- Absolute, if "5 %" is of reading: `√(2.9² + 1.7² + 0.58²) ≈ 3.4 %` (k = 1), 6.8 % (k = 2).
- Absolute, if "5 %" is of full scale: ≈ 6.7 % at 900 W/m² (k = 1). Ask the seller which
  it is; until then the paper quotes the worse case.
- The sensor covers 400–1100 nm `[DS]`: a silicon photodiode, not a thermopile. Its
  reading shifts with spectrum (haze, dust, low sun). This is a known limitation to state
  in the paper; it is not quantified here.
- Modbus is a digital path: no ADC error is added on the ESP32 side.
- **Short-term repeatability** decides how small an irradiance step we can see and how
  well a sweep can be normalised. `[MEASURE]`: on a clear day near solar noon, take
  2-minute windows, remove the linear trend, and report the standard deviation.

## 5. Voltage, current, power

INA226 limits are the maximum values of the TI datasheet (Rev. C) `[DS]`. The M5Stack unit
states the 5 mΩ shunt and the ±10 A range but **no shunt tolerance** `[DS]`.

| Source | Limit | u at 18 V / 5.5 A | Basis |
|---|---|---|---|
| Bus gain error | 0.1 % | 10.4 mV | `[DS]` |
| Bus offset | ±7.5 mV | 4.3 mV | `[DS]` |
| Bus gain drift, 50 ppm/°C over 30 °C | 0.15 % | 15.6 mV | `[DS]` drift; 30 °C swing `[ASSUMPTION]` |
| Bus LSB | 1.25 mV | 0.36 mV | `[DS]` |
| **V combined** | | **19.3 mV = 0.11 %** | |
| Shunt gain error | 0.1 % | 0.058 % | `[DS]` |
| Shunt gain drift, 50 ppm/°C over 30 °C | 0.15 % | 0.087 % | `[DS]`; swing `[ASSUMPTION]` |
| Shunt offset | ±10 µV = ±2 mA | 1.15 mA = 0.021 % | `[DS]` |
| Shunt LSB | 2.5 µV = 0.5 mA | 0.14 mA | `[DS]` |
| Shunt resistor tolerance | ±1 % | 0.58 % | `[ASSUMPTION]` → `[MEASURE]` against a reference ammeter (bench step B2) |
| **I combined** | | **0.59 %** | |
| **P = V·I** | | **0.60 %** | `√(0.11² + 0.59²)` |

- After the bench comparison with a reference ammeter, the 0.58 % term is replaced by the
  reference's own uncertainty. With a typical bench multimeter this gives ≈ 0.2–0.3 % for
  P `[EST]`.
- **Cable drop.** The INA226 measures voltage where its VBUS pin is connected, not at the
  module terminals. For copper, `R = 0.0172 Ω·mm²/m × length / area`. Example `[EST]`: a
  6 m loop of 2.5 mm² cable is 41 mΩ, which drops 0.23 V at 5.5 A, i.e. 1.3 % of Vmp. This
  is larger than every other voltage term and it grows with current, so it bends the I-V
  curve. Rule: mount the unit within 0.5 m of the junction box, measure the remaining
  resistance (bench step B2), and correct `V_module = V_meas + I · R_cable`.
- **Relative changes** over minutes use the same instrument, so gain terms cancel. What
  remains is resolution (≈ 0.007 % in V, ≈ 0.009 % in I) and PWM ripple aliasing, which
  the averaging setting must suppress `[MEASURE: bench step B6]`.
- **P_mp from a sweep** adds the fit error of the maximum (`02-gamma-from-iv.md` §4).

## 6. Time

| Source | Size | Basis |
|---|---|---|
| Setting the RTC from the PC | ≤ 0.1 s | `[EST]`: PC synced to internet time; `set_rtc.py` writes at a second boundary |
| RTC drift, 0–40 °C | ±2 ppm = 0.17 s/day | `[DS]` DS3231 |
| RTC drift, 40–85 °C | ±3.5 ppm = 0.30 s/day | `[DS]`; the enclosure may exceed 40 °C in the sun |
| RTC aging, first year | ±1 ppm | `[DS]` |
| Timestamp resolution in the row | 1 s | logger.ino |
| Read order inside one row | G: within ≈ 0.05 s of the timestamp `[EST]` (read first since 2026-11-12; up to 0.3 s if the sensor does not answer); T: ≈ +0.1 to 0.9 s | logger.ino: one Modbus exchange of 15 bytes at 9600 baud ≈ 16 ms + reply time, then BME280 and INA226, then the 750 ms conversion `[DS]`; `[MEASURE]` on the bench |
| RTC drift, measured | logged at every setting | `firmware/tools/set_rtc.py` writes the RTC-minus-PC offset before setting and the drift since the last setting to `firmware/tools/rtc_set_log.csv`; replaces the datasheet rows once a few weeks exist |

- 30 days without re-setting: up to 5–9 s. The weekly check in
  `05-daily-logging-and-cleaning.md` records the offset, so it can be corrected afterwards.
- These errors are small against the 6-minute thermal time constant. They matter for the
  comparison with the data logger, which is why that protocol aligns the two records with
  shared sync events and not by clock alone.
- Marker times use the RTC second edge plus `millis()`; accuracy a few ms `[EST, BRINGUP]`.

## 7. Derived quantities

| Quantity | Formula | Uncertainty, k = 1 |
|---|---|---|
| Spike size | `A = T_s(t_pk) − T_s(t_on)` | ≈ 0.05 °C `[EST]` |
| Cell-temperature rise | `ΔT_cell = A + ΔT_cb · ΔG/1000` | 0.05 °C at steady G; adds `0.58 °C × ΔG/1000` otherwise |
| Relative power cost | `ΔP/P = γ · ΔT_cell` | `√(u_r(γ)² + u_r(ΔT_cell)²)`; with `u_r(γ) = 10 %` and a 3 °C rise: ≈ 10 % of the cost |
| Power cost in W | `ΔP = γ · ΔT_cell · P_mp` | adds `u_r(P_mp) ≈ 0.6 %`: negligible |

Illustration only, with γ = −0.4 %/°C (**not our module's value**): a 3 °C spike costs
(1.2 ± 0.12) % of power, k = 1.

## 8. Open items that change this budget

1. Reference thermometer for the water bath: which one, and its accuracy.
2. PYR20 "5 %": of reading or of full scale (ask the seller).
3. Shunt tolerance of the M5 unit (bench step B2).
4. Cable length between the module and the INA226 unit (decided at installation).
5. Data-logger model: its channel accuracies replace the `[ASSUMPTION]` rows of
   `04-validation-day-protocol.md`.

## Sources (accessed 2026-11-11)

- JCGM 100:2008, *Evaluation of measurement data — Guide to the expression of uncertainty
  in measurement* (source priority set in PROJECT_STATE §12).
- DS18B20 datasheet, Analog Devices (Maxim), rev. 7/19:
  https://www.analog.com/media/en/technical-documentation/data-sheets/DS18B20.pdf
- INA226 datasheet, Texas Instruments, Rev. C: https://www.ti.com/lit/gpn/INA226
- BME280 datasheet BST-BME280-DS002, Bosch Sensortec, rev. 1.24 (Feb 2024):
  https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bme280-ds002.pdf
- DS3231 datasheet, Analog Devices (Maxim), rev. 10 (3/15):
  https://www.analog.com/media/en/technical-documentation/data-sheets/DS3231.pdf
- INFWIN PYR20 product page: https://www.infwin.com/pyr20-solar-radiation-pyranometer-sensor-rs485-modbus/
  and reseller page with the same figures: https://store.mcci.com/products/infwin-pyr20
- M5Stack Unit INA226-10A documentation: https://docs.m5stack.com/en/unit/Unit_INA226-10A
- King, Boyson, Kratochvil (2004), Sandia array performance model: cited here as recorded
  in PROJECT_STATE §5.1; the report itself is re-checked in Stage 3.
