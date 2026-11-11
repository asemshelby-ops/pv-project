# Research gap, research question and contribution

Status: proposal for the supervisor, 2026-11-11. Reference numbers are those of
`08-literature-review.md`. Everything here is the team's wording, not the supervisor's,
until he confirms it.

## 1. The gap, in three sentences

1. Fast changes of module temperature are well studied as a *modelling* problem:
   dynamic thermal models reduce the error of predicted temperature at second-to-minute
   scales [3]–[10], and the thermal time constant of fixed-mount systems is known to be
   about 6 minutes [9].
2. In the literature we read, these fast rises are not described as *events*: we found
   no count of how often they occur at a site, how large they are, whether irradiance or
   something else drives them, and what they cost in power; and we found no such
   measurements for a module in western Saudi Arabia.
3. Low-cost PV monitoring [24], [25], low-cost I-V tracing [21]–[23] and small tree
   models on microcontrollers [36] each exist, but we did not find them combined to
   detect thermal transients on the microcontroller itself, scored event by event
   against simple baselines. The closest systems decide on a single-board computer from
   a temperature level [43] or classify infrared images [44].

**Limits of this statement.** The search was short and most papers were read at abstract
level. "We did not find" is the honest form; "does not exist" is not. Before submission:
read [9], [16] and [43] in full, and repeat the search for point 3.

## 2. Research question

> How often do thermal spikes — rises of module temperature of at least 3 °C within
> 10 minutes — occur on a fixed 100 W module in Makkah during the measurement period,
> what drives them, what do they cost in power, and how early can a microcontroller
> detect them?

| | Sub-question | Answered by | Needs |
|---|---|---|---|
| RQ1 | How frequent and how large are spikes, and what share is irradiance-driven? | event table from temperature and irradiance (`03` §2–§5) | temperatures, irradiance |
| RQ2 | What is the power cost of a spike? | `γ · ΔT_cell`, with γ measured from I-V sweeps (`02`) | the PWM load and sweeps |
| RQ3 | How do three on-device detectors compare: a temperature-rise threshold, CUSUM, and gradient-boosted trees (NASIM)? | event-level precision, recall, delay, false alarms per day, with day-bootstrap intervals (`03` §6) | ≥ 50 + 20 labelled events |

RQ1 and RQ3 need only temperatures and irradiance. RQ2 needs the load. So logging can
start before the load exists; those days are marked `load_mode = OFF` and are treated as
pilot data, because a module at open circuit runs hotter than a loaded one `[EST]`.

## 3. Contribution statement (modest)

> To the authors' knowledge, this is the first report that (i) characterises fast
> temperature rises of a PV module as discrete events — their frequency, size, cause
> and power cost — from 10-s field measurements in Makkah, and (ii) compares simple and
> learned detectors of these events running in real time on a low-cost microcontroller.

Three concrete deliverables behind that sentence:

- **C1 — a validated low-cost instrument.** ESP32 logger with five back-sheet sensors,
  an in-plane silicon pyranometer and a two-branch PWM resistor load that both holds
  the maximum-power point and sweeps the I-V curve; an uncertainty budget (`01`); a
  comparison day against a commercial data logger (`04`).
- **C2 — a characterisation.** A labelled event set for one module over stated dates:
  spikes per day, size and duration distributions, share that is irradiance-driven, and
  power cost using a temperature coefficient measured on this module by the outdoor
  transient method of [16].
- **C3 — a detector comparison on the device.** Threshold, CUSUM and a small
  gradient-boosted-tree model, each running on the ESP32, scored at event level, with
  the agreement between the device and the offline computation reported.

## 4. What the paper does not claim

| Not claimed | Why |
|---|---|
| "Frequency of spikes in Makkah's climate" | one module, one site, a few weeks of one season; the paper states its dates |
| That cooling pays, or any cooling result | cooling is deferred; [41]–[43] are context only |
| A new thermal model | we use a first-order lag with the time constant of [9] |
| A new I-V tracer topology | resistor emulation by duty cycle is known [21]; our arrangement is documented, not claimed as novel |
| That the learned detector is better | whichever result comes out is reported; a tie with the threshold rule is a result |
| A wind-driven cause | there is no anemometer; the label is "irradiance-driven or not" |
| A datasheet comparison for γ | the module has no published coefficients; we compare with field ranges such as [12] and [16] |
| Conformity with IEC 60891 or IEC 61724-1 | only their abstracts were read ([18], [26]) |

## 5. Every outcome is reportable

| If the data show … | The paper says … |
|---|---|
| Natural spikes are rare | the rate with its interval, and that controlled tests supplied the detection events; rarity is itself the RQ1 answer |
| Most spikes are irradiance-driven | cloud and haze edges dominate at this site and season; a cheap irradiance feature explains most events |
| The threshold rule matches NASIM | on-device learning is not needed for this task at this site; delay and false-alarm numbers show by how much |
| NASIM is earlier at equal false alarms | the lead time in minutes and the share of the rise still to come at the alarm |
| γ runs disagree | the spread, the suspected term from the budget, and the power cost as a range |
| Fewer than 50 + 20 events | threshold and CUSUM as the main result, NASIM as exploratory (`03` §6) |

## 6. Main threats to validity (to be written into the paper)

1. One module, one site, a short season: no generalisation.
2. Silicon pyranometer with a stated accuracy of 5 %: irradiance is used for steps and
   ratios, never as an absolute reference.
3. Back-sheet sensors, not cell temperature: offset from [1], transient lag named in [16].
4. Controlled events (mesh, windbreak) are not natural events: reported separately.
5. Reference labels and the threshold detector share one signal: handled by the
   hindsight-versus-causal design of `03` §1, and stated openly.
6. Cause without wind measurement: only "irradiance-driven or not".
7. Module at open circuit before the load is fitted: pilot data only.

## 7. Working titles

1. *Thermal Spikes on a PV Module in Makkah: Field Characterisation and Real-Time
   Detection on a Microcontroller*
2. *Event-Level Characterisation and On-Device Detection of Fast Temperature Rises in a
   PV Module Under Hot-Dry Conditions*
3. *How Often, How Large, How Early: Thermal Spikes of a PV Module Measured and Detected
   with a Low-Cost ESP32 Instrument*

The project title (*AI-Driven Smart Cooling for PV Panels …*) stays the title of the
graduation project. The paper is its first, narrower part; the supervisor chooses the
paper's title.

## 8. For the supervisor to confirm

1. The research question and the three sub-questions (§2).
2. The contribution sentence (§3) and the list of non-claims (§4).
3. θ = 3 °C in 10 min as the event definition for the paper (`03` §2).
4. The venue and therefore the page limit (`10-venues.md`).
