# Literature review for the paper

Status: first version, 2026-11-11. Scope: only what the paper needs — fast changes of PV
module temperature, thermal time constants, temperature coefficients measured outdoors,
low-cost monitoring and I-V tracing, and real-time event detection on microcontrollers.
Cooling literature is background only (covered in `docs/literature-research-and-design-decisions.md`,
written before the change of direction).

**How each source was checked (2026-11-11).** Every entry of the reference list was
looked up in the DOI registry (Crossref) and its landing page was opened. The code after
each reference says how much was read:

- `[F]` full text, or the relevant sections, read on the publisher's or an official open copy;
- `[A]` abstract and page metadata read on the publisher's page;
- `[M]` bibliographic record confirmed and the landing page reached, but no abstract or
  text could be read there.

Numbers quoted below come only from text that was actually read. Most papers were read
at abstract level; before the paper is submitted, the works it leans on (marked ★) must
be read in full by the team. Nothing here is `[UNVERIFIED]` except where the tag appears.

## 1. Steady-state models of module temperature

The usual models give module temperature from irradiance, air temperature and wind speed
at steady state. The Sandia model [1] is `T_m = E · exp(a + b·WS) + T_a`, with a = −3.56
and b = −0.075 for an open-rack glass/cell/polymer-sheet module, and
`T_c = T_m + (E/E_0) · ΔT` with ΔT = 3 °C for the same module type; the report gives its
accuracy as about ±5 °C. Faiman [2] showed on seven module types that a modified
Hottel–Whillier–Bliss equation predicts module temperature to within the cell-to-cell
differences inside a module; the pvlib implementation uses the defaults u0 = 25 and
u1 = 6.84 [14]. Skoplaki and Palyvos [13] review the correlations that link efficiency
or power to temperature. Two outdoor studies show how much wind matters: at a test site
in Bolzano, models that include wind predicted cell temperature better than the standard
ones for most technologies [11]; in southern Greece, one year of data from two
mono-crystalline plants gave power temperature coefficients between 0.30 and
0.45 %/°C in absolute value, the lower values for the ventilated free-standing array [12].

## 2. Dynamics: thermal inertia and fast changes

A steady-state model fails when conditions change faster than the module can follow.
Jones and Underwood [3] modelled the module as a non-steady energy balance and found
agreement within 6 K for 95 % of the time under cloudy conditions, with the best
accuracy under clear and overcast skies. Armstrong and Hurley [4] state that during
rapidly changing conditions a steady-state model cannot be justified, and propose an RC
model tested from low to strong winds. Torres Lobera and Valkealahti [5] validated a
dynamic energy-balance model on three summer months of 1-s data. Veldhuis et al. [6]
proposed an empirical model for transient weather at 1-min resolution (Singapore and
Jayapura; RMSE 1.5–3.2 K, 46 % better than similar empirical models). Barry et al. [7]
added an exponential smoothing kernel to a steady-state model; the mean RMSE fell from
3.03 K to 1.58 K and the largest instantaneous error from 20.0 K to 6.6 K. Prilliman et
al. [8] weight and average the steady-state predictions of the previous 20 min, with
weights that depend on wind speed and module unit mass. Hannouch et al. [10] validated a
simplified energy-balance model against three experiments (largest discrepancy 4–16 %),
including a case with passing clouds.

★ Herteleer et al. [9] is the most useful source for us. Using 24 datasets from 15 sites,
at resolutions from 1 s to 1 h, they report a thermal time constant of
**(6.3 ± 1.0) min for fixed-mount PV systems**, and an average error reduction of 1.1 K
(RMSE) and 0.75 K (MAE) against steady-state models. This is the basis of our 10-min
window and of the first-order lag in our cause label.

**What this body of work does not give us.** All of these papers treat a fast
temperature change as something to *model*, so that predicted temperature follows
measured temperature. In what we read, none reports fast temperature rises as *events*:
how often they happen at a site, how large they are, what causes them, and what they
cost in power. None is from western Saudi Arabia. (Read at abstract level for most; see
`09-gap-question-contribution.md` for the wording this allows.)

## 3. Temperature coefficients measured outdoors

★ King, Kratochvil and Boyson [16] describe the outdoor method our γ procedure follows:
shade the module until it is near air temperature, uncover it, and measure I-V curves
while it heats, with the average module temperature taken from several sensors on the
rear surface. They name the two error sources we also budget for: non-uniform
temperature during the transient (wind, frames, junction box), and a back-surface
temperature that does not represent the cell temperature during the transient. Their
table of crystalline-silicon modules gives dV_oc/dT between −0.0038 and −0.0041 per °C
and dI_sc/dT between 0.00016 and 0.00032 per °C.

Paudyal and Imenes [17] estimate coefficients from long-term field data "following IEC
60891" with an added spectral correction: the spread of yearly γ estimates fell from
5–7 % to 1–2 %, and stricter irradiance filters moved β and γ towards the datasheet
values. IEC 60891:2021 [18] is the standard for these corrections; we read its abstract
only, so our paper describes its own procedure and does not claim conformity
(`02-gamma-from-iv.md`).

## 4. Low-cost monitoring and I-V tracing

I-V tracing methods are reviewed in [19] (at least six methods), [20] and [21]. The
review in [21] lists six topologies — variable resistor, capacitive load, electronic
load, bipolar power amplifier, four-quadrant supply, DC-DC converter — and describes the
last as using a converter's duty cycle to emulate a resistor. Low-cost implementations
exist: a fast tracer on an open-source microcontroller platform [22]; and a
relay-switched resistor bank driven by an Arduino Mega, below 100 USD, 45 s per curve,
tested on a 60 W panel with a mean absolute percentage error of 4.40 % against the
manufacturer's data [23]. The authors of [23] note that MOSFET loads need more complex
analog control and large heat sinks.

For monitoring, [24] built a low-cost system on the requirements of IEC 61724 and
compared it with commercial equipment (errors below 2 % for the meteorological and
electrical variables), and [25] built an Arduino data logger that meets the accuracy
requirements of the IEC monitoring standard. IEC 61724-1:2021 [26] defines the classes
of monitoring systems. Reference [27] reviews maximum-power-point tracking methods (at
least 19).

Where our hardware sits: resistors switched by PWM (resistor emulation by duty cycle,
without an inductor), used both to hold the maximum-power point and to sweep the curve.
We did not find this exact arrangement in the papers we read. We do not claim it as new;
it is a design choice that the paper documents and validates.

## 5. Real-time detection on microcontrollers

Sequential change detection starts with Page's CUSUM [29]; methods for time series are
surveyed in [30]. For evaluation, [31] proposed a benchmark for *real-time* anomaly
detectors on streaming data, and [32] extended precision and recall from points to
ranges, which is the family our event-level metrics belong to.

Gradient-boosted trees [33], [34] remain the state of the art on medium-sized tabular
data in a 45-dataset benchmark [35]. Tree models can be made small enough for
microcontrollers: [36] reports a tree-based model that runs in 2 KB of RAM. TinyML as a
field is described in [37]–[39]. Our models are exported to plain C with m2cgen [40].

In PV, the two closest systems we read are different in kind. ★ Novak and Landkamer [43]
use ESP32 nodes as sensors and transmitters; the cooling decision is taken on a
Raspberry Pi from panel temperature, coolant state and weather trends (pressure
gradient, lightning), with 5 s logging over 52 days (mean daily gain 5.84 %, pump
6.0 W). Mellit [44] runs a convolutional network on infrared images on a Raspberry Pi 4
to detect and diagnose module faults (99 % and 95.55 % accuracy). We did not find a
detector of thermal transients that runs on the microcontroller itself and is scored at
event level against simple baselines. The search for this point was short
(one web query and two registry queries) and must be repeated before submission `[OPEN]`.

## 6. Context: why fast temperature rises matter

Cooling studies motivate the topic but are outside the paper. Moharram et al. [41]
cooled panels by water spray in Cairo, starting at 45 °C and cooling to 35 °C; they
report a heating rate of 6 °C/h (0.1 °C/min) and a cooling rate of about 2 °C/min. This
0.1 °C/min is our reference for "normal heating". (The same article also mentions
0.5 °C/min in another place; the team must read which condition each figure refers to
before quoting either.) Bahaidarah et al. [42] cooled the back surface with water in
Dhahran. Both use temperature *level* as the trigger.

## 7. Three sources re-verified on request

| Source | What our documents say | What the source says | Result |
|---|---|---|---|
| King et al. 2004 [1] | cell − back ≈ 3 °C at 1000 W/m², open-rack glass/polymer-sheet | Table: glass/cell/polymer sheet, open rack, ΔT = 3 °C; `T_c = T_m + (E/E_0)·ΔT` | confirmed `[F]` |
| Herteleer et al. 2023 [9] | τ ≈ 6.3 ± 1 min, 24 datasets, 15 sites, *Solar Energy* 2023 | "(6.3±1.0) min for fixed-mount PV systems"; "twenty-four datasets of fifteen sites"; vol. 263, 111889 | confirmed `[A]` |
| Moharram et al. 2013 [41] | start 45 °C, stop 35 °C; heating 6 °C/h = 0.1 °C/min; *Ain Shams Eng. J.* 4(4):869–877 | MAT 45 °C; cooled to 35 °C; "6 °C/h or 0.1 °C/min"; same citation | confirmed `[F]`; see the note on 0.5 °C/min in §6 |

Also confirmed while checking: Novak and Landkamer [43] (ESP32 + Raspberry Pi + MQTT,
6.0 W pump, 5 s logging, 52 days, 5.84 %, ROI_E = 1.07 on representative high-irradiance
days), and the pvlib Faiman defaults [14].

## 8. Not verified, or dropped

- Fractional open-circuit-voltage factor (0.78 in the firmware spec): the abstract of
  [27] does not give it. It stays `[ASSUMPTION]` there.
- Dropped because their pages were not opened in this stage: Basseville and Nikiforov
  (book on change detection), emlearn, Koehl et al. 2011, Dupré et al. 2015, Dubey et
  al. 2013, Sarikh et al. 2020, Kurtz et al. 2011. They may be added after someone opens
  them.
- The Saudi soiling papers and the cooling prior art listed in the August literature
  document were not re-opened; they are not cited in the paper draft until they are.

## References

Thermal models and dynamics
1. D. L. King, W. E. Boyson, J. A. Kratochvil, *Photovoltaic Array Performance Model*, Sandia report SAND2004-3535, 2004. doi:10.2172/919131 `[F]`
2. D. Faiman, "Assessing the outdoor operating temperature of photovoltaic modules," *Prog. Photovolt: Res. Appl.* 16(4):307–315, 2008. doi:10.1002/pip.813 `[A]`
3. A. D. Jones, C. P. Underwood, "A thermal model for photovoltaic systems," *Solar Energy* 70(4):349–359, 2001. doi:10.1016/S0038-092X(00)00149-3 `[A]`
4. S. Armstrong, W. G. Hurley, "A thermal model for photovoltaic panels under varying atmospheric conditions," *Applied Thermal Engineering* 30(11–12):1488–1495, 2010. doi:10.1016/j.applthermaleng.2010.03.012 `[A]`
5. D. Torres Lobera, S. Valkealahti, "Dynamic thermal model of solar PV systems under varying climatic conditions," *Solar Energy* 93:183–194, 2013. doi:10.1016/j.solener.2013.03.028 `[A]`
6. A. J. Veldhuis, A. M. Nobre, I. M. Peters, T. Reindl, R. Rüther, A. H. M. E. Reinders, "An empirical model for rack-mounted PV module temperatures for Southeast Asian locations evaluated for minute time scales," *IEEE J. Photovoltaics* 5(3):774–782, 2015. doi:10.1109/JPHOTOV.2015.2405762 `[A]`
7. J. Barry, D. Böttcher, K. Pfeilsticker, A. Herman-Czezuch, N. Kimiaie, S. Meilinger, C. Schirrmeister, H. Deneke, J. Witthuhn, F. Gödde, "Dynamic model of photovoltaic module temperature as a function of atmospheric conditions," *Adv. Sci. Res.* 17:165–173, 2020. doi:10.5194/asr-17-165-2020 `[F]`
8. M. Prilliman, J. S. Stein, D. Riley, G. Tamizhmani, "Transient weighted moving-average model of photovoltaic module back-surface temperature," *IEEE J. Photovoltaics* 10(4):1053–1060, 2020. doi:10.1109/JPHOTOV.2020.2992351 `[A]`
9. B. Herteleer, A. Kladas, G. Chowdhury, F. Catthoor, J. Cappelle, "Investigating methods to improve photovoltaic thermal models at second-to-minute timescales," *Solar Energy* 263:111889, 2023. doi:10.1016/j.solener.2023.111889 `[A]` ★
10. A. Hannouch, J. Faraj, R. Taher, M. Mortazavi, M. Khaled, "A simplified approach to modeling temperature dynamics in photovoltaic systems – Validation, case studies, and parametric analysis," *Int. J. Thermofluids* 23:100767, 2024. doi:10.1016/j.ijft.2024.100767 `[A]`
11. C. Schwingshackl, M. Petitta, J. E. Wagner, G. Belluardo, D. Moser, M. Castelli, M. Zebisch, A. Tetzlaff, "Wind effect on PV module temperature: Analysis of different techniques for an accurate estimation," *Energy Procedia* 40:77–86, 2013. doi:10.1016/j.egypro.2013.08.010 `[A]`
12. J. K. Kaldellis, M. Kapsali, K. A. Kavadias, "Temperature and wind speed impact on the efficiency of PV installations. Experience obtained from outdoor measurements in Greece," *Renewable Energy* 66:612–624, 2014. doi:10.1016/j.renene.2013.12.041 `[A]`
13. E. Skoplaki, J. A. Palyvos, "On the temperature dependence of photovoltaic module electrical performance: A review of efficiency/power correlations," *Solar Energy* 83(5):614–624, 2009. doi:10.1016/j.solener.2008.10.008 `[A]`
14. K. S. Anderson, C. W. Hansen, W. F. Holmgren, A. R. Jensen, M. A. Mikofski, A. Driesse, "pvlib python: 2023 project update," *J. Open Source Software* 8(92):5994, 2023. doi:10.21105/joss.05994 `[M]`; and pvlib 0.16.1 documentation, `pvlib.temperature.faiman` (defaults u0 = 25.0, u1 = 6.84) `[F]`
15. P. Ineichen, R. Perez, "A new airmass independent formulation for the Linke turbidity coefficient," *Solar Energy* 73(3):151–157, 2002. doi:10.1016/S0038-092X(02)00045-2 `[M]`

Temperature coefficients
16. D. L. King, J. A. Kratochvil, W. E. Boyson, "Temperature coefficients for PV modules and arrays: measurement methods, difficulties, and results," *Conf. Record 26th IEEE Photovoltaic Specialists Conference*, 1997, pp. 1183–1186. doi:10.1109/PVSC.1997.654300 `[F]` (open copy: UNT Digital Library / OSTI 548687) ★
17. B. R. Paudyal, A. G. Imenes, "Investigation of temperature coefficients of PV modules through field measured data," *Solar Energy* 224:425–439, 2021. doi:10.1016/j.solener.2021.06.013 `[F]`
18. IEC 60891:2021, Edition 3.0, *Photovoltaic devices — Procedures for temperature and irradiance corrections to measured I-V characteristics*. https://webstore.iec.ch/en/publication/61766 `[A]` (abstract only)

Monitoring and I-V tracing
19. E. Durán, M. Piliougine, M. Sidrach-de-Cardona, J. Galán, J. M. Andújar, "Different methods to obtain the I–V curve of PV modules: A review," *33rd IEEE Photovoltaic Specialists Conference*, 2008. doi:10.1109/PVSC.2008.4922578 `[A]`
20. Y. Zhu, W. Xiao, "A comprehensive review of topologies for photovoltaic I–V curve tracer," *Solar Energy* 196:346–357, 2020. doi:10.1016/j.solener.2019.12.020 `[A]`
21. J. I. Morales-Aragonés, M. Dávila-Sacoto, L. G. González, V. Alonso-Gómez, S. Gallardo-Saavedra, L. Hernández-Callejo, "A review of I–V tracers for photovoltaic modules: Topologies and challenges," *Electronics* 10(11):1283, 2021. doi:10.3390/electronics10111283 `[F]`
22. P. Papageorgas, D. Piromalis, T. Valavanis, S. Kambasis, T. Iliopoulou, G. Vokas, "A low-cost and fast PV I-V curve tracer based on an open source platform with M2M communication capabilities for preventive monitoring," *Energy Procedia* 74:423–438, 2015. doi:10.1016/j.egypro.2015.07.641 `[A]`
23. P. L. O. Machado, L. V. G. Fachini, E. T. Tiuman, T. M. Barchi, S. L. Stevan Jr., H. V. Siqueira, R. M. Szmoski, T. Antonini Alves, "A low-cost Arduino-based I–V curve tracer with automated load switching for PV panel characterization," *Applied Sciences* 15(15):8186, 2025. doi:10.3390/app15158186 `[F]`
24. J. M. Paredes-Parra, A. Mateo-Aroca, G. Silvente-Niñirola, M. C. Bueso, Á. Molina-García, "PV module monitoring system based on low-cost solutions: Wireless Raspberry application and assessment," *Energies* 11(11):3051, 2018. doi:10.3390/en11113051 `[F]`
25. A. López-Vargas, M. Fuentes, M. Vivar García, F. J. Muñoz-Rodríguez, "Low-cost datalogger intended for remote monitoring of solar photovoltaic standalone systems based on Arduino," *IEEE Sensors J.* 19(11):4308–4320, 2019. doi:10.1109/JSEN.2019.2898667 `[A]`
26. IEC 61724-1:2021, Edition 2.0, *Photovoltaic system performance — Part 1: Monitoring*. https://webstore.iec.ch/en/publication/65561 `[A]` (abstract only)
27. T. Esram, P. L. Chapman, "Comparison of photovoltaic array maximum power point tracking techniques," *IEEE Trans. Energy Conversion* 22(2):439–449, 2007. doi:10.1109/TEC.2006.874230 `[A]`
28. JCGM 100:2008, *Evaluation of measurement data — Guide to the expression of uncertainty in measurement*. doi:10.59161/JCGM100-2008E `[M]`

Detection and embedded machine learning
29. E. S. Page, "Continuous inspection schemes," *Biometrika* 41(1–2):100–115, 1954. doi:10.1093/biomet/41.1-2.100 `[M]`
30. S. Aminikhanghahi, D. J. Cook, "A survey of methods for time series change point detection," *Knowledge and Information Systems* 51(2):339–367, 2017. doi:10.1007/s10115-016-0987-z `[A]`
31. A. Lavin, S. Ahmad, "Evaluating real-time anomaly detection algorithms — the Numenta Anomaly Benchmark," *IEEE 14th Int. Conf. Machine Learning and Applications (ICMLA)*, 2015, pp. 38–44. doi:10.1109/ICMLA.2015.141; arXiv:1510.03336 `[A]`
32. N. Tatbul, T. J. Lee, S. Zdonik, M. Alam, J. Gottschlich, "Precision and recall for time series," arXiv:1803.03639, 2018 `[A]` (cited as the preprint that was opened)
33. T. Chen, C. Guestrin, "XGBoost: A scalable tree boosting system," *Proc. 22nd ACM SIGKDD*, 2016, pp. 785–794. doi:10.1145/2939672.2939785; arXiv:1603.02754 `[A]`
34. G. Ke, Q. Meng, T. Finley, T. Wang, W. Chen, W. Ma, Q. Ye, T.-Y. Liu, "LightGBM: A highly efficient gradient boosting decision tree," *Advances in Neural Information Processing Systems* 30, 2017 `[M]` (proceedings page)
35. L. Grinsztajn, E. Oyallon, G. Varoquaux, "Why do tree-based models still outperform deep learning on tabular data?" arXiv:2207.08815, 2022 `[A]`
36. A. Kumar, S. Goyal, M. Varma, "Resource-efficient machine learning in 2 KB RAM for the Internet of Things," *Proc. 34th Int. Conf. Machine Learning*, PMLR 70:1935–1944, 2017 `[A]`
37. C. Banbury et al., "MLPerf Tiny Benchmark," arXiv:2106.07597, 2021 `[A]`
38. R. David et al., "TensorFlow Lite Micro: Embedded machine learning on TinyML systems," arXiv:2010.08678, 2020 `[A]`
39. P. P. Ray, "A review on TinyML: State-of-the-art and prospects," *J. King Saud Univ. – Computer and Information Sciences* 34(4):1595–1623, 2022. doi:10.1016/j.jksuci.2021.11.019 `[A]`
40. m2cgen (Model 2 Code Generator), software, https://github.com/BayesWitnesses/m2cgen `[M]` (version 0.10.0 pinned in `requirements.txt`)

PV cooling and edge systems (context and closest applications)
41. K. A. Moharram, M. S. Abd-Elhady, H. A. Kandil, H. El-Sherif, "Enhancing the performance of photovoltaic panels by water cooling," *Ain Shams Engineering Journal* 4(4):869–877, 2013. doi:10.1016/j.asej.2013.03.005 `[F]`
42. H. Bahaidarah, A. Subhan, P. Gandhidasan, S. Rehman, "Performance evaluation of a PV (photovoltaic) module by back surface water cooling for hot climatic conditions," *Energy* 59:445–453, 2013. doi:10.1016/j.energy.2013.07.050 `[A]`
43. M. Novak, L. Landkamer, "Energy-aware IoT architecture for active cooling of photovoltaic panels under dynamic weather conditions," *Energy Conversion and Management: X* 30:101743, 2026. doi:10.1016/j.ecmx.2026.101743 `[F]` ★
44. A. Mellit, "An embedded solution for fault detection and diagnosis of photovoltaic modules using thermographic images and deep convolutional neural networks," *Engineering Applications of Artificial Intelligence* 116:105459, 2022. doi:10.1016/j.engappai.2022.105459 `[A]`

For [37] and [38] only the first author is listed here; the full author lists are on the
arXiv pages and must be written out in the paper's bibliography.
