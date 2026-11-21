# Load of the module and γ — one page for د. عبدالإله الحمياني

2026-11-21 (second version of the day). Every price, time and temperature below is an
estimate `[EST]`, not a seller quote or a measurement. Detail: `firmware/BRINGUP.md` §12–13,
`docs/paper/02` §9.

## Decision: back to your 3 Nov instruction — a fixed resistor, nothing else

**No battery, no charge controller, no lamp.** The module is connected to a fixed power
resistor matched to it:
- **≈ 3 Ω**: two 1.5 Ω, 100 W aluminium-housed resistors in series (total rating ≥ 200 W,
  because the 100 W rating holds only on the heatsink and ambient of the datasheet; ≈ 90 W
  dissipated at the module's ≈ 5.5 A `[EST]`), bolted to an aluminium plate.
- In shade, ≥ 1.5 m from the module and from the air-temperature sensor (BME280), downwind
  of both, away from anything flammable — so it neither heats nor cools the module.
- Wiring: module → MC4 cable → fuse → INA226 (panel channel) → resistor.
- Cost ≈ 60–150 SAR `[EST]`; about one week from parts to logging; no firmware change.

**Cancelled:** MPPT controller, battery, lamp and its ESP32 switch, the third INA226 for the
battery. **The list proposed by teammates (Victron PWM controller + 9 Ah AGM + H4 halogen
lamps) was also rejected:** (1) a PWM controller pins the module near the battery voltage,
on the flat part of its curve, where power hardly changes with temperature — it hides the
very effect the project studies; (2) it contradicts your 3 Nov note; (3) the lamps total
120 W, more than the 100 W module can supply. Only its MC4 extension cable is kept.

**What the fixed resistor costs us.** It holds the module at its maximum-power point at one
condition only. In our simulation model (placeholder parameters, not our module) 3.0 Ω
matches near ≈ 950 W/m² on a hot module and takes ≈ 0.56–0.65 of the maximum power at
500 W/m². This does not matter for detecting spikes (temperatures and irradiance), but the
measured power cannot give γ.

## γ (power lost per °C)

- **Primary:** a literature value for mono-crystalline silicon with its range — outdoor data
  from mono-Si plants give 0.30–0.45 %/°C in absolute value (Kaldellis et al. 2014) —
  central −0.40 %/°C, stated as assumed, with a **sensitivity analysis from −0.30 to
  −0.50 %/°C** for every result that uses γ (power cost of a spike, net energy of cooling).
- **Better:** if an I-V curve tracer can be borrowed for 2–3 clear days, we measure our own
  module's γ with the method already written (`docs/paper/02` §2–5).

## Paper focus

Real-time spike detection on the ESP32 **plus flow-rate control**: which pump PWM duty and
burst duration give the highest net energy (cooling gain minus pump energy). Three tests,
added to the plan: (1) bucket test — flow and pump power at 30 / 50 / 70 / 100 % duty;
(2) flow steps on the module — temperature drop against duty and burst length;
(3) the duty with the highest net gain, at both ends of the γ range. ≈ 3–4 clear days for
test 2 `[EST]`, after the pump and nozzles are bought.

## Two questions for the supervisor

1. Can the department lend us an **I-V curve tracer or electronic load for 2–3 clear days**;
   if not, is the **literature γ with a sensitivity analysis** acceptable for the paper?
2. Do you approve the **paper focus**: spike detection on the ESP32 plus flow-rate control
   (pump duty and burst duration against net energy), measured with the fixed-resistor load?
