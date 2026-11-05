#include "pv_ds3231.h"

bool PvDs3231::begin() {
  if (!rtc_.begin(&wire_)) return setStatus(PvStatus::NotFound, "no ACK at 0x68");
  timeValid = !rtc_.lostPower();
  return setStatus(PvStatus::Ok, timeValid ? "" : "oscillator stopped: time not set");
}

bool PvDs3231::read() {
  wire_.beginTransmission(DS3231_ADDR);
  if (wire_.endTransmission() != 0) return setStatus(PvStatus::ReadError, "lost on the bus");
  DateTime t = rtc_.now();
  temperatureC = rtc_.getTemperature();
  if (!t.isValid()) return setStatus(PvStatus::ReadError, "invalid date read");
  unix_ = t.unixtime();
  return setStatus(PvStatus::Ok, timeValid ? "" : "time not set");
}
