"""All parameters of the synthetic pipeline, in one place.

Every value carries its source tag:
  [CLAUDE.md]  team simulation parameter recorded in CLAUDE.md (illustrative)
  [LIT]        published literature (source given)
  [DS]         datasheet / seller listing
  [EST]        first-pass engineering estimate, to be replaced by measurement
  [ASSUMPTION] chosen for the synthetic data only; not a property of our hardware
  [PLACEHOLDER] NOT KNOWN YET (no part chosen, nothing measured): a stand-in so that the
               code compiles and the synthetic tests run. Only in the Phase B block at the
               end of this file. Replace it there, nowhere else.

Nothing here is a measured property of the BANINUAN 100 W module.
"""

# --- Sampling -------------------------------------------------------------
DT_S = 10.0                 # [CLAUDE.md] one CSV row every 10 s

# --- Thermal model (sim/thermal.py) ---------------------------------------
AREA_M2 = 0.648             # [DS] listing 1200 x 540 mm -> 0.648 m2
HEAT_CAPACITY_J_K = 8700.0  # [EST] C ~ tau*U*A ~ 8.7 kJ/K (CLAUDE.md, Key physics)
FAIMAN_U0 = 25.0            # [LIT] pvlib Faiman default, W/(m2 K)
FAIMAN_U1 = 6.84            # [LIT] pvlib Faiman default, W s/(m3 K)

# --- Site and calendar (data/synth.py) ------------------------------------
LAT, LON = 21.42, 39.83     # Makkah (PROJECT_STATE sec.7)
ALTITUDE_M = 277            # [ASSUMPTION] approximate site altitude
TZ = "Asia/Riyadh"          # UTC+3, no daylight saving
TILT_DEG = 20.0             # [CLAUDE.md] workshop stand fixed at 20 deg (2026-11-10);
                            # re-measure the real tilt at installation
AZIMUTH_DEG = 180.0         # [ASSUMPTION] due south; measure at installation
DAY_START, DAY_END = "06:00:00", "18:00:00"   # logged window, local time
START_DATE = "2026-11-15"   # [ASSUMPTION] first synthetic day (test season)
N_DAYS = 60                 # [ASSUMPTION] -> 36 / 12 / 12 days train/val/test

# --- Irradiance: clear sky x haze x cloud transients ----------------------
# Clear sky: pvlib Ineichen + Linke climatology, isotropic transposition.
DAY_CLEARNESS = (0.85, 1.00)   # [ASSUMPTION] daily haze factor, uniform range
CLOUDS_PER_DAY = 1.5           # [ASSUMPTION] Poisson mean; set above Makkah's mostly
                               # clear skies so the test set has enough hard negatives
CLOUD_DEPTH = (0.3, 0.8)       # [ASSUMPTION] fraction of irradiance removed
CLOUD_DURATION_MIN = (5, 20)   # [ASSUMPTION]
CLOUD_RAMP_S = 60              # [ASSUMPTION] edge ramp of a cloud
RISE_WINDOW_MIN = 15           # [ASSUMPTION] after a cloud ends, the next 15 min
                               # are labelled "irradiance_rise" (~2.4 tau)
EVENT_HOURS = ("08:00:00", "16:00:00")  # [ASSUMPTION] window for event starts

# --- Wind: diurnal mean + slow random variation + injected lulls ----------
WIND_MEAN = 2.0             # [ASSUMPTION] m/s, daily mean of the base wind
WIND_DIURNAL_AMP = 0.5      # [ASSUMPTION] m/s, peak at 12:00
WIND_OU_SIGMA = 0.3         # [ASSUMPTION] m/s, slow variation (Ornstein-Uhlenbeck)
WIND_OU_TAU_S = 300.0       # [ASSUMPTION] s, its correlation time
WIND_MIN = 0.2              # [ASSUMPTION] m/s, floor of the true wind
LULLS_PER_DAY = (2, 4)      # [ASSUMPTION] integer range, inclusive
LULL_DROP = (0.6, 1.8)      # [ASSUMPTION] m/s drop below the base wind
LULL_DURATION_MIN = (15, 25)  # [ASSUMPTION]; CLAUDE.md spike scenario: 20 min
LULL_RAMP_S = 60            # [ASSUMPTION] wind ramps down/up over 1 min
EVENT_GAP_MIN = 30          # [ASSUMPTION] minimum gap between two lulls

# --- Labels (independent of every detector's settings) --------------------
SPIKE_EFFECT_C = 3.0        # [SUPERVISOR] 3-4 degC: a lull is a "spike" when its
                            # noise-free steady-state effect >= 3 degC, else
                            # "minor_lull" (neutral in scoring)
# Natural episodes: the background wind (without injected lulls) is compared
# with its slow diurnal baseline using the same steady-state rule. The lull
# window is unknown, so the effect is averaged over sliding windows as long as
# the shortest injected lull.
NATURAL_WINDOW_MIN = 15     # = LULL_DURATION_MIN[0]
NATURAL_MINOR_C = 2.0       # [ASSUMPTION] floor for a neutral "natural_minor":
                            # without a floor, every below-average gust gap would
                            # become a neutral window and hide false alarms

# --- Ambient air -----------------------------------------------------------
TA_DAY_MEAN = (24.0, 28.0)  # [ASSUMPTION] degC, rough Makkah winter, not climatology
TA_AMP = 5.0                # [ASSUMPTION] degC, half the daily swing, peak 15:00
RH_MEAN, RH_AMP = 45.0, 15.0  # [ASSUMPTION] %, minimum at 15:00

# --- PV electrical model (synthetic only) ---------------------------------
P_STC_W = 100.0             # [DS] listing Pmax
VMP_STC_V = 18.0            # [DS] listing Vmp
GAMMA_P = -0.004            # [EST] illustrative -0.4 %/degC, NOT this module's value
BETA_VMP = -0.0035          # [ASSUMPTION] Vmp temperature coefficient, 1/degC
VMP_LOG_G = 0.05            # [ASSUMPTION] Vmp change per ln(G/1000)
CELL_MINUS_BACK_C = 3.0     # [LIT] cell - back-sheet ~3 degC at 1000 W/m2
                            # (Sandia, King et al. 2004), scaled with G/1000

# --- Sensors (noise values are assumptions, not datasheet specs) ----------
N_BACK_SENSORS = 5
DS18B20_LSB_C = 0.0625      # [DS] 12-bit resolution
DS18B20_NOISE_C = 0.05      # [ASSUMPTION]
DS18B20_OFFSET_C = 0.2      # [ASSUMPTION] std of fixed per-sensor offset (before
                            # the water-bath cross-calibration)
TA_NOISE_C = 0.1            # [ASSUMPTION]
RH_NOISE = 1.0              # [ASSUMPTION] %
G_NOISE_W = 2.0             # [ASSUMPTION] W/m2; reading rounded to 1 W/m2 [DS]
WIND_NOISE = 0.2            # [ASSUMPTION] m/s; rounded to 0.1 m/s (spec target)
V_NOISE = 0.01              # [ASSUMPTION] V
I_NOISE = 0.003             # [ASSUMPTION] A

# --- Detectors (detect/) ---------------------------------------------------
# Shared signal: module temperature = mean of T1..T5, then a 3-sample median.
MEDIAN_N = 3                # [CLAUDE.md] 3-sample median filter
DET_WINDOW_MIN = 10         # [CLAUDE.md] rise window of the threshold rule
SILENCE_MIN = 10            # [CLAUDE.md] no new alarm for 10 min after one
G_FLOOR = 50.0              # [ASSUMPTION] W/m2, guard for divisions by irradiance

# (a) Threshold rule -- fixed team parameters, NOT tuned on our data
THR_RISE_C = 2.5            # [CLAUDE.md] rise >= 2.5 degC within 10 min
THR_GATE = 0.05             # [CLAUDE.md] irradiance gate 5 %

# (b) CUSUM on the thermal-model residual (k, h tuned on validation in Step 4)
CUSUM_WIND_CONST = 1.5      # [ASSUMPTION] m/s, model wind when no anemometer
CUSUM_WIND_TAU_MIN = 60.0   # [ASSUMPTION] slow wind baseline (EWMA) time constant
CUSUM_RES_TAU_MIN = 30.0    # [ASSUMPTION] slow residual baseline (EWMA) time constant
CUSUM_K = 0.5               # [ASSUMPTION] degC, drift allowance per sample
CUSUM_H = 20.0              # [ASSUMPTION] degC-samples, alarm level

# (c) Gradient-boosted trees (LightGBM); p threshold tuned on validation in Step 4
GBT_USE_WIND = True         # one flag: with / without the anemometer features
GBT_N_ESTIMATORS = 50       # size budget: n_estimators x depth kept small
GBT_MAX_DEPTH = 4
GBT_NUM_LEAVES = 15         # <= 2**MAX_DEPTH
GBT_LEARNING_RATE = 0.1
GBT_MIN_CHILD = 50          # min samples per leaf: no tiny, noise-fitting leaves
GBT_POS_WINDOW_MIN = 10     # training target: first 10 min of each spike
GBT_P_THRESHOLD = 0.5       # [ASSUMPTION] default, tuned in Step 4
# NASIM-GBT current version (decided 2026-11-04 after seeds 0-4; judged on 5-9):
GBT_HYBRID = True           # add CUSUM residual d and statistic S as features
HYBRID_CUSUM_K = 0.2        # [ASSUMPTION] k of the feature CUSUM (not tuned; the
                            # trees set their own thresholds on S)
GBT_CLASS_WEIGHT = "sqrt"   # positive weight sqrt(n_neg/n_pos); version 1: "full"
GBT_PERSIST = 2             # alarm after 2 consecutive samples >= p; version 1: 1
HYBRID_MISMATCH = dict(c_frac=+0.2, u0_frac=-0.2, u1_frac=-0.2)
                            # fixed a-priori +/-20 % model for the hybrid's
                            # mismatch row: the worst case of CUSUM on seeds 0-4

# Simple clear-sky model for the clear-sky index ON THE ESP32 (features only;
# the synthetic data itself uses pvlib Ineichen). Meinel & Meinel DNI with
# Kasten & Young air mass; diffuse taken as a fixed fraction of DNI.
CS_DIFFUSE_FRAC = 0.1       # [ASSUMPTION] clear-sky diffuse / DNI

# --- Evaluation (eval/) ----------------------------------------------------
SPLIT_DAYS = (36, 12, 12)   # chronological train / validation / test, whole days
MISMATCH_FRAC = 0.20        # [ASSUMPTION] CUSUM model-mismatch study: C, u0, u1
                            # each off by +/-20 % (all 8 sign combinations)
SEEDS = (5, 6, 7, 8, 9)     # evaluation seeds. Seeds 0-4 are BURNED: their test
                            # results were seen while designing NASIM-GBT's
                            # current version, so it is judged on 5-9 only

# --- "Hard" synthetic variant (all values [ASSUMPTION]) ---------------------
# Off by default. Removes the easy giveaways of the base data.
HARD_VARIANT = False
HARD_N_DAYS = 150                 # >= 50 training spikes per seed (Phase 1 target)
HARD_SPLIT_DAYS = (90, 30, 30)    # train / validation / test
HARD_LULL_DROP = (0.7, 1.6)       # m/s drop below the base wind ...
HARD_LULL_WIND_FLOOR = 1.6        # ... but the lull wind never goes below 1.6 m/s, the
                                  # bottom edge of the background: 1st percentile of
                                  # the 2-min-mean background wind 08-16 h = 1.68 m/s
                                  # (seed 0). In the base data lulls reach 0.2 m/s.
HARD_LULLS_PER_DAY = (4, 6)       # more lulls, because few of them now reach 3 degC
HARD_DS18B20_NOISE_C = 0.15       # degC, 3x the base sensor noise
HARD_SENSOR_DRIFT_C = 0.3         # degC, slow per-sensor drift (OU) ...
HARD_SENSOR_DRIFT_TAU_MIN = 60.0  # ... with a 60-min correlation time
HARD_TA_NOISE_C = 0.3
HARD_G_NOISE_W = 5.0
HARD_WIND_NOISE = 0.4
# Non-wind causes of small temperature rises:
HARD_HAZE_PER_DAY = 1.0           # Poisson mean: slow haze dips of the irradiance
HARD_HAZE_DEPTH = (0.05, 0.15)    # fraction removed
HARD_HAZE_DURATION_MIN = (20, 60) # fades in over the first and out over the last
                                  # quarter; the fade-out + 15 min = irradiance_rise
HARD_TA_STEPS_PER_DAY = 0.7       # Poisson mean: warm-air steps of the ambient
HARD_TA_STEP_C = (1.0, 3.0)       # degC
HARD_TA_STEP_RAMP_MIN = 5
HARD_TA_STEP_HOLD_MIN = (30, 90)  # the step's rise + 15 min = ambient_rise, neutral:
                                  # a real module rise, but neither wind nor irradiance

# --- Export (export/) ----------------------------------------------------------
EXPORT_VARIANT = "hard"     # models exported to C: hard variant, first evaluation
EXPORT_SEED = 5             # seed; parity test on that run's held-out test days

# --- Paper analysis (analysis/): spike definition of docs/paper/03 ------------------------
SPIKE_THETA_C = 3.0         # [SUPERVISOR] rise of about 3-4 degC; lower end
SPIKE_WINDOW_MIN = 10       # [ASSUMPTION] ~1.6 thermal time constants (tau 6.3 min [LIT])
SPIKE_MINOR_C = 2.0         # [ASSUMPTION] minor rise: 2 <= rise < 3 degC, neutral in scoring
SPIKE_PEAK_HOLD_MIN = 5     # [ASSUMPTION] a peak must not be exceeded for the next 5 min
SPIKE_MIN_COS_ZENITH = 0.2  # [ASSUMPTION] sun elevation above ~11.5 deg
CAUSE_U_EFF = 35.0          # [EST] W/(m2 K), Faiman defaults at 1.5 m/s; refit on real data
CAUSE_TAU_MIN = 6.3         # [LIT] Herteleer et al. 2023, fixed-mount systems
CAUSE_F_IRRADIANCE = 2 / 3  # [ASSUMPTION] explained fraction >= 2/3 -> "irradiance"
CAUSE_F_NON = 1 / 3         # [ASSUMPTION] explained fraction <= 1/3 -> "non_irradiance"
MATCH_GRACE_MIN = 2         # [ASSUMPTION] an alarm up to 2 min after the peak still matches
CLEANING_EXCLUDE_MIN = 30   # [ASSUMPTION] excluded after a logged cleaning
# Soiling housekeeping check (analysis/soiling.py, docs/paper/05 sec. 9). Not a result.
SOIL_NOON_WINDOW = ("11:30", "12:30")   # [ASSUMPTION] local time, around solar noon in Makkah
SOIL_MIN_G_WM2 = 600.0      # [ASSUMPTION] midday rows below this are not used (cloud, haze)
SOIL_MIN_POWER_W = 1.0      # rows without real power (load off) are not used
SOIL_MIN_ROWS = 60          # [ASSUMPTION] at least 10 min of such rows in the window
SOIL_CHECK_REL = 0.03       # [ASSUMPTION] a change of P/G above 3 % across a cleaning is
                            # reported as "check" (temperature alone can give ~2 % [EST])
# A detector skips a row it cannot use (input or time missing, or the row is absent) and
# keeps its state; this many such rows in a row reset it. ONE value for the board
# (NASIM_DET_RESET_AFTER_FAILS in nasim_config.h, used by firmware/logger) and for the
# offline segments (analysis/ingest.py). 5 rows = 50 s; up to 4 are bridged.
DET_RESET_AFTER_FAILS = 5   # [ASSUMPTION] Asim, 2026-11-12
# Reviewer checks (docs/paper/12 sec. 3), all [ASSUMPTION]:
G_STEP_GRID = (0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0)  # relative rise of G in 2 min
MATCH_FA_PER_DAY = (0.05, 0.1, 0.2, 0.5)   # false-alarm budgets at which detectors are compared
MATCH_MIN_RECALL = 0.9                     # a setting must still find 90 % of the validation spikes
# Acceptance criteria for the detector claim, fixed before any field data
# (docs/paper/03 sec. 10). Approved provisionally by Asim 2026-11-12; the supervisor
# confirms them before the first field day. After that: frozen, a change is a deviation.
PREREG_BUDGET_FA_PER_DAY = 0.2     # primary false-alarm budget (must be in MATCH_FA_PER_DAY)
PREREG_MAX_FA_PER_DAY = 0.2        # C1
PREREG_MIN_RECALL = 0.90           # C2
PREREG_MIN_REMAINING = 0.50        # C3b: share of the rise still to come at the alarm
PREREG_MIN_TRAIN_SPIKES = 50       # precondition (03 sec. 6)
PREREG_MIN_TEST_SPIKES = 20        # precondition
PREREG_MIN_TEST_NON_IRR = 8        # precondition: test spikes labelled non_irradiance or mixed
CAUSE_SENSITIVITY = ((0.25, 0.75), (1 / 3, 2 / 3), (0.4, 0.6), (0.5, 0.5))  # (non, irradiance) limits
PAPER_SPLIT = (0.6, 0.2, 0.2)   # chronological train / validation / test, whole local days
CELL_BACK_DT_C = CELL_MINUS_BACK_C   # same Sandia offset, used by the gamma analysis
# I-V sweeps taken below this in-plane irradiance (mean of G_before and G_after) are not
# used for anything computed from sweeps: the gamma fit, and through gamma the power cost.
# Reason (docs/paper/07 sec. 12): in the simulation of the load firmware [EST, 2000 uF] a
# sweep is settled to within 0.3 % of V_oc at >= 200 W/m2; below that the points can be
# further off (0.55 % at 100 W/m2) without the NOT_SETTLED flag being set, and at <= 90
# W/m2 the sweep is flagged. [ASSUMPTION] until bench B4 gives the real time constant.
SWEEP_MIN_G_WM2 = 200.0

# ==============================================================================================
# PHASE B PREPARATION (2026-11-17). SOFTWARE ONLY: no pump, flow sensor or tank exists yet.
#
# Phase B = the randomized spray trial that follows the paper: on a NASIM alarm the logger
# opens a "spike event" and draws spray / no-spray; the analysis compares the two arms.
# Nothing here is adopted: the supervisor approves the design first (docs/phase-b/).
#
# EVERY SWITCH IS OFF. With all of them off, the logger's rows and every analysis result
# are byte-identical to the project before this block existed
# (tests/test_phase_b_flags.py against tests/golden/phase_b_off.json).
#
# This block is the ONE place for values that are not known yet. The firmware gets them
# through a generated header, export/c/generated/phase_b_config.h:
#       python -m export.phase_b_header
# (tests/test_phase_b_flags.py fails if the committed header is not current.) After every
# [PLACEHOLDER] below has been replaced by a measured or chosen value, set
# PHASE_B_PLACEHOLDERS_CONFIRMED = True and regenerate. Until then the firmware refuses to
# build a version that could switch the pump on.
# ==============================================================================================

# --- Analysis switches (the firmware's own switches are in PvDaq/src/pv_config.h) -------------
PHASE_B_ENABLED = False         # pipeline: spray-trial tables (events, arms, cleaning check)
LOADOFF_CLASS_ENABLED = False   # load-off spikes as their own class; verdict with and without
ECT_ENABLED = False             # equivalent cell temperature from the sweeps' V_oc

# --- Trial design (shared by firmware and analysis) --------------------------------------------
PHASE_B_BLOCK_SIZE = 4          # [USER 2026-11-16] permuted blocks of 4: 2 spray + 2 no-spray
PHASE_B_SEED = 20261117         # [PLACEHOLDER] seed of the allocation sequence. Choose it ONCE,
                                # before the first event, write it in the protocol, never change
                                # it during the trial. The logger prints it at every boot.
PHASE_B_EVENT_WINDOW_MIN = 30   # [ASSUMPTION] the spike window: from the alarm row, the same
                                # for both arms. ~4.8 thermal time constants (6.3 min [LIT]), so
                                # a sprayed module is back within ~1 % of its uncooled path.
PHASE_B_LOCKOUT_MIN = 10        # [ASSUMPTION] after a window closes, no new event for this
                                # long: the next event's baseline must lie outside the window
PHASE_B_BASELINE_MIN = 10       # [ASSUMPTION] minutes before the alarm used as the event's own
                                # baseline (analysis only; must not exceed the lock-out)
PHASE_B_DRY_RUN = True          # events, randomization and logging run; the pump NEVER does.
                                # For commissioning, and an A/A check of the analysis on the rig.
PHASE_B_PLACEHOLDERS_CONFIRMED = False   # set True only when no [PLACEHOLDER] is left below

# --- Pump and spray [PLACEHOLDER: no pump or nozzle is chosen] ---------------------------------
PUMP_DUTY = 0.60                # [PLACEHOLDER] PWM duty while spraying, 0-1. From the pump
                                # curve (bucket test): lowest duty that gives a full spray cone
SPRAY_MAX_S = 60                # [PLACEHOLDER] longest spray of one event, s ("max spray time")
SPRAY_STOP_DROP_C = 0.0         # [PLACEHOLDER] stop when the module has cooled by this much
                                # since the alarm; 0 = not used (every spray runs SPRAY_MAX_S)
SPRAY_MIN_DT_WATER_C = 2.0      # [PLACEHOLDER] spray only while T_module - T_water >= this:
                                # warm water cannot cool (decision 2026-11-06, T_water measured)
SPRAY_MAX_DT_WATER_C = 40.0     # [PLACEHOLDER] thermal-shock guard: no spray when the glass is
                                # hotter than the water by more than this. No manufacturer
                                # guidance exists for this module; needs a stated basis
SPRAY_MIN_G_WM2 = 200.0         # [ASSUMPTION] no event below this irradiance (= SWEEP_MIN_G_WM2)
SPRAY_NEED_T_WATER = True       # an event needs a valid tank temperature (TANK_ROM filled in)
PUMP_I_MAX_A = 5.0              # [PLACEHOLDER] over-current stop (stalled pump): pump datasheet
PUMP_NO_FLOW_S = 5              # [PLACEHOLDER] pump on this long without a flow pulse -> stop
                                # (dry tank, blocked line): priming time of the real loop
PUMP_SAMPLE_MS = 250            # [ASSUMPTION] period of the pump-power readings (64-average)

# --- Sensors and pins [PLACEHOLDER: nothing is bought or wired] --------------------------------
FLOW_PULSES_PER_L = 450.0       # [PLACEHOLDER] flow-sensor pulses per litre: no sensor chosen.
                                # Calibrate by volume / time in the bucket test
FLOW_PIN = 35                   # [PLACEHOLDER] GPIO of the flow pulses. 35/36/39 are the spare
                                # pins (PINMAP.md): input-only, no pull-up; a 5 V Hall sensor
                                # needs a divider or level shifter
PUMP_INA226_ADDR = 0x41         # [PLACEHOLDER] I2C address of the pump-rail INA226. The two M5
                                # units bought are FIXED at 0x41 (M5 docs), so "its own address"
                                # is only possible behind its own PaHub channel (below) or with
                                # a different breakout
PUMP_INA226_PAHUB_CH = 1        # PaHub port "1" (bench check 2026-11-05); -1 = wired directly
                                # to the bus (only with an address that is not 0x41)
PUMP_INA226_SHUNT_OHM = 0.005   # [PLACEHOLDER] M5 unit: 5 mOhm [DS]; another board: its shunt
PUMP_INA226_MAX_A = 10.0        # [PLACEHOLDER] M5 unit: +/-10 A [DS]
TANK_ROM = (0, 0, 0, 0, 0, 0, 0, 0)   # [PLACEHOLDER] ROM of the DS18B20 in the tank (T_water).
                                # All zero = not fitted: with SPRAY_NEED_T_WATER no event opens

# --- Per-event analysis (analysis/phase_b.py), all [ASSUMPTION] --------------------------------
PHASE_B_MIN_ROWS_FRAC = 0.95    # an event counts only if this share of its window rows and of
                                # its baseline rows has power and irradiance
CLEAN_SWEEP_WINDOW_MIN = 10     # cleaning check: a sweep is "before" / "after" within this span
CLEAN_AFTER_DELAY_MIN = 0       # "after" = nearest sweep this long after the spray's latest
                                # end (alarm + SPRAY_MAX_S); raise it to wait for dry glass
MDE_ALPHA = 0.05                # two-sided significance of the arm comparison
MDE_POWER = 0.80                # chance of detecting an effect of the stated size
# One week of the trial that is never used for tuning [PROPOSAL, docs/paper/03 sec. 12; not
# adopted]. Name it by its dates BEFORE the first wet event. Its events stay out of every
# table, of the spread used for the minimum detectable effect and of any choice of a
# setting, until PHASE_B_HELDOUT_UNLOCKED is set: once, when settings and code are frozen.
PHASE_B_HELDOUT_WEEK = None     # [PLACEHOLDER] ("YYYY-MM-DD", "YYYY-MM-DD"), first and last
                                # local day; None = no week named
PHASE_B_HELDOUT_UNLOCKED = False

# --- Equivalent cell temperature from V_oc (analysis/ect.py; docs/phase-b/03) ------------------
# IEC 60904-5:2011, Eq. (4): ECT = T_ref + (1/beta) * [V_oc / V_oc,ref - 1 - a * ln(G / G_ref)].
# The standard requires these three numbers to come from a calibration of THIS module.
# We have none: they are placeholders, replaceable by fitted values (ect.fit_parameters).
ECT_VOC_REF_V = 21.5            # [PLACEHOLDER] V_oc at the reference condition: seller listing
ECT_G_REF_WM2 = 1000.0          # [PLACEHOLDER] the listing does not state its conditions;
ECT_T_REF_C = 25.0              # [PLACEHOLDER] STC is assumed
ECT_BETA_VOC_REL = -0.0030      # [PLACEHOLDER] relative temperature coefficient of V_oc, 1/degC.
                                # NOT in the listing and NOT this module's value: an
                                # illustrative crystalline-silicon figure, like GAMMA_P above
ECT_A_IRR = 0.05                # [PLACEHOLDER] irradiance constant "a" of the standard. [EST]
                                # n * N_s * kT/q / V_oc with 36 cells [ASSUMPTION], n = 1.2
ECT_ISC_REF_A = 5.88            # [PLACEHOLDER] listing I_sc, for the self-reference form
ECT_USE_ISC = False             # True: ln(I_sc / I_sc,ref) instead of ln(G / G_ref)
ECT_MIN_G_WM2 = 400.0           # [LIT] IEC 60904-5:2011+AMD1:2022: only above 400 W/m2 (the
                                # 2011 edition said 200)
ECT_LAG_MAX_S = 300             # [ASSUMPTION] search range of the cross-correlation
ECT_SPIKE_PAD_MIN = 10          # [ASSUMPTION] minutes before onset and after the peak that
                                # belong to a spike's lag window
ECT_LAG_MIN_SWEEPS = 8          # [ASSUMPTION] a spike needs this many usable sweeps for a lag
ECT_FIT_MAX_RATE_C_MIN = 0.2    # [ASSUMPTION] fit only on sweeps taken while the back sheet
                                # changes more slowly than this (it then stands for the cells)
ECT_FIT_MIN_SWEEPS = 30         # [ASSUMPTION] fewer steady sweeps: no fit, placeholders stay
