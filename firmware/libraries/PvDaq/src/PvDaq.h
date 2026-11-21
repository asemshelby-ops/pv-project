// Phase 0 data-logger drivers. Include this one header from a sketch.
#pragma once
#include <driver/gpio.h>
#include "pv_config.h"
#include "pv_device.h"
#include "pv_pahub.h"
#include "pv_ina226.h"
#include "pv_ds3231.h"
#include "pv_bme280.h"
#include "pv_ds18b20.h"
#include "pv_pyr20.h"
#include "pv_sdcard.h"

// Every sketch calls this first: pump and both measurement-load MOSFETs OFF, the pump
// (GPIO 32) before anything else. For each pin the LOW level is written into the output
// latch BEFORE the pin is made an output (gpio_set_level works on a pin that is still an
// input; Arduino's digitalWrite does not), so the pin starts to drive at LOW and can never
// show HIGH, not even for the time between two statements.
inline void pvOutputsOff() {
  for (int pin : {pins::PUMP_PWM, pins::LOAD_A_PWM, pins::LOAD_B_PWM}) {
    gpio_set_level((gpio_num_t)pin, 0);
    pinMode(pin, OUTPUT);
    digitalWrite(pin, LOW);
  }
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
