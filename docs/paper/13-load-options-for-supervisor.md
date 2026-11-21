# What the module should be connected to — two options for د. عبدالإله الحمياني

2026-11-21. One page for the supervisor's decision. **No part of either option has been bought:
all purchases of controller, battery and lamp are on hold until he decides.** Every price, time
and temperature below is an estimate `[EST]`, not a seller quote or a measurement. Detail
behind Option 1: `docs/paper/02` §9 (γ Method C), `firmware/BRINGUP.md` §12–13.

**Why it matters.** The paper needs γ (how much power the module loses per °C) to turn a
thermal spike into a power cost. Without the PWM sweep load (dropped 2026-11-21) there are no
I-V curves, so γ must come from the power the module delivers into whatever it is connected to.

**Option 1 — real MPPT charge controller + AGM battery + 100 W lamp switched by the ESP32**
(current plan). The controller holds the module at its maximum-power point; the lamp burns the
energy around solar noon (± 2 h, only above ≈ 800 W/m², off below ≈ 700 W/m² or at a battery
cut-off voltage); the battery buffers.

**Option 2 — one fixed resistor of ≈ 3.2 Ω** (3.3 Ω is the nearest standard value), no battery,
no controller — his request of 3 Nov. Aluminium-housed resistor on a heatsink, in the shade,
downwind of the module and the air sensor.

| | Option 1: MPPT + battery + lamp | Option 2: fixed ≈ 3.2 Ω resistor |
|---|---|---|
| Parts, cost `[EST, not quotes]` | MPPT controller that really tracks (≈ 250–450 SAR), AGM 12 V ≥ 26 Ah, preferably ≈ 40 Ah (≈ 200–450 SAR), 100 W 12 V lamp + holder (≈ 30–80 SAR), ≥ 10 A switch + fuses (≈ 20–40 SAR), third INA226-10A for the battery (≈ 40–50 SAR). **≈ 550–1 050 SAR** | Resistor(s) with ≥ 200 W total rating, because the 100 W rating holds only on a large heatsink at 25 °C and Makkah shade is hotter (≈ 20–60 SAR), heatsink (≈ 30–80 SAR), fuse already bought. **≈ 60–150 SAR** |
| How γ is obtained | Power against cell temperature over many days, only in rows inside the noon window with the lamp on and the battery not full (Method C). Must first pass a go/no-go: module voltage at noon ≈ 15–18 V and ≥ 2 V above the battery, otherwise the controller is not tracking and γ is lost | The same regression, but a fixed resistor sits on the maximum-power point at **one** irradiance only. Twin model (simulation, placeholder module): the fixed-resistor slope is −0.48 %/°C against the model's γ of −0.47 % at 900 W/m², but −0.59 % at 1000 and −0.24 % at 800 W/m². So only a narrow irradiance band is usable, and where that band lies for our real module is unknown without one I-V curve |
| Measurement weaknesses | Controller's tracking accuracy unknown and not measurable without a sweep; most of the day unusable (outside window, battery full, ≥ 5 min after each lamp switch); more columns (lamp state, battery V and I, charge state) | Off the maximum-power point at most irradiances: model, simulation: ≈ 0.82–0.93 of P_mp at 700 W/m², ≈ 0.6–0.7 at 500. Power cost of a spike must use a P_mp estimate, not the measured power; but nothing to log beyond today's 21 columns |
| Risk of false thermal spikes | **Yes.** When the battery fills or the lamp switches off, the controller moves the module toward open circuit and it runs ≈ 4 °C hotter `[EST]` — one spike's worth, for an electrical reason. Must be labelled from lamp and battery state and kept out of the spike counts | **Low.** The load never changes, so the heat taken out electrically changes only smoothly with the sun. Only a broken wire or blown fuse (open circuit, ≈ 4 °C rise `[EST]`) — seen at once as current = 0 |
| Build time `[EST]` | Delivery 1–3 weeks; wiring 1–2 days; firmware (lamp rules, battery INA226, columns) ≈ 1 week of Asim's time; one clear noon for the go/no-go. **≈ 2–4 weeks** | Local parts in days; mounting and wiring ½–1 day; no firmware change. **≈ 1 week** |
| Failure modes | Controller turns out PWM-type → no γ; battery drained by a stuck lamp switch or weak-sun days; AGM life shortened by heat (needs shade); 100 W lamp is a hot object near wiring; lamp failure → curtailment → false spikes | Resistor overheats if the heatsink is too small or in sun; its warm air reaches the air-temperature sensor if placed upwind; cable resistance (≈ 0.1 Ω ≈ 0.5 V at 5 A `[EST]`) moves the operating point, so voltage is read at the module |

**In one sentence each.** Option 1 gives the better γ *if* the controller really tracks, at
higher cost, longer build and a known source of false spikes. Option 2 is cheap, quick and
clean for the spike study, but gives γ only in a narrow irradiance band. With either option,
borrowing an I-V curve tracer (or electronic load) for 2–3 clear days would measure γ directly
(Method A, `docs/paper/02` §2) and would also show where the fixed resistor really sits.

## Two questions for the supervisor

1. Which load do you approve for the paper: **Option 1** (MPPT controller + AGM battery + 100 W
   lamp) or **Option 2** (fixed ≈ 3.2 Ω resistor, no battery, no controller)?
2. Can the department lend us an **I-V curve tracer or electronic load for 2–3 clear days**, or
   is γ estimated from operating data (reported as an estimate with its confidence interval,
   not as an I-V measurement) acceptable for the paper?
