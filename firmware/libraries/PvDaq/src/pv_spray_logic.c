// Phase B randomized spray trial: see pv_spray_logic.h and docs/phase-b/01-randomized-spray.md.
#include "pv_spray_logic.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

static int is_nan(float x) { return x != x; }
static int32_t since(uint32_t now, uint32_t t) { return (int32_t)(now - t); }  // wrap-safe

// ---- allocation ---------------------------------------------------------------------------

// Integer hash "lowbias32" (C. Wellons, hash-prospector): every input bit changes every
// output bit with probability near 1/2. Used as a counter-based random number generator.
static uint32_t mix32(uint32_t x) {
  x ^= x >> 16;
  x *= 0x7feb352dU;
  x ^= x >> 15;
  x *= 0x846ca68bU;
  x ^= x >> 16;
  return x;
}

// Block b = n / block holds block/2 ones (spray) and block/2 zeros in an order drawn by a
// Fisher-Yates shuffle. Its random numbers depend only on (seed, b), so no generator state
// has to be kept between events or across a restart.
int spray_alloc(uint32_t seed, uint32_t block, uint32_t n) {
  uint8_t arm[SPRAY_MAX_BLOCK];
  if (block < 2 || block > SPRAY_MAX_BLOCK || (block & 1u)) return 0;
  const uint32_t b = n / block;
  for (uint32_t i = 0; i < block; i++) arm[i] = i < block / 2 ? 1 : 0;
  uint32_t s = mix32(seed ^ mix32(b + 0x9e3779b9U));
  for (uint32_t i = block - 1; i > 0; i--) {
    const uint32_t bound = i + 1;                 // j uniform in 0 .. i
    const uint32_t reject = (0u - bound) % bound; // 2^32 mod bound: these r would bias j
    uint32_t r;
    do {
      s += 0x9e3779b9U;
      r = mix32(s);
    } while (r < reject);
    const uint32_t j = r % bound;
    const uint8_t t = arm[i];
    arm[i] = arm[j];
    arm[j] = t;
  }
  return arm[n % block];
}

// The dry run draws from its own sequence, so commissioning does not show the arms the
// wet trial will use.
static uint32_t dry_seed(uint32_t seed) { return mix32(seed ^ 0xD5A1D5A1U); }

// ---- small helpers ------------------------------------------------------------------------

static void set_duty(spray_t *S, uint32_t now, float d) {
  S->duty_ms += S->duty * (float)since(now, S->t_duty_ms);
  S->t_duty_ms = now;
  S->duty = d;
}

static int cfg_ok(const spray_cfg_t *c) {
  return c->block >= 2 && c->block <= SPRAY_MAX_BLOCK && !(c->block & 1u) &&
         c->row_period_ms > 0 && c->spray_max_ms > 0 &&
         c->spray_max_ms + c->row_period_ms < c->window_ms &&
         c->pump_duty > 0.0f && c->pump_duty <= 1.0f && c->pulses_per_l > 0.0f &&
         c->pump_sample_ms > 0 && c->pump_fail_limit > 0;
}

static int event_open(const spray_t *S) {
  return S->mode == SPRAY_ACTIVE || S->mode == SPRAY_WINDOW;
}

static void snapshot(spray_t *S) {
  S->done.spike_id = S->spike_id;
  S->done.assign = S->assign;
  S->done.sprayed = S->sprayed;
  S->done.stop_reason = S->stop_reason;
  S->done.spray_ms = S->spray_ms;
  S->done.pulses = S->pulses_event;
  S->done.energy_j = S->e_event_j;
}

static void stop_spray(spray_t *S, uint32_t now, uint32_t reason) {
  if (S->mode != SPRAY_ACTIVE) return;
  set_duty(S, now, 0.0f);
  S->spray_ms = (uint32_t)since(now, S->t_spray_ms);
  S->stop_reason = reason;
  S->mode = SPRAY_WINDOW;
  if (reason >= SPRAY_STOP_SENSOR && reason != SPRAY_STOP_COMMAND) S->abort_flag = 1;
  if (reason == SPRAY_STOP_NO_FLOW || reason == SPRAY_STOP_OVERCURRENT ||
      reason == SPRAY_STOP_HARD_TIMER)
    S->fault_latched = reason;
  snapshot(S);
  S->notes |= SPRAY_NOTE_STOPPED;
}

static void close_event(spray_t *S, uint32_t now) {
  stop_spray(S, now, SPRAY_STOP_MAX_TIME);  // cannot be on here (spray_max < window): be sure
  snapshot(S);
  S->notes |= SPRAY_NOTE_CLOSED;
  S->spike_id = 0;
  S->assign = -1;
  S->mode = SPRAY_LOCKOUT;
  S->t_lock_ms = now;
}

// Would an alarm at this row open an event? Uses only what is known before the arm is drawn.
static uint32_t eligibility(const spray_t *S, const spray_row_in_t *in) {
  const spray_cfg_t *c = &S->cfg;
  if (event_open(S) || S->commit_pending) return SPRAY_NO_EVENT_OPEN;
  if (!S->armed) return SPRAY_NO_DISARMED;
  if (S->mode == SPRAY_LOCKOUT) return SPRAY_NO_LOCKOUT;
  if (S->fault_latched) return SPRAY_NO_FAULT_LATCHED;
  if (in->row_fault || is_nan(in->t_module)) return SPRAY_NO_SENSOR;
  if (!in->storage_ok) return SPRAY_NO_STORAGE;
  if (!in->window_fits) return SPRAY_NO_LATE;
  if (!(in->g >= c->min_g)) return SPRAY_NO_LOW_LIGHT;  // also catches NaN
  if (!in->load_ok) return SPRAY_NO_LOAD;
  if (!c->dry_run) {  // these need the Phase B hardware, which a dry run does not
    if (!S->pump_ok) return SPRAY_NO_PUMP_SENSOR;
    if (c->need_t_water) {
      if (is_nan(in->t_water)) return SPRAY_NO_TANK_SENSOR;
      const float dt = in->t_module - in->t_water;
      if (!(dt >= c->min_dt_water_c)) return SPRAY_NO_WATER_WARM;
      if (dt > c->max_dt_water_c) return SPRAY_NO_THERMAL_SHOCK;
    }
  }
  return SPRAY_OK;
}

// ---- life cycle ---------------------------------------------------------------------------

void spray_init(spray_t *S, const spray_cfg_t *cfg, uint32_t n_wet, uint32_t n_dry, int armed,
                uint32_t now_ms) {
  memset(S, 0, sizeof(*S));
  S->cfg = *cfg;
  S->mode = SPRAY_IDLE;
  S->assign = -1;
  S->n_wet = n_wet;
  S->n_dry = n_dry;
  S->armed = (armed && cfg_ok(cfg)) ? 1 : 0;
  S->done.assign = -1;
  S->t_meter_ms = S->t_duty_ms = now_ms;
  S->t_pump_trig_ms = now_ms - cfg->pump_sample_ms;  // the first reading is due at once
}

void spray_service(spray_t *S, uint32_t now_ms) {
  const spray_cfg_t *c = &S->cfg;
  if (S->mode != SPRAY_ACTIVE) return;
  if (since(now_ms, S->t_spray_ms) >= (int32_t)c->spray_max_ms) {
    stop_spray(S, now_ms, SPRAY_STOP_MAX_TIME);
  } else if (!c->dry_run && since(now_ms, S->t_last_pulse_ms) >= (int32_t)c->no_flow_ms) {
    stop_spray(S, now_ms, SPRAY_STOP_NO_FLOW);
  }
}

void spray_flow(spray_t *S, uint32_t now_ms, uint32_t pulses) {
  if (!pulses) return;
  S->pulses_total += pulses;
  S->pulses_row += pulses;
  if (event_open(S)) S->pulses_event += pulses;
  S->t_last_pulse_ms = now_ms;
}

int spray_pump_due(spray_t *S, uint32_t now_ms) {
  if (S->pump_pending) return 0;
  if (since(now_ms, S->t_pump_trig_ms) < (int32_t)S->cfg.pump_sample_ms) return 0;
  S->pump_pending = 1;
  S->t_pump_trig_ms = now_ms;
  return 1;
}

void spray_pump_reading(spray_t *S, uint32_t now_ms, float v, float i, int ok) {
  const spray_cfg_t *c = &S->cfg;
  S->pump_pending = 0;
  if (!ok || is_nan(v) || is_nan(i)) {
    S->pump_ok = 0;
    S->pump_have_prev = 0;  // the gap up to the next good reading is not measured
    if (S->pump_fails < c->pump_fail_limit) S->pump_fails++;
    if (!c->dry_run && S->pump_fails >= c->pump_fail_limit)
      stop_spray(S, now_ms, SPRAY_STOP_PUMP_SENSOR);
    return;
  }
  const float p = v * i;
  if (S->pump_have_prev) {  // trapezoid between two good readings
    const float dt_ms = (float)since(now_ms, S->t_pump_prev_ms);
    const float e = 0.5f * (S->p_prev_w + p) * dt_ms / 1000.0f;
    S->e_row_j += e;
    if (event_open(S)) S->e_event_j += e;
    S->t_valid_ms += (uint32_t)dt_ms;
  }
  S->p_prev_w = p;
  S->t_pump_prev_ms = now_ms;
  S->pump_have_prev = 1;
  S->pump_fails = 0;
  S->pump_ok = 1;
  if (!c->dry_run && i > c->i_max_a) stop_spray(S, now_ms, SPRAY_STOP_OVERCURRENT);
}

// ---- the 10 s row -------------------------------------------------------------------------

void spray_row(spray_t *S, uint32_t now_ms, const spray_row_in_t *in) {
  const spray_cfg_t *c = &S->cfg;
  const uint32_t half = c->row_period_ms / 2;  // rows are counted, so half a period of slack

  if (event_open(S) && (uint32_t)since(now_ms, S->t_event_ms) + half >= c->window_ms)
    close_event(S, now_ms);
  if (S->mode == SPRAY_LOCKOUT && (uint32_t)since(now_ms, S->t_lock_ms) + half >= c->lockout_ms)
    S->mode = SPRAY_IDLE;

  if (S->mode == SPRAY_ACTIVE) {  // stops that depend on this row's sensors
    if (in->row_fault || is_nan(in->t_module)) {
      stop_spray(S, now_ms, SPRAY_STOP_SENSOR);
    } else if (!c->dry_run && c->need_t_water && is_nan(in->t_water)) {
      stop_spray(S, now_ms, SPRAY_STOP_SENSOR);
    } else if (!c->dry_run && c->need_t_water &&
               !(in->t_module - in->t_water >= c->min_dt_water_c)) {
      stop_spray(S, now_ms, SPRAY_STOP_WATER_WARM);
    } else if (c->stop_drop_c > 0.0f && S->t_module_alarm - in->t_module >= c->stop_drop_c) {
      stop_spray(S, now_ms, SPRAY_STOP_TARGET);
    }
  }

  const uint32_t reason = cfg_ok(c) ? eligibility(S, in) : SPRAY_NO_BAD_CONFIG;
  S->blocked = reason == SPRAY_NO_EVENT_OPEN ? SPRAY_OK : reason;
  if (!in->alarm) return;
  if (reason != SPRAY_OK) {
    S->last_reason = reason;
    S->notes |= SPRAY_NOTE_REFUSED;
    return;
  }
  S->commit_pending = 1;
  S->pend_dry = c->dry_run ? 1 : 0;
  S->pend_count = (c->dry_run ? S->n_dry : S->n_wet) + 1;
  S->t_row_alarm_ms = now_ms;
  S->t_module_alarm = in->t_module;
}

void spray_commit(spray_t *S, uint32_t now_ms, int stored) {
  const spray_cfg_t *c = &S->cfg;
  if (!S->commit_pending) return;
  S->commit_pending = 0;
  if (!stored) {
    S->last_reason = SPRAY_NO_STORAGE;
    S->notes |= SPRAY_NOTE_REFUSED;
    return;
  }
  const uint32_t n = S->pend_count - 1;  // number of this event in its sequence, from 0
  if (S->pend_dry) {
    S->n_dry = S->pend_count;
    S->spike_id = -(int32_t)S->pend_count;
    S->assign = spray_alloc(dry_seed(c->seed), c->block, n);
  } else {
    S->n_wet = S->pend_count;
    S->spike_id = (int32_t)S->pend_count;
    S->assign = spray_alloc(c->seed, c->block, n);
  }
  S->t_event_ms = S->t_row_alarm_ms;
  S->sprayed = 0;
  S->stop_reason = SPRAY_STOP_NONE;
  S->spray_ms = 0;
  S->pulses_event = 0;
  S->e_event_j = 0.0f;
  S->blocked = SPRAY_OK;
  S->notes |= SPRAY_NOTE_OPENED;
  if (S->assign == 1) {
    S->mode = SPRAY_ACTIVE;
    S->sprayed = 1;
    S->t_spray_ms = now_ms;
    S->t_last_pulse_ms = now_ms;  // the no-flow clock starts with the pump
    set_duty(S, now_ms, c->dry_run ? 0.0f : c->pump_duty);
  } else {
    S->mode = SPRAY_WINDOW;
  }
}

const char *spray_row_header(void) {
  return ",spike_id,spray_assign,pump_duty,flow_Lpm,water_L_cum,P_pump_W";
}

uint32_t spray_row_fields(spray_t *S, uint32_t now_ms, char *buf, uint32_t size) {
  const spray_cfg_t *c = &S->cfg;
  uint32_t interval = (uint32_t)since(now_ms, S->t_meter_ms);
  if (interval == 0) interval = 1;
  set_duty(S, now_ms, S->duty);  // close the duty integral at this instant
  if (S->pump_have_prev) {       // hold the last reading up to the row boundary
    const float dt_ms = (float)since(now_ms, S->t_pump_prev_ms);
    const float e = S->p_prev_w * dt_ms / 1000.0f;
    S->e_row_j += e;
    if (event_open(S)) S->e_event_j += e;
    S->t_valid_ms += (uint32_t)dt_ms;
    S->t_pump_prev_ms = now_ms;
  }
  const float mean_duty = S->duty_ms / (float)interval;
  const float flow_lpm = (float)S->pulses_row / c->pulses_per_l / ((float)interval / 60000.0f);
  const float litres = (float)S->pulses_total / c->pulses_per_l;
  const int have_p = 2 * S->t_valid_ms >= interval;  // at least half the interval measured
  uint32_t status = 0;
  int n = snprintf(buf, size, ",%ld,%ld,%.3f,%.3f,%.4f,", (long)S->spike_id, (long)S->assign,
                   (double)mean_duty, (double)flow_lpm, (double)litres);
  if (have_p && n > 0 && (uint32_t)n < size)
    snprintf(buf + n, size - (uint32_t)n, "%.3f",
             (double)(S->e_row_j / ((float)S->t_valid_ms / 1000.0f)));
  if (!have_p) status |= SPRAY_ST_PUMP_FAIL;
  if (S->blocked) status |= SPRAY_ST_SPRAY_BLOCKED;
  if (S->abort_flag) status |= SPRAY_ST_SPRAY_ABORT;
  S->abort_flag = 0;
  S->duty_ms = 0.0f;
  S->pulses_row = 0;
  S->e_row_j = 0.0f;
  S->t_valid_ms = 0;
  S->t_meter_ms = now_ms;
  return status;
}

// ---- commands and faults ------------------------------------------------------------------

void spray_arm(spray_t *S, uint32_t now_ms, int on) {
  if (!on) stop_spray(S, now_ms, SPRAY_STOP_COMMAND);
  S->armed = (on && cfg_ok(&S->cfg)) ? 1 : 0;
}

void spray_reset_fault(spray_t *S) { S->fault_latched = 0; }

void spray_hard_stop(spray_t *S, uint32_t now_ms) {
  if (S->mode == SPRAY_ACTIVE) {
    stop_spray(S, now_ms, SPRAY_STOP_HARD_TIMER);
  } else {  // the pump was not meant to be on at all
    S->fault_latched = SPRAY_STOP_HARD_TIMER;
    S->abort_flag = 1;
  }
}

uint32_t spray_take_notes(spray_t *S) {
  const uint32_t n = S->notes;
  S->notes = 0;
  return n;
}

// ---- text and test access -----------------------------------------------------------------

const char *spray_mode_text(uint32_t mode) {
  static const char *const T[] = {"idle", "spray", "window", "lockout"};
  return mode <= SPRAY_LOCKOUT ? T[mode] : "?";
}

const char *spray_reason_text(uint32_t r) {
  static const char *const T[] = {
      "ok", "event open", "switched off (SPRAY OFF)", "lock-out", "pump fault latched",
      "row sensor missing", "storage not available", "too late in the day", "low light",
      "load not holding", "pump INA226 not readable", "no tank temperature",
      "water too warm", "thermal-shock limit", "bad configuration"};
  return r <= SPRAY_NO_BAD_CONFIG ? T[r] : "?";
}

const char *spray_stop_text(uint32_t r) {
  static const char *const T[] = {
      "none", "max time", "target reached", "water too warm", "sensor missing", "no flow",
      "overcurrent", "pump INA226 failing", "command", "hard timer"};
  return r <= SPRAY_STOP_HARD_TIMER ? T[r] : "?";
}

uint32_t spray_sizeof(void) { return (uint32_t)sizeof(spray_t); }
uint32_t spray_cfg_sizeof(void) { return (uint32_t)sizeof(spray_cfg_t); }
uint32_t spray_row_in_sizeof(void) { return (uint32_t)sizeof(spray_row_in_t); }

double spray_get(const spray_t *S, const char *f) {
#define F(name, value) if (!strcmp(f, name)) return (double)(value)
  F("mode", S->mode);
  F("duty", S->duty);
  F("spike_id", S->spike_id);
  F("assign", S->assign);
  F("armed", S->armed);
  F("fault_latched", S->fault_latched);
  F("n_wet", S->n_wet);
  F("n_dry", S->n_dry);
  F("commit_pending", S->commit_pending);
  F("pend_count", S->pend_count);
  F("pend_dry", S->pend_dry);
  F("last_reason", S->last_reason);
  F("blocked", S->blocked);
  F("pump_ok", S->pump_ok);
  F("notes", S->notes);
  F("stop_reason", S->stop_reason);
  F("spray_ms", S->spray_ms);
  F("pulses_total", S->pulses_total);
  F("done_spike_id", S->done.spike_id);
  F("done_assign", S->done.assign);
  F("done_sprayed", S->done.sprayed);
  F("done_stop_reason", S->done.stop_reason);
  F("done_spray_ms", S->done.spray_ms);
  F("done_pulses", S->done.pulses);
  F("done_energy_j", S->done.energy_j);
#undef F
  return -1e99;  // unknown field: the tests assert on this
}
