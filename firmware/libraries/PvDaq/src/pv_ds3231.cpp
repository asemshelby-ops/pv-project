#include "pv_ds3231.h"

bool PvDs3231::begin() {
  if (!rtc_.begin(&wire_)) return setStatus(PvStatus::NotFound, "no ACK at 0x68");
  timeValid = !rtc_.lostPower();
  return setStatus(PvStatus::Ok, timeValid ? "" : "oscillator stopped: time not set");
}

// Two BCD digits -> number; false if a digit is above 9 (not a value the chip can hold).
static bool bcd(uint8_t b, uint8_t &out) {
  if ((b & 0x0F) > 9 || (b >> 4) > 9) return false;
  out = (uint8_t)((b >> 4) * 10 + (b & 0x0F));
  return true;
}

// The seven time registers 0x00..0x06 are read HERE, not with RTClib's now(): now() does
// not report a failed read and then builds a date from whatever was in its buffer, which
// can look valid (audit 2026-11-21). Decoding is RTClib's (DS3231 datasheet, 24-hour
// mode): seconds, minutes, hours, weekday, date, month (+ century bit 7), year - 2000.
// The time is kept only if all seven bytes arrived and every field is in range.
bool PvDs3231::read() {
  uint8_t b[7];
  wire_.beginTransmission(DS3231_ADDR);
  wire_.write((uint8_t)0x00);
  if (wire_.endTransmission(false) != 0) return setStatus(PvStatus::ReadError, "lost on the bus");
  if (wire_.requestFrom(DS3231_ADDR, (uint8_t)7) != 7) {
    return setStatus(PvStatus::ReadError, "lost on the bus");
  }
  for (uint8_t &x : b) x = (uint8_t)wire_.read();
  uint8_t ss, mi, hh, dd, mo, yy;
  if (!bcd(b[0] & 0x7F, ss) || !bcd(b[1], mi) || !bcd(b[2], hh) || !bcd(b[4], dd) ||
      !bcd(b[5] & 0x7F, mo) || !bcd(b[6], yy) || ss > 59 || mi > 59 || hh > 23 || dd < 1 ||
      dd > 31 || mo < 1 || mo > 12) {
    return setStatus(PvStatus::ReadError, "invalid date read");
  }
  const DateTime t(2000U + yy, mo, dd, hh, mi, ss);
  if (!t.isValid()) return setStatus(PvStatus::ReadError, "invalid date read");
  unix_ = t.unixtime();
  temperatureC = rtc_.getTemperature();  // information only (tests/ds3231); not logged
  return setStatus(PvStatus::Ok, timeValid ? "" : "time not set");
}
