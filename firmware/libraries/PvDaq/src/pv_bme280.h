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

// The BME280 library's begin(), with a limit on its one wait. The library waits for the
// chip with "while (isReadingCalibration()) delay(10);": no time limit, and a failed read
// counts as "still busy", so the loop never ends if the sensor drops off the bus at that
// moment (audit 2026-11-21, A1). beginBounded() is the library's own sequence (begin()
// and init() of version 2.3.0, same registers, same order) through its protected
// members, with that wait limited to 200 ms.
class PvBoundedBme280 : public Adafruit_BME280 {
 public:
  bool beginBounded(uint8_t addr, TwoWire *wire) {
    if (i2c_dev) delete i2c_dev;
    i2c_dev = new Adafruit_I2CDevice(addr, wire);
    if (!i2c_dev->begin()) return false;
    _sensorID = read8(BME280_REGISTER_CHIPID);
    if (_sensorID != 0x60) return false;
    write8(BME280_REGISTER_SOFTRESET, 0xB6);
    delay(10);
    for (int k = 0; isReadingCalibration(); k++) {
      if (k >= 20) return false;  // 200 ms; the chip needs 2 ms (Bosch datasheet)
      delay(10);
    }
    readCoefficients();
    setSampling();
    delay(100);
    return true;
  }
};

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
  bool readReg(uint8_t addr, uint8_t reg, uint8_t &value);
  bool readChipId(uint8_t addr, uint8_t &id) { return readReg(addr, 0xD0, id); }
  bool waitIdle(uint32_t timeout_ms);   // status bit 3 "measuring" clear; false on error
  bool configure();          // forced mode, 1x oversampling, filter off
  TwoWire &wire_;
  PvBoundedBme280 bme_;
  Adafruit_BMP280 bmp_;
  uint8_t initAddr_ = 0, initId_ = 0;   // chip the library has been started for (0 = none)
};
