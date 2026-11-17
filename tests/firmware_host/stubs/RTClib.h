// HOST STUB (see Arduino.h in this folder).
#pragma once
#include <Wire.h>
class TimeSpan {
 public:
  explicit TimeSpan(int32_t seconds);
};
class DateTime {
 public:
  explicit DateTime(uint32_t unix_s = 0);
  DateTime(uint16_t year, uint8_t month, uint8_t day, uint8_t hour = 0, uint8_t min = 0,
           uint8_t sec = 0);
  uint16_t year() const;
  uint8_t month() const;
  uint8_t day() const;
  uint8_t hour() const;
  uint8_t minute() const;
  uint8_t second() const;
  uint32_t unixtime() const;
  DateTime operator+(const TimeSpan &span) const;
};
class RTC_DS3231 {
 public:
  bool begin(TwoWire *wire);
  bool lostPower();
  DateTime now();
  void adjust(const DateTime &t);
  float getTemperature();
};
