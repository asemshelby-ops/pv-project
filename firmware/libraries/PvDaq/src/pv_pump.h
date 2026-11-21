// Spray pump on the ESP32: one PWM output (LEDC, pins::PUMP_PWM -> logic-level MOSFET), the
// pump's INA226 in triggered mode, and the timing around pv_pump_logic.c, which decides
// (soft start, over-current, dry run). Used only with ENABLE_PUMP 1 (pv_config.h).
//
// SAFE STATE: begin() starts with the pump off. A trip, off() and a reset all mean duty 0;
// after a reset the pin floats and the 10 kOhm gate pull-down keeps the MOSFET off.
// What this does NOT replace: the fuse on the pump line (a hard short is faster than any
// reading) and the flyback diode across the pump.
// Reaction time: a reading takes ~35 ms (16 averages), a trip needs PUMP_I_TRIP_N of them,
// and they are only taken while service() is called. The logger calls it on every loop pass
// and inside its waits (DS18B20 conversion, RTC second edge), but not during an SD-card or
// flash write or a device restart: tens of ms normally, seconds when an SD mount fails [EST].
#pragma once
#include "pv_config.h"
#include "pv_ina226.h"
#include "pv_pump_logic.h"

pump_cfg_t pvPumpConfig();   // the parameters of pv_config.h, as the logic takes them

class PvPump {
 public:
  explicit PvPump(PvIna226 &ina) : ina_(ina) {}

  // Attaches the pin to LEDC at duty 0 and puts the logic in IDLE. False if LEDC refused:
  // the pin is then driven LOW and stays there.
  bool begin();
  bool ok() const { return ok_; }

  void request(float duty) { pump_request(&P, duty); }   // 0..1; the ramp does the rest
  void service();       // call on every loop pass: ramp, PWM, current readings, trips
  void off();           // request 0 and duty 0 on the pin now
  void clear() { pump_clear(&P); }                        // after a trip, by a person

  pump_t P;             // state: P.state, P.d, P.trip_reason, P.i_last (pv_pump_logic.h)
  // Last good reading taken while the pump ran (NaN: none since it last started).
  float busV = NAN, currentA = NAN, powerW = NAN;

 private:
  void write();
  PvIna226 &ina_;
  bool ok_ = false, pending_ = false;
  uint32_t counts_ = 0, t_trig_ms_ = 0;
};
