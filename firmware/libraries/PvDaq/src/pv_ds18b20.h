// DS18B20 sensors on one 1-Wire bus (OneWire + DallasTemperature).
// Screening per CLAUDE.md: an authentic Maxim ROM has the pattern 28-xx-xx-xx-xx-00-00-xx
// (family 0x28, bytes 5 and 6 zero). This is a heuristic: a mismatch flags a likely clone,
// it does not stop the sensor from being used. All sensors still get a water-bath
// cross-calibration before mounting.
#pragma once
#include <DallasTemperature.h>
#include <OneWire.h>
#include "pv_device.h"

class PvDs18b20 : public PvDevice {
 public:
  static constexpr uint8_t MAX_SENSORS = 10;

  explicit PvDs18b20(int pin) : ow_(pin), dt_(&ow_) {}
  bool begin() override;   // scan the bus, store ROMs, 12-bit resolution
  bool read() override;    // one conversion of all sensors (blocks ~750 ms at 12 bit)
  const char *name() const override { return "DS18B20"; }

  uint8_t count = 0;
  uint8_t rom[MAX_SENSORS][8];
  float tempC[MAX_SENSORS];

  static bool romCrcOk(const uint8_t *r) { return OneWire::crc8(r, 7) == r[7]; }
  static bool looksGenuine(const uint8_t *r) { return r[0] == 0x28 && r[5] == 0 && r[6] == 0; }
  static void romText(const uint8_t *r, char *out);   // "28-xx-xx-xx-xx-xx-xx-xx", out >= 24 chars
  int indexOf(const uint8_t *r) const;                 // -1 if that ROM is not on the bus

 private:
  OneWire ow_;
  DallasTemperature dt_;
};
