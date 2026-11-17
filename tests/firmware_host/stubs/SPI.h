// HOST STUB (see Arduino.h in this folder).
#pragma once
#include <Arduino.h>
class SPIClass {
 public:
  void begin(int sck, int miso, int mosi, int cs);
};
extern SPIClass SPI;
