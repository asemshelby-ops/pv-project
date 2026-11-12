// Measurement load on the ESP32: two PWM outputs (LEDC), the panel INA226 in triggered
// mode, and the timing around pv_load_logic.c, which makes every decision.
// Design: docs/paper/07-load-firmware-spec.md. Bench procedure: tests/load_bench/BENCH.md.
//
// SAFE STATE: begin() starts with both branches off. off() switches both off at once.
// A reset releases the pins; the 10 kOhm pull-downs at the gate-driver inputs then hold
// both MOSFETs off (bench step B1).
#pragma once
#include "pv_config.h"
#include "pv_ina226.h"
#include "pv_load_logic.h"

load_cfg_t pvLoadConfig();   // the parameters of pv_config.h, as the logic takes them

class PvLoad {
 public:
  explicit PvLoad(PvIna226 &ina) : ina_(ina) {}

  // Attaches both pins to LEDC (20 kHz, same timer, so same phase) at duty 0 and puts the
  // logic in OFF. False if LEDC refused: the pins are then driven LOW and stay there.
  bool begin();
  bool ok() const { return ok_; }

  // Call on every loop pass: runs the logic, writes the duties, takes the readings.
  // g: irradiance W/m2 (NaN if unknown); t_heatsink: degC (NaN = sensor missing -> TRIP).
  void service(float g, float t_heatsink);

  // I-V sweep. sweepStart() begins one if the logic allows it; finishSweep() then runs it
  // to its end (about 1.7 s, blocking). Afterwards L.sweep_done is 1 and L.sweep holds it.
  bool sweepStart(uint32_t ms_to_next_row, float g_before);
  void finishSweep();

  // A fresh 64-average reading for the 10 s row (blocks ~0.15 s). False if it failed.
  // The values are in the INA226 driver (busV, currentA, powerW).
  bool rowReading();

  void off();                          // both branches off now; logic back to its start
  void manual(float d_a, float d_b);   // BENCH ONLY: fixed duties, the logic is bypassed
  bool isManual() const { return manual_; }

  load_t L;                            // state: L.mode, L.d_a, L.sweep, ... (pv_load_logic.h)

 private:
  void write();
  bool startRead(uint8_t avg, bool for_logic);
  void poll();
  void deliver(bool ok);

  PvIna226 &ina_;
  bool ok_ = false, manual_ = false;
  float man_a_ = 0, man_b_ = 0;
  float g_ = NAN, th_ = NAN;           // last inputs, reused inside finishSweep()
  uint32_t counts_a_ = 0, counts_b_ = 0;
  bool pending_ = false, for_logic_ = false, last_ok_ = false;
  uint32_t t_trig_ms_ = 0, timeout_ms_ = 0;
};
