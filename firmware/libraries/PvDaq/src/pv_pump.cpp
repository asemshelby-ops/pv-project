#include "pv_pump.h"

// 16 averages x 2 conversions x 1.1 ms = 35 ms per reading (TI datasheet). One 1.1 ms
// conversion already spans 22 PWM periods at 20 kHz, so the reading is the mean current.
static const uint8_t PUMP_INA_AVG = INA226_16_SAMPLES;
static const uint32_t PUMP_READ_TIMEOUT_MS = 150;  // 4 x the conversion + I2C traffic
static const uint8_t PUMP_LEDC_CHANNEL = 2;        // 0 and 1 are the load's (timer 0); 2 = timer 1

pump_cfg_t pvPumpConfig() {
  pump_cfg_t c = {};
  c.ramp_ms = PUMP_RAMP_MS;
  c.max_step_ms = PUMP_RAMP_MAX_STEP_MS;
  c.d_min = PUMP_D_MIN;
  c.i_trip_a = PUMP_I_TRIP_A;
  c.i_trip_n = PUMP_I_TRIP_N;
  c.i_dry_a = PUMP_I_DRY_A;
  c.prime_ms = PUMP_PRIME_MS;
  c.dry_ms = PUMP_DRY_MS;
  c.ina_fail_limit = PUMP_INA_FAIL_LIMIT;
  return c;
}

bool PvPump::begin() {
  const pump_cfg_t cfg = pvPumpConfig();
  pump_init(&P, &cfg, millis());
  pending_ = false;
  counts_ = 0;
  ok_ = ledcAttachChannel(pins::PUMP_PWM, PWM_FREQ_HZ, PWM_RES_BITS, PUMP_LEDC_CHANNEL);
  if (ok_) {
    ledcWrite(pins::PUMP_PWM, 0);
  } else {  // no PWM: hold the gate low for good
    ledcDetach(pins::PUMP_PWM);
    pinMode(pins::PUMP_PWM, OUTPUT);
    digitalWrite(pins::PUMP_PWM, LOW);
  }
  return ok_;
}

void PvPump::write() {
  if (!ok_) return;
  const uint32_t full = 1u << PWM_RES_BITS;
  uint32_t counts = (uint32_t)lroundf(P.d * (float)full);
  if (counts > full) counts = full;
  if (counts != counts_) ledcWrite(pins::PUMP_PWM, counts);
  counts_ = counts;
}

void PvPump::off() {
  pump_request(&P, 0.0f);
  pump_service(&P, millis());
  write();
}

void PvPump::service() {
  if (!ok_) return;
  const bool was_off = P.d <= 0.0f;
  pump_service(&P, millis());
  write();
  if (P.d <= 0.0f) {  // off or tripped: no readings; one in flight is dropped
    pending_ = false;
    return;
  }
  if (was_off) busV = currentA = powerW = NAN;  // a new start: no reading yet
  bool got = false, good = false;
  if (!pending_) {
    t_trig_ms_ = millis();
    pending_ = ina_.trigger(PUMP_INA_AVG);
    got = !pending_;  // the trigger itself failed = a failed reading
  } else if (ina_.ready()) {
    got = true;
    good = ina_.fetch();
  } else if (millis() - t_trig_ms_ > PUMP_READ_TIMEOUT_MS) {
    got = true;
  }
  if (!got) return;
  pending_ = false;
  if (good) {
    busV = ina_.busV;
    currentA = ina_.currentA;
    powerW = ina_.powerW;
  }
  pump_reading(&P, millis(), ina_.currentA, good ? 1 : 0);
  write();  // a trip: duty 0 on the pin at once
}
