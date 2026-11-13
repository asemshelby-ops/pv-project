/* NASIM on-device spike detection: features, thermal model, CUSUM core and
 * the NASIM-GBT alarm logic. A line-by-line port of detect/features.py,
 * detect/residual.py, sim/thermal.py (step) and detect/gbt.py (GbtDetector).
 * Also the paper's two baselines, each with its own state: the temperature-rise
 * threshold (detect/threshold.py, no irradiance gate) and the CUSUM on the
 * temperature increment (detect/cusum_inc.py).
 *
 * Precision: `real` is double by default (bit-for-bit parity with Python).
 * Compile with -DNASIM_FLOAT -fsingle-precision-constant for single precision,
 * the only floating-point type the ESP32 FPU executes in hardware.
 * All numeric constants come from nasim_config.h, generated from config.py.
 */
#ifndef NASIM_H
#define NASIM_H

#include <math.h>
#include "nasim_config.h"

#ifdef NASIM_FLOAT
typedef float real;
#define R_SIN sinf
#define R_COS cosf
#define R_ACOS acosf
#define R_EXP expf
#define R_POW powf
#else
typedef double real;
#define R_SIN sin
#define R_COS cos
#define R_ACOS acos
#define R_EXP exp
#define R_POW pow
#endif

#define NASIM_PI 3.141592653589793
#define RING_MAX (NASIM_N10 + 1)
#define N_BACK 5
#define MAX_FEATURES 12

/* One 10 s sample, as read from the sensors (wind = NAN if no anemometer). */
typedef struct {
    real t_back[N_BACK];   /* T1..T5, degC */
    real ta;               /* ambient, degC */
    real g;                /* in-plane irradiance, W/m2 */
    real wind;             /* m/s */
    int doy;               /* day of year (local date) */
    real minute;           /* minutes since local midnight */
} sample_t;

/* Fixed-size ring buffer; ring_ago(r, 0) is the newest value. */
typedef struct {
    real buf[RING_MAX];
    int size, head, count;
} ring_t;

/* Online thermal model + residual + CUSUM statistic (detect/residual.py). */
typedef struct {
    int use_wind;
    real k, c, u0, u1, a_wind, a_res;
    int has_model, has_v;
    real model, prev_g, prev_ta, prev_v, v_slow, r_base, S, d;
} cusum_core_t;

/* Streaming feature extractor (detect/features.py). */
typedef struct {
    int use_wind, hybrid;
    ring_t t_raw, t_med, g, wind;
    cusum_core_t core;
} features_t;

/* Model: m2cgen-generated tree ensemble, output[1] = p(spike). */
typedef void (*score_fn)(real *input, real *output);

/* NASIM-GBT detector (detect/gbt.py, GbtDetector). */
typedef struct {
    features_t fx;
    score_fn score;
    real p_threshold;
    int persist, quiet, run, n_features;
    real p;                /* last probability, NAN while warming up */
    real f[MAX_FEATURES];  /* last feature vector (valid when p is not NAN) */
} gbt_detector_t;

/* Baseline (a): rise of the filtered temperature over 10 min >= rise_c
 * (detect/threshold.py, ThresholdDetector(rise_c, gate=None)). */
typedef struct {
    real rise_c;
    ring_t t_raw, t_med;
    int quiet;
    real rise;             /* last 10-min rise, NAN while warming up */
} thr_detector_t;

/* Baseline (b): CUSUM on the increment of the filtered temperature
 * (detect/cusum_inc.py, CusumIncDetector). */
typedef struct {
    real kappa, h;
    ring_t t_raw;
    int has_prev, n, quiet;
    real prev, S;
} cusum_inc_t;

real thermal_step(real t, real g, real ta, real v, real c, real u0, real u1);
void cusum_core_init(cusum_core_t *cc, int use_wind, real k, real c, real u0, real u1);
void cusum_core_reset(cusum_core_t *cc);
int cusum_core_update(cusum_core_t *cc, real t, const sample_t *s);

void features_init(features_t *fx, int use_wind, int hybrid);
void features_reset(features_t *fx);
int features_count(int use_wind, int hybrid);
int features_update(features_t *fx, const sample_t *s, real *out);

void gbt_init(gbt_detector_t *det, score_fn score, int use_wind, int hybrid,
              real p_threshold, int persist);
void gbt_reset(gbt_detector_t *det);
int gbt_update(gbt_detector_t *det, const sample_t *s);

void thr_init(thr_detector_t *det, real rise_c);
void thr_reset(thr_detector_t *det);
int thr_update(thr_detector_t *det, const sample_t *s);

void cusum_inc_init(cusum_inc_t *det, real kappa, real h);
void cusum_inc_reset(cusum_inc_t *det);
int cusum_inc_update(cusum_inc_t *det, const sample_t *s);

#endif
