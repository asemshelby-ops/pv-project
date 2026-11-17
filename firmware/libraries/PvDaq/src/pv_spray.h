// Phase B randomized spray trial on the ESP32: the pump's PWM output, the flow pulse
// counter, the pump-rail INA226, the stored event counters and an independent cut-off
// timer, around pv_spray_logic.c, which makes every decision.
// Design: docs/phase-b/01-randomized-spray.md. NOT ADOPTED and NOT RUN ON HARDWARE: written
// 2026-11-17 before any pump, flow sensor or tank exists.
//
// This file is empty unless the logger is built with PHASE_B_ENABLED 1 (pv_config.h).
// Every value comes from the generated header phase_b_config.h (config.py -> python -m
// export.phase_b_header); the include path to export/c/generated is set by
// firmware/build_all.py, as for the NASIM headers.
//
// SAFE STATE (the pump is OFF at boot, on reset and on every fault):
//   * boot:   pvOutputsOff() drives the pump pin LOW first; begin() attaches the PWM at duty 0;
//   * reset:  the pin is released and the 10 kOhm gate pull-down holds the MOSFET off
//             (PINMAP.md) -- a hardware requirement, to check on the bench;
//   * fault:  every stop in pv_spray_logic.c sets duty 0; an independent one-shot timer
//             switches the PWM off if the loop is late; the loop watchdog resets the chip
//             if the loop stalls.
//   * dry run (PHASE_B_DRY_RUN 1, the default): the PWM is never attached; the pin stays LOW.
#pragma once
#include "pv_config.h"

#if PHASE_B_ENABLED
#include <Preferences.h>
#include <driver/pulse_cnt.h>
#include <esp_timer.h>

#include "phase_b_config.h"
#include "pv_ina226.h"
#include "pv_spray_logic.h"

#if !PHASE_B_DRY_RUN
#if !PHASE_B_PLACEHOLDERS_CONFIRMED
#error "Phase B wet run: config.py still holds [PLACEHOLDER] values. Replace them, set PHASE_B_PLACEHOLDERS_CONFIRMED = True, run python -m export.phase_b_header."
#endif
#endif

// The pump pin is pins::PUMP_PWM (PINMAP.md). It must never be a load pin.
static_assert(pins::PUMP_PWM != pins::LOAD_A_PWM && pins::PUMP_PWM != pins::LOAD_B_PWM,
              "the pump PWM pin must not be a load pin (GPIO33 / GPIO14)");
// The flow input must be a pin nothing else uses.
static_assert(FLOW_PIN != pins::I2C_SDA && FLOW_PIN != pins::I2C_SCL &&
              FLOW_PIN != pins::ONEWIRE && FLOW_PIN != pins::SD_SCK &&
              FLOW_PIN != pins::SD_MISO && FLOW_PIN != pins::SD_MOSI &&
              FLOW_PIN != pins::SD_CS && FLOW_PIN != pins::RS485_TX &&
              FLOW_PIN != pins::RS485_RX && FLOW_PIN != pins::PYR20_ANALOG &&
              FLOW_PIN != pins::PUMP_PWM && FLOW_PIN != pins::LOAD_A_PWM &&
              FLOW_PIN != pins::LOAD_B_PWM && FLOW_PIN != pins::MARKER_BUTTON,
              "FLOW_PIN (config.py) is already used: see firmware/PINMAP.md");
static_assert(SPRAY_MAX_MS + SAMPLE_PERIOD_MS < PHASE_B_EVENT_WINDOW_MS,
              "the longest spray must end well inside the event window");
static_assert(PHASE_B_BLOCK_SIZE >= 2 && PHASE_B_BLOCK_SIZE <= SPRAY_MAX_BLOCK &&
              PHASE_B_BLOCK_SIZE % 2 == 0, "PHASE_B_BLOCK_SIZE must be even, 2 .. 16");

// LEDC channel of the pump. The load uses channels 0 and 1 (one timer); channel 2 is on
// the next timer (ESP32 Arduino core 3.x: two channels per timer), so it does not touch
// the load's PWM. Same frequency and resolution as the load (pv_config.h).
constexpr uint8_t PUMP_LEDC_CHANNEL = 2;
// One pump-rail reading: 1.1 ms conversions x 2 x 64 averages = 141 ms (TI datasheet).
constexpr uint32_t PUMP_CONV_MS = 145;
constexpr uint32_t PUMP_READ_TIMEOUT_MS = 450;   // then the reading counts as failed
// The independent timer cuts the pump this long after the longest spray should have ended.
constexpr uint32_t SPRAY_HARD_MARGIN_MS = 3000;  // [ASSUMPTION] above the longest loop stall
// The PCNT counter runs from 0 up to this value and starts again at 0 (ESP-IDF pulse_cnt).
constexpr int FLOW_COUNT_LIMIT = 32767;
constexpr uint32_t FLOW_GLITCH_NS = 10000;       // [ASSUMPTION] pulses shorter than 10 us ignored

spray_cfg_t pvSprayConfig();   // the values of phase_b_config.h, as the logic takes them

class PvSpray {
 public:
  explicit PvSpray(PvIna226 &pump_ina) : ina_(pump_ina) {}

  // Pump pin LOW, PWM at duty 0 (not attached at all in a dry run), flow counter started,
  // event counters read from flash. False if any part failed: the trial is then switched
  // off (no event opens) and detail() says why.
  bool begin();
  const char *detail() const { return detail_; }
  bool storageOk() const { return prefs_ok_; }

  // Every loop pass: flow pulses, the logic's time-based stops, the pump PWM, one
  // pump-rail reading when due.
  void service();
  // Every 10 s row, after the detectors. Stores the event counter before an event opens.
  void row(const spray_row_in_t &in);
  // The six columns of the row (leading comma) and its SPRAY_ST_* status bits.
  uint32_t rowFields(char *buf, uint32_t size) { return spray_row_fields(&S, millis(), buf, size); }

  void arm(bool on);            // SPRAY ON / SPRAY OFF; kept across restarts
  void resetFault() { spray_reset_fault(&S); }
  bool pumpOn() const { return S.duty > 0.0f; }

  spray_t S;                    // state: S.mode, S.spike_id, S.assign, S.done ... (pv_spray_logic.h)

 private:
  void write();
  void pollPump();
  bool beginFlow();
  static void hardStop(void *self);

  PvIna226 &ina_;
  Preferences prefs_;
  bool pwm_ok_ = false, flow_ok_ = false, prefs_ok_ = false, init_ok_ = false;
  char detail_[64] = "";
  uint32_t counts_ = 0;                    // PWM compare value on the pin now
  pcnt_unit_handle_t pcnt_ = nullptr;
  int flow_last_ = 0;
  bool reading_ = false;
  uint32_t t_trig_ms_ = 0;
  esp_timer_handle_t timer_ = nullptr;
  volatile bool hard_fired_ = false;
};
#endif  // PHASE_B_ENABLED
