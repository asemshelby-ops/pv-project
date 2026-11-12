// Measurement-load logic: see pv_load_logic.h and docs/paper/07-load-firmware-spec.md.
#include "pv_load_logic.h"

#include <math.h>
#include <string.h>

enum { PH_VOC = 0, PH_COARSE = 1, PH_FINE = 2 };

static int is_nan(float x) { return x != x; }
static int32_t since(uint32_t now, uint32_t t) { return (int32_t)(now - t); }  // wrap-safe

uint32_t load_counts(const load_t *L, float d) {
  const uint32_t top = (1u << L->cfg.pwm_bits) - 1u;
  if (!(d > 0.0f)) return 0;  // also catches NaN
  if (d >= 1.0f) return top;
  return (uint32_t)(d * (float)top + 0.5f);
}

static float quant(const load_t *L, float d) {
  return (float)load_counts(L, d) / (float)((1u << L->cfg.pwm_bits) - 1u);
}

static uint32_t period_ms(const load_t *L) {
  return L->gamma_active ? L->cfg.gamma_period_ms : L->cfg.sweep_period_ms;
}

static int sweep_wanted(const load_t *L, uint32_t now) {
  return L->sweep_req || (period_ms(L) && since(now, L->t_next_sweep_ms) >= 0);
}

// Both branches off; the next OFF -> HOLD needs a fresh open-circuit reading.
static void outputs_off(load_t *L, uint32_t mode, uint32_t now) {
  L->d_a = L->d_b = L->d_hold = 0.0f;
  L->mode = mode;
  L->have_reading = 0;
  L->low_g = 0;
  L->t_next_read_ms = now;
}

static void trip(load_t *L, uint32_t reason, uint32_t now) {
  if (L->mode != LOAD_TRIP) {
    outputs_off(L, LOAD_TRIP, now);
    L->trip_reason = reason;  // the first cause is the one kept
  }
  L->t_fault_ms = now;
}

void load_init(load_t *L, const load_cfg_t *cfg, uint32_t now_ms) {
  memset(L, 0, sizeof(*L));
  L->cfg = *cfg;
  outputs_off(L, LOAD_OFF, now_ms);
}

// ---- sweep ------------------------------------------------------------------------------

static float coarse_duty(const load_t *L, uint32_t k) {
  const load_cfg_t *c = &L->cfg;
  if (c->n_coarse < 2) return 1.0f;
  return c->d_min * powf(1.0f / c->d_min, (float)k / (float)(c->n_coarse - 1));
}

static float fine_duty(const load_t *L, uint32_t k) {
  const load_cfg_t *c = &L->cfg;
  const uint32_t m = L->sw_m;
  const float lo = L->sweep.d[m > 0 ? m - 1 : 0];
  const float hi = L->sweep.d[m + 1 < c->n_coarse ? m + 1 : c->n_coarse - 1];
  if (c->n_fine < 2) return lo;
  return lo + (hi - lo) * (float)k / (float)(c->n_fine - 1);
}

static void set_sweep_duty(load_t *L, float d, uint32_t now) {
  L->d_a = L->d_b = quant(L, d);  // branch rule: same duty on A and B
  L->t_step_ms = now;
  L->sw_have_prev = 0;
}

static uint32_t best_point(const load_sweep_t *s, uint32_t from, uint32_t to) {
  uint32_t best = from;
  for (uint32_t k = from; k < to; k++)
    if (s->v[k] * s->i[k] > s->v[best] * s->i[best]) best = k;
  return best;
}

// Back to HOLD on branch A alone. adopt = 1: take V_set and the duty from the best point.
static void sweep_end(load_t *L, uint32_t now, int adopt) {
  load_sweep_t *s = &L->sweep;
  s->best = s->n ? best_point(s, 0, s->n) : 0;
  if (adopt && s->n && s->v[s->best] * s->i[s->best] > 0.0f) {
    L->v_set = s->v[s->best];
    L->d_hold = 2.0f * s->d[s->best];  // same R_eff with one branch instead of two
    if (L->d_hold > 1.0f) L->d_hold = 1.0f;
  } else {
    L->v_set = L->v_set_resume;
    L->d_hold = L->d_resume;
  }
  L->d_a = quant(L, L->d_hold);
  L->d_b = 0.0f;
  L->mode = LOAD_HOLD;
  L->t_next_read_ms = now + L->cfg.hold_period_ms;
  s->t_end_ms = now;
  L->last_sweep_id = s->id;
  L->sweep_done = 1;
}

static void sweep_reading(load_t *L, uint32_t now, float v, float i) {
  const load_cfg_t *c = &L->cfg;
  load_sweep_t *s = &L->sweep;
  if (L->sw_phase == PH_VOC) {
    s->v_oc = v;
    L->sw_phase = PH_COARSE;
    L->sw_k = 0;
    set_sweep_duty(L, coarse_duty(L, 0), now);
    return;
  }
  // Store the point only if it was triggered late enough and repeats the reading before
  // it; otherwise read again at the same duty (see the header).
  const uint32_t left = c->n_coarse + c->n_fine - s->n - 1;  // points after this one
  const int late_enough = since(L->t_trig_ms, L->t_step_ms) >= (int32_t)c->t_settle_ms;
  const int agrees = L->sw_have_prev && fabsf(v - L->sw_v_prev) <= c->settle_tol * s->v_oc;
  const int no_time = (uint32_t)since(now, s->t_start_ms) +
                      left * (c->t_settle_ms + c->sweep_conv_ms) > c->sweep_budget_ms;
  if (!late_enough || !(agrees || no_time)) {
    L->sw_v_prev = v;
    L->sw_have_prev = 1;
    return;
  }
  if (!agrees) s->flags |= LOAD_FLAG_NOT_SETTLED;
  s->d[s->n] = L->d_a;  // the duty really applied (rounded to the PWM grid)
  s->v[s->n] = v;
  s->i[s->n] = i;
  s->n++;
  L->sw_k++;
  if (L->sw_phase == PH_COARSE) {
    if (L->sw_k < c->n_coarse) {
      set_sweep_duty(L, coarse_duty(L, L->sw_k), now);
      return;
    }
    for (uint32_t k = 1; k < c->n_coarse; k++)
      if (s->v[k] > s->v[k - 1] + c->mono_tol * s->v_oc) s->flags |= LOAD_FLAG_NON_MONOTONIC;
    L->sw_m = best_point(s, 0, c->n_coarse);
    L->sw_phase = PH_FINE;
    L->sw_k = 0;
    if (c->n_fine) {
      set_sweep_duty(L, fine_duty(L, 0), now);
      return;
    }
  } else if (L->sw_k < c->n_fine) {
    set_sweep_duty(L, fine_duty(L, L->sw_k), now);
    return;
  }
  sweep_end(L, now, 1);
  // By duty, not by index: a coarse point can sit exactly on an end of the fine pass.
  if (c->n_fine && (s->d[s->best] <= s->d[c->n_coarse] || s->d[s->best] >= s->d[s->n - 1]))
    s->flags |= LOAD_FLAG_MAX_AT_EDGE;
}

int load_sweep_due(const load_t *L, uint32_t now_ms, uint32_t ms_to_next_row) {
  return L->mode == LOAD_HOLD && sweep_wanted(L, now_ms) && !L->ina_frozen &&
         !L->read_pending && L->have_reading && ms_to_next_row >= L->cfg.sweep_guard_ms;
}

int load_sweep_start(load_t *L, uint32_t now_ms, uint32_t ms_to_next_row, float g_before) {
  if (!load_sweep_due(L, now_ms, ms_to_next_row)) return 0;
  const uint32_t period = period_ms(L);
  if (period && since(now_ms, L->t_next_sweep_ms) >= 0) {
    L->t_next_sweep_ms += period;  // fixed grid; after a long pause, restart it from now
    if (since(now_ms, L->t_next_sweep_ms) >= 0) L->t_next_sweep_ms = now_ms + period;
  }
  L->sweep_req = 0;
  const uint32_t id = L->sweep.id + 1;
  memset(&L->sweep, 0, sizeof(L->sweep));
  L->sweep.id = id;
  L->sweep.g_before = g_before;
  L->sweep.g_after = NAN;
  L->sweep.t_start_ms = now_ms;
  L->sweep_done = 0;
  L->d_resume = L->d_hold;
  L->v_set_resume = L->v_set;
  L->mode = LOAD_SWEEP;
  L->sw_phase = PH_VOC;
  L->d_a = L->d_b = 0.0f;
  L->t_step_ms = now_ms;
  return 1;
}

void load_sweep_request(load_t *L, uint32_t now_ms) {
  (void)now_ms;
  L->sweep_req = 1;
}

uint32_t load_sweep_close(load_t *L, float g_after) {
  load_sweep_t *s = &L->sweep;
  const load_cfg_t *c = &L->cfg;
  s->g_after = g_after;
  // G not known on either side: stability cannot be shown, so the sweep is flagged.
  if (is_nan(s->g_before) || is_nan(g_after) || !(s->g_before > 0.0f) ||
      fabsf(g_after - s->g_before) / s->g_before > c->g_unstable)
    s->flags |= LOAD_FLAG_G_UNSTABLE;
  if (s->g_before < c->g_on || s->v_oc < c->voc_on) s->flags |= LOAD_FLAG_LOW_LIGHT;
  L->sweep_done = 0;
  return s->flags;
}

// ---- service and readings ---------------------------------------------------------------

uint32_t load_service(load_t *L, uint32_t now_ms, float g, float t_heatsink) {
  const load_cfg_t *c = &L->cfg;

  if (is_nan(t_heatsink)) trip(L, LOAD_TRIP_HEATSINK_SENSOR, now_ms);
  else if (t_heatsink >= c->t_trip_c) trip(L, LOAD_TRIP_HEATSINK_HOT, now_ms);

  if (L->gamma_active && since(now_ms, L->t_gamma_end_ms) >= 0) {
    L->gamma_active = 0;
    L->t_next_sweep_ms = now_ms + c->sweep_period_ms;
  }

  switch (L->mode) {
    case LOAD_TRIP:
      if (since(now_ms, L->t_fault_ms) >= (int32_t)c->trip_clear_ms)
        outputs_off(L, LOAD_OFF, now_ms);
      break;
    case LOAD_OFF:
      if (!L->manual_off && !L->ina_frozen && L->have_reading && g >= c->g_on &&
          L->v_last >= c->voc_on) {
        L->mode = LOAD_HOLD;
        L->v_set = c->vset_fraction * L->v_last;  // until the first sweep
        L->d_hold = c->hold_d_min;
        L->d_a = quant(L, L->d_hold);
        L->t_next_sweep_ms = now_ms;              // first sweep as soon as a slot allows
      }
      break;
    case LOAD_HOLD:
      if (L->manual_off) {
        outputs_off(L, LOAD_OFF, now_ms);
      } else if (!is_nan(g)) {
        if (g >= c->g_off) {
          L->low_g = 0;
        } else if (!L->low_g) {
          L->low_g = 1;
          L->t_low_g_ms = now_ms;
        } else if (since(now_ms, L->t_low_g_ms) >= (int32_t)c->g_off_ms) {
          outputs_off(L, LOAD_OFF, now_ms);
        }
      }
      break;
    default:
      break;
  }

  if (L->read_pending) return LOAD_REQ_NONE;
  if (L->mode == LOAD_SWEEP) {
    // First reading of a point: timed to end as t_settle_ms ends. Later ones: at once.
    uint32_t wait = c->voc_settle_ms;
    if (L->sw_phase != PH_VOC)
      wait = L->sw_have_prev || c->t_settle_ms < c->sweep_conv_ms
                 ? 0 : c->t_settle_ms - c->sweep_conv_ms;
    if (since(now_ms, L->t_step_ms) < (int32_t)wait) return LOAD_REQ_NONE;
    L->t_trig_ms = now_ms;
    L->read_pending = 1;
    return LOAD_REQ_READ_SWEEP;
  }
  if (since(now_ms, L->t_next_read_ms) < 0) return LOAD_REQ_NONE;
  L->t_next_read_ms = now_ms + c->hold_period_ms;
  L->read_pending = 1;
  return LOAD_REQ_READ_HOLD;
}

void load_reading(load_t *L, uint32_t now_ms, float v, float i, int ok) {
  const load_cfg_t *c = &L->cfg;
  L->read_pending = 0;
  if (!ok) {
    if (L->ina_fails < c->ina_fail_limit) L->ina_fails++;
    // Frozen = the hold duty simply stays where it is (a fixed resistor is passive) and
    // no sweep starts. A sweep that loses a reading is cut short and flagged.
    if (L->ina_fails >= c->ina_fail_limit) L->ina_frozen = 1;
    if (L->mode == LOAD_SWEEP) {
      L->sweep.flags |= LOAD_FLAG_INA_ERROR;
      sweep_end(L, now_ms, 0);
    }
    return;
  }
  L->ina_fails = 0;
  L->ina_frozen = 0;
  if (i > c->i_trip_a) {
    trip(L, LOAD_TRIP_OVERCURRENT, now_ms);
    return;
  }
  L->v_last = v;
  L->i_last = i;
  L->have_reading = 1;
  if (L->mode == LOAD_SWEEP) {
    sweep_reading(L, now_ms, v, i);
  } else if (L->mode == LOAD_HOLD && L->v_set > 0.0f) {
    float f = 1.0f + c->hold_gain * (v - L->v_set) / L->v_set;
    if (f > 1.0f + c->hold_max_step) f = 1.0f + c->hold_max_step;
    if (f < 1.0f - c->hold_max_step) f = 1.0f - c->hold_max_step;
    L->d_hold *= f;
    if (L->d_hold < c->hold_d_min) L->d_hold = c->hold_d_min;
    if (L->d_hold > 1.0f) L->d_hold = 1.0f;
    L->d_a = quant(L, L->d_hold);
  }
}

void load_gamma(load_t *L, uint32_t now_ms, uint32_t minutes) {
  L->gamma_active = minutes > 0;
  L->t_gamma_end_ms = now_ms + minutes * 60000u;
  L->t_next_sweep_ms = minutes ? now_ms : now_ms + L->cfg.sweep_period_ms;
}

void load_manual_off(load_t *L, uint32_t now_ms, int on) {
  L->manual_off = on ? 1 : 0;
  if (on && (L->mode == LOAD_HOLD || L->mode == LOAD_SWEEP)) outputs_off(L, LOAD_OFF, now_ms);
}

// ---- text and test access ---------------------------------------------------------------

const char *load_mode_text(uint32_t mode) {
  static const char *const T[] = {"off", "hold", "sweep", "trip"};
  return mode <= LOAD_TRIP ? T[mode] : "?";
}

const char *load_trip_text(uint32_t r) {
  static const char *const T[] = {"none", "heatsink hot", "heatsink sensor missing",
                                  "overcurrent"};
  return r <= LOAD_TRIP_OVERCURRENT ? T[r] : "?";
}

uint32_t load_sizeof(void) { return (uint32_t)sizeof(load_t); }
uint32_t load_cfg_sizeof(void) { return (uint32_t)sizeof(load_cfg_t); }

double load_get(const load_t *L, const char *f) {
#define F(name, value) if (!strcmp(f, name)) return (double)(value)
  F("mode", L->mode);
  F("d_a", L->d_a);
  F("d_b", L->d_b);
  F("v_set", L->v_set);
  F("trip_reason", L->trip_reason);
  F("ina_frozen", L->ina_frozen);
  F("manual_off", L->manual_off);
  F("gamma_active", L->gamma_active);
  F("have_reading", L->have_reading);
  F("sweep_done", L->sweep_done);
  F("last_sweep_id", L->last_sweep_id);
  F("sweep_id", L->sweep.id);
  F("sweep_n", L->sweep.n);
  F("sweep_best", L->sweep.best);
  F("sweep_flags", L->sweep.flags);
  F("sweep_v_oc", L->sweep.v_oc);
  F("sweep_t_start", L->sweep.t_start_ms);
  F("sweep_t_end", L->sweep.t_end_ms);
#undef F
  return -1e99;  // unknown field: the tests assert on this
}

double load_sweep_point(const load_t *L, uint32_t k, int what) {
  if (k >= L->sweep.n) return -1e99;
  return what == 0 ? L->sweep.d[k] : what == 1 ? L->sweep.v[k] : L->sweep.i[k];
}
