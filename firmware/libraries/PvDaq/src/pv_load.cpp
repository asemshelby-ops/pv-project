#include "pv_load.h"

// One reading with 1.1 ms conversions takes 2 x 1.1 ms x averages (TI datasheet):
// 8.8 ms with 4 averages, 141 ms with 64. The time-out is three times that plus the
// I2C traffic; a reading that has not arrived by then counts as failed.
static uint32_t readTimeoutMs(uint8_t avg) { return avg == INA226_64_SAMPLES ? 450 : 50; }

load_cfg_t pvLoadConfig() {
  load_cfg_t c = {};
  c.pwm_bits = PWM_RES_BITS;
  c.n_coarse = SWEEP_N_COARSE;
  c.n_fine = SWEEP_N_FINE;
  c.d_min = SWEEP_D_MIN;
  c.t_settle_ms = T_SETTLE_MS;
  c.sweep_conv_ms = SWEEP_CONV_MS;
  c.settle_tol = SWEEP_SETTLE_TOL;
  c.sweep_budget_ms = SWEEP_BUDGET_MS;
  c.voc_settle_ms = VOC_SETTLE_MS;
  c.sweep_period_ms = SWEEP_PERIOD_S * 1000;
  c.gamma_period_ms = GAMMA_SWEEP_PERIOD_S * 1000;
  c.sweep_guard_ms = SWEEP_GUARD_MS;
  c.hold_period_ms = HOLD_PERIOD_MS;
  c.hold_gain = HOLD_GAIN;
  c.hold_max_step = HOLD_MAX_STEP;
  c.hold_d_min = HOLD_D_MIN;
  c.vset_fraction = VSET_FRACTION_OF_VOC;
  c.g_on = LOAD_G_ON_WM2;
  c.g_off = LOAD_G_OFF_WM2;
  c.g_off_ms = LOAD_G_OFF_MS;
  c.voc_on = LOAD_VOC_ON_V;
  c.t_trip_c = T_TRIP_C;
  c.i_trip_a = I_TRIP_A;
  c.trip_clear_ms = TRIP_CLEAR_MS;
  c.ina_fail_limit = INA_FAIL_LIMIT;
  c.g_unstable = SWEEP_G_UNSTABLE;
  c.mono_tol = SWEEP_MONO_TOL;
  return c;
}

bool PvLoad::begin() {
  const load_cfg_t cfg = pvLoadConfig();
  load_init(&L, &cfg, millis());
  manual_ = pending_ = false;
  counts_a_ = counts_b_ = 0;
  // Channels 0 and 1 share LEDC timer 0 (ESP32 Arduino core 3.x): same frequency and phase.
  ok_ = ledcAttachChannel(pins::LOAD_A_PWM, PWM_FREQ_HZ, PWM_RES_BITS, 0) &&
        ledcAttachChannel(pins::LOAD_B_PWM, PWM_FREQ_HZ, PWM_RES_BITS, 1);
  if (ok_) {
    ledcWrite(pins::LOAD_A_PWM, 0);
    ledcWrite(pins::LOAD_B_PWM, 0);
  } else {  // no PWM: hold both gates low for good
    ledcDetach(pins::LOAD_A_PWM);
    ledcDetach(pins::LOAD_B_PWM);
    pinMode(pins::LOAD_A_PWM, OUTPUT);
    digitalWrite(pins::LOAD_A_PWM, LOW);
    pinMode(pins::LOAD_B_PWM, OUTPUT);
    digitalWrite(pins::LOAD_B_PWM, LOW);
  }
  return ok_;
}

void PvLoad::write() {
  if (!ok_) return;
  const uint32_t a = load_counts(&L, manual_ ? man_a_ : L.d_a);
  const uint32_t b = load_counts(&L, manual_ ? man_b_ : L.d_b);
  if (b != counts_b_) ledcWrite(pins::LOAD_B_PWM, b);
  if (a != counts_a_) ledcWrite(pins::LOAD_A_PWM, a);
  counts_a_ = a;
  counts_b_ = b;
}

void PvLoad::off() {
  const load_cfg_t cfg = L.cfg;
  load_init(&L, &cfg, millis());
  manual_ = pending_ = false;
  write();
}

void PvLoad::manual(float d_a, float d_b) {
  manual_ = true;
  man_a_ = d_a;
  man_b_ = d_b;
  write();
}

bool PvLoad::startRead(uint8_t avg, bool for_logic) {
  for_logic_ = for_logic;
  t_trig_ms_ = millis();
  timeout_ms_ = readTimeoutMs(avg);
  pending_ = true;
  if (!ina_.trigger(avg)) deliver(false);
  return pending_;
}

void PvLoad::deliver(bool ok) {
  pending_ = false;
  last_ok_ = ok;
  if (for_logic_ && !manual_) {
    load_reading(&L, millis(), ina_.busV, ina_.currentA, ok ? 1 : 0);
    write();  // a hold step, the next sweep duty, or a trip: on the pins at once
  }
}

void PvLoad::poll() {
  if (ina_.ready()) deliver(ina_.fetch());
  else if (millis() - t_trig_ms_ > timeout_ms_) deliver(false);
}

void PvLoad::service(float g, float t_heatsink) {
  g_ = g;
  th_ = t_heatsink;
  if (!ok_) return;
  const uint32_t req = manual_ ? (uint32_t)LOAD_REQ_NONE : load_service(&L, millis(), g, t_heatsink);
  write();
  if (pending_) poll();
  else if (req) startRead(req == LOAD_REQ_READ_SWEEP ? INA226_4_SAMPLES : INA226_64_SAMPLES, true);
}

bool PvLoad::sweepStart(uint32_t ms_to_next_row, float g_before) {
  if (!ok_ || manual_ || pending_) return false;
  if (!load_sweep_start(&L, millis(), ms_to_next_row, g_before)) return false;
  write();  // duty 0 on both branches: the V_oc step
  return true;
}

void PvLoad::finishSweep() {
  const uint32_t t0 = millis();
  while (L.mode == LOAD_SWEEP) {
    service(g_, th_);
    if (millis() - t0 > 2 * SWEEP_BUDGET_MS) {  // cannot happen while the logic keeps its budget
      off();
      return;
    }
    delayMicroseconds(200);
  }
}

bool PvLoad::rowReading() {
  uint32_t t0 = millis();
  while (pending_ && millis() - t0 < 600) {  // let a reading in flight finish
    poll();
    if (pending_) delay(1);
  }
  if (!startRead(INA226_64_SAMPLES, L.mode != LOAD_SWEEP)) return false;
  while (pending_) {  // ends by the reading or by its time-out
    delay(1);
    poll();
  }
  return last_ok_;
}
