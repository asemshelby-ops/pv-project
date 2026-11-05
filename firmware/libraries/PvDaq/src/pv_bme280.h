// Ambient sensor on the BME280 footprint. Many sellers ship a BMP280 (no humidity):
// the chip ID in register 0xD0 tells them apart (Bosch datasheets: BME280 = 0x60,
// BMP280 = 0x58, BMP280 engineering samples = 0x56 / 0x57).
// Forced mode, 1x oversampling, filter off: Bosch's suggested "weather monitoring"
// setting (BME280 datasheet §3.5.1); one measurement per read keeps self-heating low.
#pragma once
#include <Adafruit_BME280.h>
#include <Adafruit_BMP280.h>
#include "pv_config.h"
#include "pv_device.h"

class PvBme280 : public PvDevice {
 public:
  explicit PvBme280(TwoWire &wire = Wire) : wire_(wire), bmp_(&wire) {}
  bool begin() override;   // try 0x76 then 0x77, read the chip ID, start the right driver
  bool read() override;    // one forced measurement
  const char *name() const override { return "BME280"; }

  uint8_t address = 0;
  uint8_t chipId = 0;
  const char *model() const;      // "BME280", "BMP280", "BMP280 (sample)", "unknown"
  bool hasHumidity() const { return chipId == 0x60; }

  float temperatureC = NAN, humidityPct = NAN, pressureHpa = NAN;

 private:
  bool readChipId(uint8_t addr, uint8_t &id);
  TwoWire &wire_;
  Adafruit_BME280 bme_;
  Adafruit_BMP280 bmp_;
};
