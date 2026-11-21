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
  // The same in two steps, without blocking: start the conversion, then, at least
  // CONVERSION_MS later, fetch the results.
  static constexpr uint32_t CONVERSION_MS = 750;   // 12 bit (Maxim datasheet)
  bool startConversion();
  bool fetch();
  // The logger's way (2026-11-21): ask a probe by ITS ROM, whatever the bus scan found.
  // convert() starts one conversion of every probe on the bus and waits for it (the
  // library polls the bus and gives up after 750 ms). readRom() then returns that probe's
  // temperature, or NaN if it does not answer, its CRC is wrong, it shows the power-on
  // value 85.0 or a value outside -55..125 degC (the sensor's range, Maxim datasheet).
  bool convert();
  float readRom(const uint8_t *r);
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
