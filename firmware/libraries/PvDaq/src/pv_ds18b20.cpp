#include "pv_ds18b20.h"

void PvDs18b20::romText(const uint8_t *r, char *out) {
  for (int i = 0; i < 8; i++) sprintf(out + 3 * i, i < 7 ? "%02X-" : "%02X", r[i]);
}

int PvDs18b20::indexOf(const uint8_t *r) const {
  for (uint8_t i = 0; i < count; i++)
    if (memcmp(rom[i], r, 8) == 0) return i;
  return -1;
}

bool PvDs18b20::begin() {
  dt_.begin();
  count = 0;
  uint8_t n = dt_.getDeviceCount();
  for (uint8_t i = 0; i < n && count < MAX_SENSORS; i++) {
    if (dt_.getAddress(rom[count], i)) {
      tempC[count] = NAN;
      count++;
    }
  }
  if (count == 0) return setStatus(PvStatus::NotFound, "no sensor (pull-up 4.7 kOhm fitted?)");
  dt_.setResolution(12);           // 0.0625 degC steps, 750 ms conversion (Maxim datasheet)
  dt_.setWaitForConversion(true);
  return setStatus(PvStatus::Ok);
}

bool PvDs18b20::read() {
  if (count == 0) return setStatus(PvStatus::NotFound, "no sensor");
  dt_.requestTemperatures();
  return fetch();
}

bool PvDs18b20::startConversion() {
  if (count == 0) return setStatus(PvStatus::NotFound, "no sensor");
  dt_.setWaitForConversion(false);
  dt_.requestTemperatures();
  dt_.setWaitForConversion(true);
  return true;
}

bool PvDs18b20::fetch() {
  if (count == 0) return setStatus(PvStatus::NotFound, "no sensor");
  uint8_t bad = 0;
  for (uint8_t i = 0; i < count; i++) {
    float t = dt_.getTempC(rom[i]);
    // -127 = no answer (library); +85.0 = the power-on value of the temperature register
    // (Maxim datasheet), i.e. a conversion that did not happen.
    if (t == DEVICE_DISCONNECTED_C || t == 85.0f) {
      t = NAN;
      bad++;
    }
    tempC[i] = t;
  }
  if (bad) {
    char d[48];
    snprintf(d, sizeof(d), "%u of %u sensors gave no valid reading", bad, count);
    return setStatus(PvStatus::ReadError, d);
  }
  return setStatus(PvStatus::Ok);
}
