#include "pv_flow_logic.h"

#include <math.h>

uint32_t flow_pulses_since(int count_now, int count_last, int limit) {
  int d = count_now - count_last;
  if (d < 0) d += limit;  // the counter reached its limit and started again at 0
  return (uint32_t)d;
}

float flow_l_per_min(uint32_t pulses, uint32_t dt_ms, float pulses_per_l) {
  if (!(pulses_per_l > 0.0f) || dt_ms == 0) return NAN;
  return (float)pulses / pulses_per_l * 60000.0f / (float)dt_ms;
}
