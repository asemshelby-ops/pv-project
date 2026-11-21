// Pulse-output flow sensor on pins::FLOW_PULSE (GPIO35), counted by the ESP32 pulse counter
// (PCNT): the hardware counts every rising edge by itself, so no pulse is missed while the
// logger is busy with the SD card or a sweep, and no interrupt runs per pulse.
// Used only with HAS_FLOW 1 (pv_config.h).
//
// WIRING (firmware/PINMAP.md): GPIO35 is input-only and has NO internal pull-up or
// pull-down. The signal must swing between 0 V and at most 3.3 V at the pin: an
// open-collector output needs an external pull-up to 3.3 V; a sensor that drives 5 V needs
// a divider. With nothing connected the pin floats and counts noise: keep HAS_FLOW 0.
// The sensor model is not chosen yet, so its pulses per litre are not known:
// FLOW_PULSES_PER_L = 0 logs raw pulses only, until a bucket calibration fills it in.
#pragma once
#include <driver/pulse_cnt.h>
#include "pv_config.h"
#include "pv_device.h"
#include "pv_flow_logic.h"

class PvFlow : public PvDevice {
 public:
  explicit PvFlow(int pin = pins::FLOW_PULSE) : pin_(pin) {}
  bool begin() override;   // set up the counter and start it
  bool read() override;    // pulses and flow since the last read()
  const char *name() const override { return "flow sensor"; }

  uint32_t pulses = 0;     // in the interval that ended at the last read()
  uint32_t intervalMs = 0; // its length
  uint32_t totalPulses = 0;  // since begin()
  float litresPerMin = NAN;  // NaN while FLOW_PULSES_PER_L is 0

 private:
  static constexpr int LIMIT = 30000;  // the counter starts again at 0 here (16-bit hardware)
  int pin_;
  pcnt_unit_handle_t unit_ = nullptr;
  pcnt_channel_handle_t chan_ = nullptr;
  int last_count_ = 0;
  uint32_t last_ms_ = 0;
};
