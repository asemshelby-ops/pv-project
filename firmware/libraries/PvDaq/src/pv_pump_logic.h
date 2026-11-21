// Pump protection logic: PWM soft-start, over-current cut-off, dry-run cut-off, all from
// the pump's own INA226. Plain C with no Arduino calls: it compiles on the PC and is
// tested there (tests/test_pump_logic.py). pv_pump.cpp is the hardware side.
//
// The caller, in a loop:
//   1. duty = pump_service(&P, now_ms)        and writes it to the PWM output
//   2. while duty > 0: takes INA226 readings and gives each to pump_reading()
// Somebody else decides WHEN to pump and how hard (pump_request): this file only makes
// sure the pump starts gently and is switched off when its current says something is wrong.
//
//   IDLE  duty 0, nothing requested
//   RAMP  soft start: the duty climbs by 1/ramp_ms per millisecond to the request. A loop
//         that was busy for a while does not make it jump (max_step_ms per call).
//         Going DOWN, or to 0, is immediate.
//   RUN   duty = request
//   TRIP  duty 0 until pump_clear(): a person looks at the pump first. Reasons:
//           over-current  i_trip_n readings in a row above i_trip_a (blocked pump or
//                         nozzle, short circuit; one reading alone is a start-up peak)
//           dry run       current below i_dry_a for dry_ms, once the pump has been at
//                         its requested duty and prime_ms have passed since it started
//                         (a self-priming pump draws air at first). A pump that moves no
//                         water draws less current [ASSUMPTION: to be shown in the bucket test]
//           sensor        ina_fail_limit failed readings in a row: without the current
//                         there is no protection, so the pump may not run
//
// Every time is millis(); differences are taken with wrap-around arithmetic.
#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum { PUMP_IDLE = 0, PUMP_RAMP = 1, PUMP_RUN = 2, PUMP_TRIP = 3 };
enum { PUMP_TRIP_NONE = 0, PUMP_TRIP_OVERCURRENT = 1, PUMP_TRIP_DRY = 2, PUMP_TRIP_SENSOR = 3 };

typedef struct {
  uint32_t ramp_ms;         // soft start: time from duty 0 to duty 1
  uint32_t max_step_ms;     // longest time one pump_service() call may advance the ramp by
  float d_min;              // a request below this is treated as 0 (the pump would stall)
  float i_trip_a;           // over-current threshold, A
  uint32_t i_trip_n;        // readings in a row above it that trip
  float i_dry_a;            // dry-run threshold, A
  uint32_t prime_ms;        // no dry-run check before this long after the start
  uint32_t dry_ms;          // low current for this long = dry run
  uint32_t ina_fail_limit;  // failed readings in a row that trip
} pump_cfg_t;

typedef struct {
  pump_cfg_t cfg;
  uint32_t state;           // PUMP_IDLE ...
  uint32_t trip_reason;     // PUMP_TRIP_NONE ...
  float d;                  // duty to write now, 0..1
  float d_req;              // requested duty
  float i_last;             // last good current reading, A (at a trip: the reading that tripped)
  uint32_t t_last_ms;       // last pump_service()
  uint32_t t_start_ms;      // when the duty last left 0
  uint32_t over_n, fail_n;  // readings in a row: above i_trip_a / failed
  uint32_t dry_active;      // 1 = current is below i_dry_a since dry_since_ms
  uint32_t dry_since_ms;
} pump_t;

void pump_init(pump_t *p, const pump_cfg_t *cfg, uint32_t now_ms);
void pump_request(pump_t *p, float duty);              // 0..1; ignored while tripped
float pump_service(pump_t *p, uint32_t now_ms);        // the duty to write now
void pump_reading(pump_t *p, uint32_t now_ms, float i_a, int ok);
void pump_clear(pump_t *p);                            // TRIP -> IDLE (request = 0)
const char *pump_state_text(uint32_t state);
const char *pump_trip_text(uint32_t reason);
uint32_t pump_sizeof(void);      // for the PC test: its mirrors of the structs must match
uint32_t pump_cfg_sizeof(void);

#ifdef __cplusplus
}
#endif
