"""All parameters of the synthetic pipeline, in one place.

Every value carries its source tag:
  [CLAUDE.md]  team simulation parameter recorded in CLAUDE.md (illustrative)
  [LIT]        published literature (source given)
  [DS]         datasheet / seller listing
  [EST]        first-pass engineering estimate, to be replaced by measurement
  [ASSUMPTION] chosen for the synthetic data only; not a property of our hardware

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
TILT_DEG = 21.0             # [CLAUDE.md] tilt ~21 deg
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
