// HOST STUB (see Arduino.h in this folder). ESP32 Arduino core "Preferences" (NVS).
#pragma once
#include <Arduino.h>
class Preferences {
 public:
  bool begin(const char *name, bool read_only = false, const char *partition = nullptr);
  uint32_t getUInt(const char *key, uint32_t default_value = 0);
  size_t putUInt(const char *key, uint32_t value);
  bool getBool(const char *key, bool default_value = false);
  size_t putBool(const char *key, bool value);
};
