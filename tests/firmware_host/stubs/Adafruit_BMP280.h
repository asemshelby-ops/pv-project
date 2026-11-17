// HOST STUB (see Arduino.h in this folder): only what PvDaq's header needs.
#pragma once
#include <Wire.h>
class Adafruit_BMP280 {
 public:
  explicit Adafruit_BMP280(TwoWire *wire = &Wire);
};
