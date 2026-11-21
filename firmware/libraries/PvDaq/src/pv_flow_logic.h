// Flow-sensor arithmetic, plain C, tested on the PC (tests/test_flow_logic.py).
#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Pulses counted since the last reading. The hardware counter runs 0 .. limit-1 and starts
// again at 0 when it reaches `limit`; fewer than `limit` pulses may arrive between readings.
uint32_t flow_pulses_since(int count_now, int count_last, int limit);

// Litres per minute from `pulses` in `dt_ms`. pulses_per_l is the sensor's calibration;
// 0 (not calibrated yet) or dt_ms = 0 gives NaN: the pulses are then the only figure.
float flow_l_per_min(uint32_t pulses, uint32_t dt_ms, float pulses_per_l);

#ifdef __cplusplus
}
#endif
