// Measurement-load logic (docs/paper/07-load-firmware-spec.md): the mode machine
// OFF / HOLD / SWEEP / TRIP, the sweep duty sequences, the best-point search, the hold
// step and the fault rules. Plain C with no Arduino calls: it compiles on the PC and is
// tested there against a simulated module (tests/test_load_logic.py).
//
// The logic never touches hardware. The caller (pv_load.cpp on the board, the test on
// the PC) does three things, in a loop:
//   1. req = load_service(&L, now_ms, G, T_heatsink)
//   2. writes L.d_a and L.d_b to the two PWM outputs
//   3. if req != LOAD_REQ_NONE: takes one INA226 reading and gives it to load_reading()
//
// Settling of a sweep point. After a duty step the capacitor needs time to reach the new
// voltage; that time is not a constant (it grows as the module current falls). So a point
// is stored only when (a) its reading was triggered at least t_settle_ms after the step
// and (b) it agrees with the reading before it at the same duty within settle_tol. The
// first check reading is taken while t_settle_ms runs, so a settled point costs no extra
// time. If the slot (sweep_budget_ms) runs out, points are stored after t_settle_ms alone
// and the sweep carries LOAD_FLAG_NOT_SETTLED.
// Every time is millis(); differences are taken with wrap-around arithmetic.
//
// SAFE STATE: load_init(), LOAD_OFF and LOAD_TRIP all mean d_a = d_b = 0 (both MOSFETs
// off). Branch B is non-zero only in LOAD_SWEEP.
#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define LOAD_MAX_POINTS 64  // n_coarse + n_fine must not exceed this

enum { LOAD_OFF = 0, LOAD_HOLD = 1, LOAD_SWEEP = 2, LOAD_TRIP = 3 };  // spec section 3

// What the caller must measure now.
enum {
  LOAD_REQ_NONE = 0,
  LOAD_REQ_READ_HOLD = 1,   // 64-average reading (hold loop, also in OFF: gives V_oc)
  LOAD_REQ_READ_SWEEP = 2,  // 4-average reading (one sweep point)
};

enum {  // why the load tripped (spec section 8)
  LOAD_TRIP_NONE = 0,
  LOAD_TRIP_HEATSINK_HOT = 1,     // heatsink >= t_trip_c
  LOAD_TRIP_HEATSINK_SENSOR = 2,  // heatsink sensor missing or invalid (NaN)
  LOAD_TRIP_OVERCURRENT = 3,      // I > i_trip_a
};

// Sweep validity flags (spec section 4), stored in the sweep summary. 0 = clean.
#define LOAD_FLAG_G_UNSTABLE    (1u << 0)  // |G_after - G_before| / G_before > g_unstable
#define LOAD_FLAG_MAX_AT_EDGE   (1u << 1)  // best point is the first or last of the fine pass
#define LOAD_FLAG_NON_MONOTONIC (1u << 2)  // coarse pass: voltage rose while the duty rose
#define LOAD_FLAG_LOW_LIGHT     (1u << 3)  // G_before < g_on or V_oc < voc_on
#define LOAD_FLAG_INA_ERROR     (1u << 4)  // a reading failed: the sweep was cut short
#define LOAD_FLAG_NOT_SETTLED   (1u << 5)  // a point was stored although two readings in a row
                                           // still differed (no time left in the sweep slot)

typedef struct {
  uint32_t pwm_bits;          // LEDC resolution: duties are rounded to this grid
  uint32_t n_coarse, n_fine;  // sweep points per pass
  float d_min;                // first coarse duty (the last one is 1.0)
  uint32_t t_settle_ms;       // shortest wait after a duty step before the stored reading
                              // is triggered
  uint32_t sweep_conv_ms;     // time one sweep reading takes (INA226: 8.8 ms -> 9)
  float settle_tol;           // settled = two readings in a row differ by less than this
                              // fraction of V_oc
  uint32_t sweep_budget_ms;   // the sweep stops waiting for slow points when it would
                              // run longer than this
  uint32_t voc_settle_ms;     // wait at duty 0 before V_oc is read
  uint32_t sweep_period_ms;   // normal time between sweeps; 0 = only on request
  uint32_t gamma_period_ms;   // time between sweeps during a gamma run
  uint32_t sweep_guard_ms;    // a sweep starts only if the next 10 s row is at least this far
  uint32_t hold_period_ms;    // one hold step (and one reading) per period
  float hold_gain;            // K in D <- D * (1 + K * e)
  float hold_max_step;        // largest relative change of D per step (0.10 = 10 %)
  float hold_d_min;           // D is kept inside [hold_d_min, 1]
  float vset_fraction;        // V_set = fraction * V_oc before the first sweep
  float g_on, g_off;          // W/m2: OFF -> HOLD above g_on; HOLD -> OFF below g_off ...
  uint32_t g_off_ms;          // ... for this long
  float voc_on;               // V: OFF -> HOLD also needs V_oc >= this
  float t_trip_c;             // heatsink trip temperature
  float i_trip_a;             // current trip
  uint32_t trip_clear_ms;     // TRIP -> OFF after the cause has been absent this long
  uint32_t ina_fail_limit;    // failed readings in a row that freeze the duty
  float g_unstable;           // relative G change over a sweep that sets G_UNSTABLE
  float mono_tol;             // NON_MONOTONIC: tolerated voltage rise, as a fraction of V_oc
} load_cfg_t;

typedef struct {  // the last sweep (complete or cut short)
  uint32_t id;                // 1, 2, 3 ... since load_init; 0 = no sweep yet
  uint32_t n;                 // points stored: coarse first (k = 0 ..), then fine
  float d[LOAD_MAX_POINTS], v[LOAD_MAX_POINTS], i[LOAD_MAX_POINTS];
  float v_oc;
  uint32_t best;              // index of the point with the largest V * I
  uint32_t flags;
  float g_before, g_after;
  uint32_t t_start_ms, t_end_ms;
} load_sweep_t;

typedef struct {
  load_cfg_t cfg;
  // ---- outputs: what the PWM pins must carry, already rounded to the PWM grid ----
  float d_a, d_b;
  // ---- state the caller may read ----
  uint32_t mode;              // LOAD_OFF ..
  uint32_t trip_reason;       // LOAD_TRIP_..; kept after the trip clears, until the next one
  uint32_t ina_frozen;        // 1 = duty frozen after ina_fail_limit failed readings
  uint32_t manual_off;        // 1 = held in OFF by load_manual_off() (controlled test)
  uint32_t gamma_active;
  float v_set;                // hold target, V
  float v_last, i_last;       // last good reading
  uint32_t have_reading;      // 1 = v_last and i_last are valid
  uint32_t sweep_done;        // 1 = `sweep` is finished and waits for load_sweep_close()
  uint32_t last_sweep_id;     // id of the last finished sweep (the sweep_id row column)
  load_sweep_t sweep;
  // ---- internal ----
  float d_hold;               // hold duty before rounding (so small errors still add up)
  float d_resume, v_set_resume;  // hold state to go back to if a sweep is cut short
  uint32_t read_pending;      // a reading was requested and has not arrived yet
  uint32_t ina_fails;
  uint32_t t_next_read_ms;
  uint32_t t_next_sweep_ms;
  uint32_t sweep_req;         // one sweep asked for by load_sweep_request()
  uint32_t t_low_g_ms, low_g; // start of the current run of G < g_off
  uint32_t t_fault_ms;        // last time a trip cause was present
  uint32_t t_gamma_end_ms;
  uint32_t sw_phase, sw_k, sw_m, t_step_ms;
  uint32_t t_trig_ms;         // when the reading in flight was asked for
  uint32_t sw_have_prev;      // a reading at the present duty has already been seen
  float sw_v_prev;
} load_t;

// Starts in LOAD_OFF with both duties 0.
void load_init(load_t *L, const load_cfg_t *cfg, uint32_t now_ms);

// Call on every loop pass. g: irradiance in W/m2 (NaN = not known: the light rules then
// change nothing). t_heatsink: degC (NaN = sensor missing -> TRIP). Returns LOAD_REQ_*.
// After a request the logic waits for load_reading() and asks for nothing else.
uint32_t load_service(load_t *L, uint32_t now_ms, float g, float t_heatsink);

// One INA226 reading. ok = 0: the read failed (v and i are ignored).
void load_reading(load_t *L, uint32_t now_ms, float v, float i, int ok);

// 1 if a sweep may start now: mode HOLD, period elapsed, readings healthy, no reading in
// flight, and the next 10 s row at least sweep_guard_ms away.
int load_sweep_due(const load_t *L, uint32_t now_ms, uint32_t ms_to_next_row);
// Starts the sweep (if due). g_before: irradiance read just before. Returns 1 if started.
int load_sweep_start(load_t *L, uint32_t now_ms, uint32_t ms_to_next_row, float g_before);
// Asks for one sweep as soon as load_sweep_due() allows it (bench command).
void load_sweep_request(load_t *L, uint32_t now_ms);
// After sweep_done: adds the G flags, clears sweep_done, returns the final flags.
uint32_t load_sweep_close(load_t *L, float g_after);

// Gamma run: sweeps every gamma_period_ms for `minutes`, then back to sweep_period_ms.
void load_gamma(load_t *L, uint32_t now_ms, uint32_t minutes);
// Controlled test: on = 1 forces LOAD_OFF (open circuit) until called again with on = 0.
void load_manual_off(load_t *L, uint32_t now_ms, int on);

// PWM compare value for a duty: 0 .. 2^pwm_bits - 1 (the top value = always on).
uint32_t load_counts(const load_t *L, float d);
const char *load_mode_text(uint32_t mode);         // "off", "hold", "sweep", "trip"
const char *load_trip_text(uint32_t trip_reason);

// For the PC tests (ctypes): sizes and field access without mirroring load_t.
uint32_t load_sizeof(void);
uint32_t load_cfg_sizeof(void);
double load_get(const load_t *L, const char *field);
double load_sweep_point(const load_t *L, uint32_t k, int what);  // what: 0 = D, 1 = V, 2 = I

#ifdef __cplusplus
}
#endif
