// NASIM update timing: see pv_timing.h.
#include "pv_timing.h"

#include <stdio.h>
#include <string.h>

void timing_init(pv_timing_t *t, uint32_t period_ms, uint32_t now_ms) {
  memset(t, 0, sizeof(*t));
  t->period_ms = period_ms;
  t->t_start_ms = now_ms;
}

void timing_add(pv_timing_t *t, uint32_t us, int inference) {
  if (inference) {
    t->n++;
    t->sum_us += us;
    if (us > t->max_us) t->max_us = us;
  } else {
    t->n_warm++;
    t->sum_warm_us += us;
    if (us > t->max_warm_us) t->max_warm_us = us;
  }
}

int timing_due(const pv_timing_t *t, uint32_t now_ms) {
  return (uint32_t)(now_ms - t->t_start_ms) >= t->period_ms;  // wrap-safe
}

const char *timing_header(void) {
  return "nasim_n,nasim_us_mean,nasim_us_max,warmup_n,warmup_us_mean,warmup_us_max";
}

int timing_line(pv_timing_t *t, uint32_t now_ms, char *buf, uint32_t size) {
  char mean[24] = "", mean_warm[24] = "";
  if (t->n) snprintf(mean, sizeof(mean), "%.1f", (double)t->sum_us / (double)t->n);
  if (t->n_warm)
    snprintf(mean_warm, sizeof(mean_warm), "%.1f", (double)t->sum_warm_us / (double)t->n_warm);
  const int len = snprintf(buf, size, "%lu,%s,%lu,%lu,%s,%lu", (unsigned long)t->n, mean,
                           (unsigned long)t->max_us, (unsigned long)t->n_warm, mean_warm,
                           (unsigned long)t->max_warm_us);
  const uint32_t period = t->period_ms;
  timing_init(t, period, now_ms);
  return len;
}

uint32_t timing_sizeof(void) { return (uint32_t)sizeof(pv_timing_t); }
