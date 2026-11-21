#include "pv_pump_logic.h"

static void pump_off(pump_t *p) {
  p->d = 0.0f;
  p->over_n = p->fail_n = p->dry_active = 0;
}

static void pump_trip(pump_t *p, uint32_t reason) {
  pump_off(p);
  p->d_req = 0.0f;
  p->state = PUMP_TRIP;
  p->trip_reason = reason;
}

void pump_init(pump_t *p, const pump_cfg_t *cfg, uint32_t now_ms) {
  p->cfg = *cfg;
  p->state = PUMP_IDLE;
  p->trip_reason = PUMP_TRIP_NONE;
  p->d_req = 0.0f;
  p->i_last = 0.0f;
  p->t_last_ms = p->t_start_ms = p->dry_since_ms = now_ms;
  pump_off(p);
}

void pump_request(pump_t *p, float duty) {
  if (p->state == PUMP_TRIP) return;
  if (!(duty >= p->cfg.d_min)) duty = 0.0f;  // also catches NaN
  if (duty > 1.0f) duty = 1.0f;
  p->d_req = duty;
}

float pump_service(pump_t *p, uint32_t now_ms) {
  uint32_t dt = now_ms - p->t_last_ms;  // wrap-around safe
  p->t_last_ms = now_ms;
  if (p->state == PUMP_TRIP) return 0.0f;
  if (p->d_req <= 0.0f) {
    pump_off(p);
    p->state = PUMP_IDLE;
    return 0.0f;
  }
  if (p->d <= 0.0f) p->t_start_ms = now_ms;  // leaving 0: a new start
  if (p->d < p->d_req) {
    if (dt > p->cfg.max_step_ms) dt = p->cfg.max_step_ms;
    p->d += p->cfg.ramp_ms ? (float)dt / (float)p->cfg.ramp_ms : 1.0f;
  }
  if (p->d >= p->d_req) {  // reached, or the request was lowered
    p->d = p->d_req;
    p->state = PUMP_RUN;
  } else {
    p->state = PUMP_RAMP;
    p->dry_active = 0;  // the dry-run check needs the full requested duty
  }
  return p->d;
}

void pump_reading(pump_t *p, uint32_t now_ms, float i_a, int ok) {
  if (p->state != PUMP_RAMP && p->state != PUMP_RUN) return;
  if (!ok) {
    if (++p->fail_n >= p->cfg.ina_fail_limit) pump_trip(p, PUMP_TRIP_SENSOR);
    return;
  }
  p->fail_n = 0;
  p->i_last = i_a;
  if (i_a > p->cfg.i_trip_a) {
    if (++p->over_n >= p->cfg.i_trip_n) pump_trip(p, PUMP_TRIP_OVERCURRENT);
    return;
  }
  p->over_n = 0;
  const int primed = now_ms - p->t_start_ms >= p->cfg.prime_ms;
  if (p->state != PUMP_RUN || !primed || !(i_a < p->cfg.i_dry_a)) {
    p->dry_active = 0;
    return;
  }
  if (!p->dry_active) {
    p->dry_active = 1;
    p->dry_since_ms = now_ms;
  } else if (now_ms - p->dry_since_ms >= p->cfg.dry_ms) {
    pump_trip(p, PUMP_TRIP_DRY);
  }
}

void pump_clear(pump_t *p) {
  if (p->state != PUMP_TRIP) return;
  p->state = PUMP_IDLE;
  p->trip_reason = PUMP_TRIP_NONE;
  p->d_req = 0.0f;
}

const char *pump_state_text(uint32_t state) {
  switch (state) {
    case PUMP_IDLE: return "off";
    case PUMP_RAMP: return "ramp";
    case PUMP_RUN: return "run";
    case PUMP_TRIP: return "trip";
    default: return "?";
  }
}

const char *pump_trip_text(uint32_t reason) {
  switch (reason) {
    case PUMP_TRIP_NONE: return "none";
    case PUMP_TRIP_OVERCURRENT: return "over-current";
    case PUMP_TRIP_DRY: return "dry run";
    case PUMP_TRIP_SENSOR: return "current sensor not readable";
    default: return "?";
  }
}

uint32_t pump_sizeof(void) { return (uint32_t)sizeof(pump_t); }
uint32_t pump_cfg_sizeof(void) { return (uint32_t)sizeof(pump_cfg_t); }
