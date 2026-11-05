// Phase 0 data-logger drivers. Include this one header from a sketch.
#pragma once
#include "pv_config.h"
#include "pv_device.h"
#include "pv_pahub.h"
#include "pv_ina226.h"
#include "pv_ds3231.h"
#include "pv_bme280.h"
#include "pv_ds18b20.h"
#include "pv_pyr20.h"
#include "pv_sdcard.h"

// Every sketch calls this first: pump and measurement-load MOSFETs OFF.
inline void pvOutputsOff() {
  pinMode(pins::PUMP_PWM, OUTPUT);
  digitalWrite(pins::PUMP_PWM, LOW);
  pinMode(pins::LOAD_PWM, OUTPUT);
  digitalWrite(pins::LOAD_PWM, LOW);
}

// Probes every 7-bit I2C address and prints the ones that ACK. Returns how many answered.
inline int pvScanI2C(Print &out, const char *label, TwoWire &wire = Wire) {
  out.printf("  %s:", label);
  int found = 0;
  for (uint8_t a = 0x08; a < 0x78; a++) {
    wire.beginTransmission(a);
    if (wire.endTransmission() == 0) {
      out.printf(" 0x%02X", a);
      found++;
    }
  }
  out.println(found ? "" : " (nothing)");
  return found;
}

// Prints bytes as "01 04 00 00 ..." (frame debugging).
inline void pvPrintHex(Print &out, const uint8_t *b, size_t n) {
  for (size_t i = 0; i < n; i++) out.printf(i ? " %02X" : "%02X", b[i]);
}
