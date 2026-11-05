// DS3231 real-time clock (+ AT24C32 EEPROM on the same module, 0x57). Uses RTClib.
// The RTC holds Makkah LOCAL time (UTC+3, no daylight saving): the CSV and NASIM's
// solar geometry both expect local time (export/c/nasim.c: tz_hours = 3).
#pragma once
#include <RTClib.h>
#include "pv_config.h"
#include "pv_device.h"

class PvDs3231 : public PvDevice {
 public:
  explicit PvDs3231(TwoWire &wire = Wire) : wire_(wire) {}
  bool begin() override;   // probe 0x68; reports lostPower (battery flat / never set)
  bool read() override;    // time + die temperature
  const char *name() const override { return "DS3231"; }

  void set(const DateTime &t) { rtc_.adjust(t); timeValid = true; }

  DateTime time() const { return DateTime(unix_); }   // time of the last read()
  float temperatureC = NAN;     // die temperature, 0.25 degC steps (for info only)
  bool timeValid = false;       // false after a power loss until set()

 private:
  TwoWire &wire_;
  RTC_DS3231 rtc_;
  uint32_t unix_ = 0;   // stored as seconds: RTClib's DateTime has no copy-assignment
};
