// HOST STUB (see Arduino.h in this folder). INA226 library by Rob Tillaart, 0.6.x.
#pragma once
#include <Wire.h>
#define INA226_ERR_NONE 0x0000
enum { INA226_1_SAMPLE = 0, INA226_4_SAMPLES = 1, INA226_64_SAMPLES = 3 };
enum { INA226_140_us = 0, INA226_1100_us = 4 };
class INA226 {
 public:
  explicit INA226(const uint8_t address, TwoWire *wire = &Wire);
  bool begin();
  bool isConnected();
  uint16_t getManufacturerID();
  uint16_t getDieID();
  bool reset();
  int setMaxCurrentShunt(float max_current, float shunt, bool normalize = true);
  float getCurrentLSB();
  float getBusVoltage();
  float getShuntVoltage_mV();
  float getCurrent();
  float getPower();
  bool setAverage(uint8_t avg);
  bool setBusVoltageConversionTime(uint8_t ct);
  bool setShuntVoltageConversionTime(uint8_t ct);
  bool setModeShuntBusTrigger();
  bool setModeBusContinuous();
  bool isConversionReady();
};
