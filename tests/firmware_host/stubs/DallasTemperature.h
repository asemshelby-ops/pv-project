// HOST STUB (see Arduino.h in this folder).
#pragma once
#include <OneWire.h>
#define DEVICE_DISCONNECTED_C -127.0f
class DallasTemperature {
 public:
  explicit DallasTemperature(OneWire *bus);
  void begin();
  uint8_t getDeviceCount();
  bool getAddress(uint8_t *rom, uint8_t index);
  void setResolution(uint8_t bits);
  void setWaitForConversion(bool wait);
  void requestTemperatures();
  float getTempC(const uint8_t *rom);
};
