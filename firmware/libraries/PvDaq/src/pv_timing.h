// Time of the NASIM update on the board, collected over an hour and written as one line
// of the status file /status_log.csv (docs/phase-b/04-model-footprint.md). Used by the
// logger only when it is built with NASIM_TIMING_LOG 1 (pv_config.h); NOT measured yet:
// there is no number until the board has run with it.
//
// Two kinds of update are kept apart, because they cost very different amounts:
//   inference   the buffers are full: features, thermal model, CUSUM and the 50 trees
//   warm-up     the first 10 min of a detector run: the buffers fill, no model call
// Plain C, tested on the PC (tests/test_timing.py). Times are micros() differences; the
// period is counted with millis(), wrap-safe.
#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
  uint32_t period_ms;        // one line per period (3 600 000 = one hour)
  uint32_t t_start_ms;       // start of the period being collected
  uint32_t n, max_us;        // inference updates: count, slowest
  uint64_t sum_us;
  uint32_t n_warm, max_warm_us;
  uint64_t sum_warm_us;
} pv_timing_t;

void timing_init(pv_timing_t *t, uint32_t period_ms, uint32_t now_ms);
// One update that took `us` microseconds. inference = 1 if the model ran (p is a number).
void timing_add(pv_timing_t *t, uint32_t us, int inference);
// 1 when the period is over and a line must be written.
int timing_due(const pv_timing_t *t, uint32_t now_ms);
// Writes "n,mean_us,max_us,n_warm,mean_warm_us,max_warm_us" (a mean is empty when its
// count is 0) and starts the next period. Returns the number of characters.
int timing_line(pv_timing_t *t, uint32_t now_ms, char *buf, uint32_t size);
// The column names of that text.
const char *timing_header(void);

uint32_t timing_sizeof(void);   // for the PC test

#ifdef __cplusplus
}
#endif
