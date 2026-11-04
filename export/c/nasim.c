/* NASIM on-device spike detection -- see nasim.h. Each function names the
 * Python function it mirrors; the order of arithmetic is kept identical so
 * that the double build reproduces Python bit for bit. */
#include "nasim.h"

/* ---- ring buffer, median (detect/features.py: Ring, median3) ------------ */

static void ring_init(ring_t *r, int size) {
    r->size = size;
    r->head = -1;
    r->count = 0;
    for (int i = 0; i < RING_MAX; i++) r->buf[i] = 0;
}

static void ring_push(ring_t *r, real x) {
    r->head = (r->head + 1) % r->size;
    r->buf[r->head] = x;
    if (r->count < r->size) r->count++;
}

static real ring_ago(const ring_t *r, int k) {
    return r->buf[(r->head - k + r->size) % r->size];
}

static int ring_full(const ring_t *r) { return r->count == r->size; }

static real rmin(real a, real b) { return b < a ? b : a; }   /* Python min(a, b) */
static real rmax(real a, real b) { return b > a ? b : a; }   /* Python max(a, b) */

static real median3(real a, real b, real c) {
    return rmax(rmin(a, b), rmin(rmax(a, b), c));
}

/* ---- thermal model (sim/thermal.py: faiman_u, steady_state, time_constant, step) */

real thermal_step(real t, real g, real ta, real v, real c, real u0, real u1) {
    real area = NASIM_AREA_M2;
    real u = u0 + u1 * v;
    real t_ss = ta + (area * g - 0.0) / (area * u);
    real tau = c / (area * u);
    return t_ss + (t - t_ss) * R_EXP(-NASIM_DT_S / tau);
}

/* ---- CUSUM core (detect/residual.py: CusumCore) ------------------------- */

void cusum_core_init(cusum_core_t *cc, int use_wind, real k, real c, real u0, real u1) {
    cc->use_wind = use_wind;
    cc->k = k;
    cc->c = c;
    cc->u0 = u0;
    cc->u1 = u1;
    cc->a_wind = 1.0 - R_EXP(-NASIM_DT_S / (NASIM_CUSUM_WIND_TAU_MIN * 60));
    cc->a_res = 1.0 - R_EXP(-NASIM_DT_S / (NASIM_CUSUM_RES_TAU_MIN * 60));
    cusum_core_reset(cc);
}

void cusum_core_reset(cusum_core_t *cc) {
    cc->has_model = 0;
    cc->has_v = 0;
    cc->model = cc->prev_g = cc->prev_ta = cc->prev_v = cc->v_slow = 0;
    cc->r_base = 0.0;
    cc->S = 0.0;
    cc->d = 0.0;
}

int cusum_core_update(cusum_core_t *cc, real t, const sample_t *s) {
    if (cc->use_wind && s->wind == s->wind) {             /* not NaN */
        cc->v_slow = cc->has_v ? cc->v_slow + cc->a_wind * (s->wind - cc->v_slow) : s->wind;
    } else {
        cc->v_slow = NASIM_CUSUM_WIND_CONST;
    }
    cc->has_v = 1;
    if (!cc->has_model) {                                   /* first sample of the day */
        cc->model = t;
        cc->prev_g = s->g;
        cc->prev_ta = s->ta;
        cc->prev_v = cc->v_slow;
        cc->has_model = 1;
        return 0;
    }
    cc->model = thermal_step(cc->model, cc->prev_g, cc->prev_ta, cc->prev_v,
                             cc->c, cc->u0, cc->u1);
    cc->prev_g = s->g;
    cc->prev_ta = s->ta;
    cc->prev_v = cc->v_slow;
    real r = t - cc->model;
    cc->d = r - cc->r_base;
    cc->r_base += cc->a_res * (r - cc->r_base);
    cc->S = rmax(0.0, cc->S + cc->d - cc->k);
    return 1;
}

/* ---- solar geometry, clear sky (detect/features.py) --------------------- */

static void solar_angles(int doy, real minute_local, real *cos_z, real *cos_aoi) {
    real d2r = NASIM_PI / 180.0;
    real gam = 2.0 * NASIM_PI / 365.0 * ((doy - 1) + (minute_local / 60.0 - 12.0) / 24.0);
    real eqtime = 229.18 * (0.000075 + 0.001868 * R_COS(gam) - 0.032077 * R_SIN(gam)
                            - 0.014615 * R_COS(2 * gam) - 0.040849 * R_SIN(2 * gam));
    real decl = (0.006918 - 0.399912 * R_COS(gam) + 0.070257 * R_SIN(gam)
                 - 0.006758 * R_COS(2 * gam) + 0.000907 * R_SIN(2 * gam)
                 - 0.002697 * R_COS(3 * gam) + 0.00148 * R_SIN(3 * gam));
    real tz_hours = 3.0;                                  /* Asia/Riyadh, UTC+3 */
    real true_solar_min = minute_local + eqtime + 4.0 * NASIM_LON - 60.0 * tz_hours;
    real ha = (true_solar_min / 4.0 - 180.0) * d2r;
    real lat = NASIM_LAT * d2r, beta = NASIM_TILT_DEG * d2r;
    real gs = (NASIM_AZIMUTH_DEG - 180.0) * d2r;
    real sd = R_SIN(decl), cd = R_COS(decl);
    *cos_z = R_SIN(lat) * sd + R_COS(lat) * cd * R_COS(ha);
    *cos_aoi = (sd * R_SIN(lat) * R_COS(beta)
                - sd * R_COS(lat) * R_SIN(beta) * R_COS(gs)
                + cd * R_COS(lat) * R_COS(beta) * R_COS(ha)
                + cd * R_SIN(lat) * R_SIN(beta) * R_COS(gs) * R_COS(ha)
                + cd * R_SIN(beta) * R_SIN(gs) * R_SIN(ha));
}

static real clear_sky_poa_simple(real cos_z, real cos_aoi) {
    if (cos_z <= 0.01) return 0.0;
    real z_deg = R_ACOS(cos_z) * (180.0 / NASIM_PI);
    real am = 1.0 / (cos_z + 0.50572 * R_POW(96.07995 - z_deg, -1.6364));
    real dni = 1353.0 * R_POW(0.7, R_POW(am, 0.678));
    real dhi = NASIM_CS_DIFFUSE_FRAC * dni;
    real beta = NASIM_TILT_DEG * (NASIM_PI / 180.0);
    return dni * rmax(cos_aoi, 0.0) + dhi * (1.0 + R_COS(beta)) / 2.0;
}

/* ---- feature extractor (detect/features.py: FeatureExtractor) ----------- */

int features_count(int use_wind, int hybrid) {
    return 8 + (use_wind ? 2 : 0) + (hybrid ? 2 : 0);
}

void features_init(features_t *fx, int use_wind, int hybrid) {
    fx->use_wind = use_wind;
    fx->hybrid = hybrid;
    cusum_core_init(&fx->core, use_wind, NASIM_HYBRID_CUSUM_K,
                    NASIM_HEAT_CAPACITY_J_K, NASIM_FAIMAN_U0, NASIM_FAIMAN_U1);
    features_reset(fx);
}

void features_reset(features_t *fx) {
    ring_init(&fx->t_raw, NASIM_MEDIAN_N);
    ring_init(&fx->t_med, NASIM_N10 + 1);
    ring_init(&fx->g, NASIM_N10 + 1);
    ring_init(&fx->wind, NASIM_N10);
    cusum_core_reset(&fx->core);
}

/* Fills out[] in the order of detect/features.py feature_names(use_wind, hybrid).
 * Returns 1 when the features are valid, 0 while the buffers fill. */
int features_update(features_t *fx, const sample_t *s, real *out) {
    real t_mean = 0.0;
    for (int i = 0; i < N_BACK; i++) t_mean += s->t_back[i];
    t_mean = t_mean / N_BACK;
    ring_push(&fx->t_raw, t_mean);
    ring_push(&fx->g, s->g);
    ring_push(&fx->wind, s->wind == s->wind ? s->wind : 0.0);
    if (!ring_full(&fx->t_raw)) return 0;
    real t = median3(ring_ago(&fx->t_raw, 0), ring_ago(&fx->t_raw, 1), ring_ago(&fx->t_raw, 2));
    ring_push(&fx->t_med, t);
    if (fx->hybrid) cusum_core_update(&fx->core, t, s);   /* every sample */
    if (!(ring_full(&fx->t_med) && ring_full(&fx->g) && ring_full(&fx->wind))) return 0;

    real g0 = ring_ago(&fx->g, 0);
    real g_max = g0, g_min = g0;
    for (int k = 1; k < NASIM_N10 + 1; k++) {
        g_max = rmax(g_max, ring_ago(&fx->g, k));
        g_min = rmin(g_min, ring_ago(&fx->g, k));
    }
    real cos_z, cos_aoi;
    solar_angles(s->doy, s->minute, &cos_z, &cos_aoi);
    real g_cs = clear_sky_poa_simple(cos_z, cos_aoi);
    real g2 = ring_ago(&fx->g, NASIM_N2);

    int i = 0;
    out[i++] = t - ring_ago(&fx->t_med, NASIM_N2);          /* rise_2m */
    out[i++] = t - ring_ago(&fx->t_med, NASIM_N5);          /* rise_5m */
    out[i++] = t - ring_ago(&fx->t_med, NASIM_N10);         /* rise_10m */
    out[i++] = (g0 - g2) / rmax(g2, NASIM_G_FLOOR);         /* g_change_2m */
    out[i++] = (g_max - g_min) / rmax(g0, NASIM_G_FLOOR);   /* g_range_10m */
    out[i++] = g0 / rmax(g_cs, NASIM_G_FLOOR);              /* kc */
    out[i++] = cos_z;                                       /* cos_zenith */
    out[i++] = (t - s->ta) / rmax(g0, NASIM_G_FLOOR) * 1000.0;   /* excess_per_g */
    if (fx->use_wind) {
        real now = 0.0, before = 0.0;
        for (int k = 0; k < NASIM_N2; k++) now += ring_ago(&fx->wind, k);
        for (int k = NASIM_N10 - NASIM_N2; k < NASIM_N10; k++) before += ring_ago(&fx->wind, k);
        now = now / NASIM_N2;
        before = before / NASIM_N2;
        out[i++] = now;                                     /* wind_2m */
        out[i++] = before - now;                            /* wind_drop */
    }
    if (fx->hybrid) {
        out[i++] = fx->core.d;                              /* cusum_d */
        out[i++] = fx->core.S;                              /* cusum_S */
    }
    return 1;
}

/* ---- NASIM-GBT detector (detect/gbt.py: GbtDetector) -------------------- */

void gbt_init(gbt_detector_t *det, score_fn score, int use_wind, int hybrid,
              real p_threshold, int persist) {
    features_init(&det->fx, use_wind, hybrid);
    det->score = score;
    det->p_threshold = p_threshold;
    det->persist = persist;
    det->n_features = features_count(use_wind, hybrid);
    gbt_reset(det);
}

void gbt_reset(gbt_detector_t *det) {
    features_reset(&det->fx);
    det->quiet = 0;
    det->run = 0;
    det->p = NAN;
}

int gbt_update(gbt_detector_t *det, const sample_t *s) {
    real out[2];
    int ok = features_update(&det->fx, s, det->f);
    if (det->quiet > 0) det->quiet--;
    if (!ok) {
        det->p = NAN;
        det->run = 0;
        return 0;
    }
    det->score(det->f, out);
    det->p = out[1];
    det->run = det->p >= det->p_threshold ? det->run + 1 : 0;
    if (det->quiet > 0) return 0;
    if (det->run >= det->persist) {
        det->quiet = NASIM_SILENCE_N;
        det->run = 0;
        return 1;
    }
    return 0;
}
