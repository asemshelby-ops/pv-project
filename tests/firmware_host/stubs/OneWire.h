// HOST STUB (see Arduino.h in this folder).
#pragma once
#include <Arduino.h>
class OneWire {
 public:
  explicit OneWire(uint8_t pin);
  static uint8_t crc8(const uint8_t *addr, uint8_t len);
};
