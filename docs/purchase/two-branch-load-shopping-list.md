# Two-branch PWM load — shopping list

Status: draft, 2026-11-13 (Claude), for Asim to check. Based on
`docs/paper/06-load-bench-test-protocol.md` §1 (parts) and §2 (equipment),
`docs/paper/07-load-firmware-spec.md` (heatsink sensor), and the "still missing" list in
`PROJECT_STATE.md` (§2 orders note, §4). The Excel BOM stays authoritative: copy the
rows you accept into it.

How to read the table:
- **Rating verified?** "Yes" means the rating in 06 §1 was checked against the
  manufacturer's datasheet. "**NO — FLAG**" means it could not be checked: generic part,
  no datasheet, or seller numbers only. Each flagged part has a bench check that must
  pass (06 step B0 or B5) before it carries load.
- **Link** is the product page found by web search on 2026-11-13. Prices are what the
  search index showed, not live prices; check stock and price before ordering. Where no
  Saudi listing was found, the link is a search page and the row says so.
- An Amazon listing that names a branded part (e.g. IRFB4110) may be a copy. Measure it
  in B0 before trusting the datasheet numbers.

## A. Load parts (06 §1) — buy now

| # | Part | Exact part number | Qty (fitted + spare) | Why this part / rating needed (06 §1) | Rating verified? | Saudi source and link |
|---|---|---|---|---|---|---|
| A1 | N-MOSFET, one per branch | **Infineon IRFB4110PbF**, TO-220AB | **5** (2 + 3), one pack of 5 | Need V_DS ≥ 60 V, pulse ≥ 30 A, low R_DS(on). Datasheet: V_DS 100 V, I_DM 670 A, R_DS(on) 3.7 mΩ typ / 4.5 mΩ max at V_GS = 10 V. Needs 10 V gate drive → driver on 12 V (A3) | Yes for the genuine part ([Infineon datasheet via Mouser](https://www.mouser.com/datasheet/2/196/Infineon_IRFB4110_DataSheet_v01_01_EN-1732544.pdf)). **FLAG: Amazon seller part, authenticity unknown** — measure R_DS(on) in B0 | Amazon.sa, PENGLIN IRFB4110 pack of 5, ≈ SAR 56: no direct product link found, [search page](https://www.amazon.sa/-/en/s?k=PENGLIN+IRFB4110). Alternative with the same listing type: [IRFB3206 (60 V, 120 A) 5 pcs](https://www.amazon.sa/-/en/5pcs-3206-Mosfet-220Ab-Irfb3206/dp/B09T786LT4). That part sits exactly at the 60 V floor, so the IRFB4110 is the better choice |
| A2 | TO-220 insulating kit (silicone pad + shoulder washer) and thermal paste | generic TO-220 silicone pad kit; any non-conductive paste | 1 kit (≥ 10 pads), 1 tube | The MOSFET tab is the drain; both MOSFETs share a heatsink with the resistors | n/a | Amazon.sa: no listing checked, [search page](https://www.amazon.sa/-/en/s?k=TO-220+silicone+insulator+pad) |
| A3 | Gate driver, 2 channels, 3.3 V logic inputs | **Microchip TC4427A** (dual non-inverting, 1.5 A), DIP-8 (`TC4427ACPA`) or SOIC-8 | **3** (1 + 2) | Datasheet: V_IH 2.4 V min (ESP32 3.3 V output is enough), V_IL 0.8 V, supply 4.5–18 V, 1.5 A peak. Supply it from the 12 V adapter (row C2), not the PV line | Yes ([Microchip product page](https://microchip.com/wwwproducts/en/TC4427A)) | **No Saudi source found.** Ask at a Jeddah shop (C. below), else order from a distributor that ships to KSA: [LCSC TC4427AVPA](https://www.lcsc.com/product-detail/C640787.html) or Mouser/DigiKey |
| A4 | 10 kΩ pull-down, driver input to GND (GPIO33 → A, GPIO14 → B) | 10 kΩ 1 % 1/4 W metal film | 2 + many spares | 06 §1 and the 2026-11-12 decision | n/a | Comes in the resistor kit of row C1 |
| A5 | Resistor bank A and B, 1.5 Ω each | **Option 1 (Saudi, quick):** 3 × 0.5 Ω 100 W aluminium-housed wirewound in **series** per bank → 1.5 Ω, 300 W nameplate | **8** (6 + 2) = 4 packs of 2 | Bank A: ≥ 100 W at 50 °C from the derating curve. Bank B: 308 W pulse for ≈ 2 s. Three in series share the power: ≈ 33 W each in hold | **NO — FLAG.** Generic part, no datasheet, no derating curve, unknown heatsink condition for the "100 W". B0 can't be passed from paper; it rests on the B5 heat run (resistor body temperature with ≥ 20 °C margin). One buyer review of a similar listing reports units off-value: measure each in B0 (±5 %) | Amazon.sa: [2 pcs 100 W 0.5 Ω aluminium shell, ±5 %](https://www.amazon.sa/-/en/Wirewound-Aluminum-Resistor-Tolerance-Certified/dp/B0BHZT9683) |
| A5-alt | Same, with a datasheet | **Option 2:** Arcol **HS100 3R0 J** (3 Ω 100 W), 2 in **parallel** per bank → 1.5 Ω, 200 W nameplate | 5 (4 + 1) | Arcol publishes the derating curve and the reference heatsink, so B0 can be passed from the datasheet | Yes once you download the Arcol HS datasheet; **I did not read the derating curve** | Saudi-domain distributor [sa.enrgtechglobal.com](https://sa.enrgtechglobal.com/product/chassis-mount-resistors/ET13873195/hs100-1k5-j) lists the Arcol HS100 series (that page is the 1.5 kΩ value: search their site for `HS100 3R0 J`). Otherwise RS/Mouser |
| A6 | Input capacitor bank | **Panasonic EEU-FR1V102** (1000 µF, 35 V, 105 °C, 12.5 × 20 mm) | **6** (4 + 2) | Need ≥ 2000 µF, ≥ 35 V, ≥ 6 A RMS ripple at 20 kHz with margin. Distributor data: 2.6 A RMS at 100 kHz, ≈ 18 mΩ each → 4 in parallel = 10.4 A at 100 kHz | **Partly — FLAG.** The 2.6 A figure is from distributor pages, and the ripple at **20 kHz** needs the frequency correction factor from Panasonic's FR datasheet, which I did not read. Check it before ordering: 4 parts must still give ≥ 6 A with margin at 20 kHz. **Settling note:** 4000 µF doubles the time constant the 2026-11-13 simulation assumed (2000 µF): worst τ ≈ 36 ms at 200 W/m² instead of 18. The settle check in the firmware absorbs it, but sweeps get longer; B4 measures it. If B4 shows the sweep is too slow, try 3 parts (7.8 A at 100 kHz) | **No genuine Panasonic found on Amazon.sa.** Generic 1000 µF 35 V parts there ([uxcell 10 pcs](https://www.amazon.sa/-/en/uxcell-Capacitor-Aluminum-Electrolytic-Capacitors/dp/B08MZ54NP1)) give **no ripple or ESR rating → do not use for the bank**. Order the Panasonic part from Mouser/DigiKey/LCSC (ship to KSA) |
| A7 | Freewheel Schottky across each bank | **MBR20100CT** (20 A, 100 V, TO-220AB; legs paralleled) | **3** (2 + 1) | Carries the bank-inductance current at turn-off, up to ≈ 14 A briefly; 100 V > V_oc | Yes (100 V, 20 A in the [Diodes Inc. part page](https://diodes.com/part/view/MBR20100CT%28LS%29) and other makers' datasheets). Confirm the package suffix | **No Saudi source found.** Mouser/DigiKey/LCSC, or ask a Jeddah shop |
| A8 | TVS at each MOSFET (drain–source) | **1.5KE33A** (unidirectional, 1500 W, DO-201) | **4** (2 + 2) | Stand-off 28.2 V > V_oc 21.5 V and > the 25 V lab-supply test; clamp 45.7 V max < 100 V V_DS | Yes (stand-off 28.2 V, clamp 45.7 V at I_PP in the ST/Littelfuse/Vishay listings) | **No Saudi source found** (Amazon.sa shows only 1.5KE15/18 kits and SMBJ24A, whose stand-off is too close to V_oc). Mouser/DigiKey/[LCSC](https://www.lcsc.com/product-detail/C132967.html) |
| A9 | Heatsink for bank A + both MOSFETs | extruded 6063 aluminium, 200 × 182 × 44.5 mm, 19 fins | 1 | Bank A dissipates up to ≈ 100 W all day in Makkah sun-shade | **NO — FLAG.** No thermal resistance (K/W) given by the seller. Whether it holds 100 W with margin is decided only in B5 | Amazon.sa: [Large aluminium heatsink 200 × 182 × 44.5 mm](https://www.amazon.sa/%D8%A7%D9%84%D8%A7%D9%84%D9%88%D9%85%D9%86%D9%8A%D9%88%D9%85-%D9%84%D8%B4%D8%B1%D9%8A%D8%AD%D8%A9-%D8%A7%D9%84%D8%A7%D9%84%D9%83%D8%AA%D8%B1%D9%88%D9%86%D9%8A-%D8%A7%D9%84%D8%AA%D8%B1%D8%A7%D9%86%D8%B2%D8%B3%D8%AA%D9%88%D8%B1-%D8%A7%D9%84%D8%AD%D8%B1%D8%A7%D8%B1%D8%A9/dp/B0B7RGS5SB), ≈ SAR 259 |
| A10 | Heatsink for bank B | 200 × 69 × 36 mm, 27 fins | 1 | Bank B carries ≈ 1.3 W mean (≈ 5 W in γ mode) but 308 W pulses | **NO — FLAG** (no K/W); B5 reads bank B temperature after the γ-mode run | Amazon.sa: [Awxlumv 200 × 69 × 36 mm](https://www.amazon.sa/-/en/Awxlumv-Diffusion-Amplifier-Transistor-Semiconductor/dp/B07TJZ2MYZ), ≈ SAR 111 |
| A11 | Heatsink temperature sensor (trip, `T_TRIP_C` = 80 °C) | one of the 10 DS18B20 already ordered | 0 to buy | 07 §8: no heatsink sensor → load stays in TRIP | — | Already ordered |
| A12 | Power connectors, module → load | **CHECK FIRST, don't buy yet:** MC4 male/female pair, only if the module leads end in MC4 (look at the module; the seller listing doesn't say). Plus polarised power connectors on the load board | 2 pairs, if needed | Reverse-polarity protection by polarised connectors + polarity check (PROJECT_STATE §4) | n/a | Amazon.sa: no listing checked, [search page](https://www.amazon.sa/-/en/s?k=MC4+connector). The A14 cable listing may already include MC4 plugs: check |
| A13 | ~~INA226 shunt, 10 mΩ~~ | **Do not buy** — Vishay WSK2512R0100FEA was the candidate | 0 | Decision 2026-11-13 (Asim): keep the M5Stack Unit INA226-10A with its on-board 5 mΩ shunt, ±10 A | — | — |
| A14 | Power cable, module → INA226 unit → load | 4 mm² (12 AWG) PV cable, H1Z2Z2-K / PV1-F, red + black | 10 m red + 10 m black (planned run 2–5 m, rest spare) | PROJECT_STATE §4: 2–5 m of 4 mm² gives ≈ 0.5–1.3 % drop at V_mp | **FLAG:** cable type and rating from the seller only | Amazon.sa: [KBE Berlin pair 10 m black + 10 m red, 4 mm²](https://www.amazon.sa/-/en/KBE-Berlin-Copper-Connectors-Extension/dp/B08ZKXJ8QG) |

**Note on A13 (the shunt).** Decided 2026-11-13 (Asim): no external shunt. The M5Stack
Unit INA226-10A already carries a 5 mΩ shunt rated ±10 A, which covers I_sc 5.88 A. The
firmware is calibrated for it (`INA226_10A_SHUNT_OHM` = 0.005, current LSB 0.5 mA). The
older 10 mΩ lines in PROJECT_STATE were written for a bare INA226 breakout and have been
corrected.

**Gate resistors:** `06` §1 does not specify any gate resistor for the load MOSFETs (the
~100 Ω gate resistor in CLAUDE.md is for the deferred pump driver), so none is listed.
If B1 or B6 shows ringing at the gates, add a few-ohm series gate resistor from the
C1 kit and note the change in `06`.

Already in hand (no purchase): blade-fuse kit and two 10 A inline holders (the 10 A fuse in
the supply lead, 06 §2); INA226 unit for the panel channel.

## B. Bench equipment (06 §2)

| # | Item | Part / model | Qty | Rating needed | Verified? | Source and link |
|---|---|---|---|---|---|---|
| B1 | Lab DC supply | **First choice: borrow** one from the university lab (plan of 2026-11-04). If none can be lent: **Wanptek KPS3010D** (0–30 V, 0–10 A, CV/CC) or NICE-POWER 30 V 10 A | 1 | ≥ 25 V, ≥ 6 A, adjustable current limit; B3 uses 21 V with a 5.9 A limit | **NO — FLAG.** Seller specs only; no datasheet for the limit accuracy or how fast CC mode responds. The 06 §5 limits apply anyway | desertcart.com.sa: [Wanptek 0–30 V 0–10 A](https://www.desertcart.com.sa/products/53072442-dc-power-supply-variable-0-30-v-0-10-wanptek.md); [NICE-POWER 30 V 10 A, ≈ SAR 279](https://www.desertcart.com.sa/products/549931352-nice-power-dc-power-supply-variable-30v-10a-adjustable-switching.md). Amazon.sa: [search KPS3010D](https://www.amazon.sa/-/en/s?k=KPS3010D) |
| B2 | Multimeters ×2, oscilloscope, IR thermometer, safety glasses | lab equipment | — | 06 §2 | — | Borrow from the lab; record model and accuracy |

## C. Missing items from PROJECT_STATE (outside the load)

Buy now (Phase 0 needs them):

| # | Item | Part / spec | Qty | Verified? | Source and link |
|---|---|---|---|---|---|
| C1 | **4.7 kΩ pull-up** for the DS18B20 bus (the 4.7 Ω ordered by mistake is unusable) + the two 10 kΩ pull-downs (A4) | 1 % 1/4 W metal-film kit containing 4.7 kΩ and 10 kΩ (Geekstory 1460 pcs, 73 values, 20 of each) | 1 kit | n/a | Amazon.sa: [Geekstory 1460 pcs kit](https://www.amazon.sa/-/en/Precision-Electronic-Assortment-1ohm-1Mohm-Geekstory/dp/B079FP5WD9). Also any Jeddah shop for a few loose 4.7 kΩ |
| C2 | 12 V mains adapter (gate driver now; pump later) | 12 V 5 A 60 W, UK (Type G) plug, 5.5 × 2.1 mm | 1 | **FLAG:** seller ratings only; check the plug type and the 5 A against the pump when it is chosen | Amazon.sa: [12 V 5 A UK plug](https://www.amazon.sa/-/en/Adaptor-100-240V-Adapter-Monitors-Amplifier/dp/B07LDKZPWP) |
| C3 | ESP32 (only if the team has none) | ESP32-DevKitC-32 (ESP-WROOM-32), 30-pin; GPIO33 and GPIO14 must be on the header | 2 (1 + spare) | Board is a clone; check the module marking | Amazon.sa: [AITRIP 2 pcs ESP-WROOM-32 DevKitC](https://www.amazon.sa/-/en/AITRIP-ESP-WROOM-32-Development-Bluetooth-ESP32-DevKitC-32/dp/B0B82BBKCY) |
| C4 | Enclosure for the DAQ + driver (the resistors and heatsinks stay outside, in shade, downwind) | IP65 ABS/PC, 263 × 182 × 95 mm, clear lid | 1 | **FLAG:** IP65 is seller-stated; the screws are not rust-proof per the listing | Amazon.sa: [LeMotech 263 × 182 × 95 mm](https://www.amazon.sa/-/en/LeMotech-Dustproof-Waterproof-Electrical-Transparent/dp/B07BPQGWC6) |
| C5 | Radiation shield for the BME280 | ventilated multi-plate (Stevenson-type) shield | 1 | **FLAG:** none checked | No listing checked: [Amazon.sa search](https://www.amazon.sa/-/en/s?k=radiation+shield+sensor). Fallback: build one from stacked white plastic plates |
| C6 | Confirm, don't buy yet | BME280, microSD module, terminal blocks — listed as bought but not seen in the order screenshots | — | — | Check the boxes first |

Deferred — **do not buy now** (CURRENT PHASE: cooling and pump come after the paper; the
anemometer is `HAS_ANEMOMETER 0` and awaits approval): pump MOSFET driver, nozzles, pump
+ one spare, tubing, gutter, tank, inline filter, anemometer.

## Local shops (Jeddah) to ask for the rows with no Saudi listing (A3, A6, A7, A8)

Found by search; stock of these exact parts **not confirmed** — phone first with the
part numbers:
- Electronics 2000, Bani Malik, Jeddah — Arduino/ESP32, modules, ICs ([listing](https://magicpin.com/saudi-arabia/Jeddah/Bani-Malik/Autocare/Electronics-2000/store/22955b2))
- ATA Electronics Trading Co., Al Sharafiyah, Jeddah — electronic spare parts ([listing](https://magicpin.com/saudi-arabia/Jeddah/Al-Sharafiyah/Electronics/Ata-Electronics-Trading-Co./store/227a677))
- Scientific Village Store (qariya.net, prices in SAR) — sells loose MOSFETs and capacitors ([example: IRF540](https://qariya.net/en/product/irf540/)); location not confirmed

A local copy of a Panasonic capacitor or a TC4427 is still a copy: the same B0 checks apply.

## Open points for Asim

1. Pick A5 option 1 (Saudi, fast, unverified) or option 2 (Arcol, datasheet, slower).
2. Read the Panasonic FR datasheet's frequency factor and confirm 4 × EEU-FR1V102 ≥ 6 A
   RMS at 20 kHz (A6); decide 3 or 4 parts after B4.
3. Check whether the module leads are MC4 (A12) before buying connectors.
5. ~~Shunt question (A13)~~ — decided 2026-11-13: keep the M5 5 mΩ units, buy nothing.
4. Ask د. عبدالمجيد to review the list for quality and price before ordering (his offer,
   PROJECT_STATE §1).
