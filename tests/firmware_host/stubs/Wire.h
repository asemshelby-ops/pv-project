// HOST STUB (see Arduino.h in this folder).
#pragma once
#include <Arduino.h>
class TwoWire {
 public:
  bool begin(int sda, int scl, uint32_t hz);
  void beginTransmission(uint8_t addr);
  uint8_t endTransmission();
  size_t write(uint8_t b);
  uint8_t requestFrom(uint8_t addr, uint8_t n);
  int read();
};
extern TwoWire Wire;
