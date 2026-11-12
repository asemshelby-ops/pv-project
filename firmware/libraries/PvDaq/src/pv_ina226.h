// M5Stack Unit INA226-10A behind a PaHub channel (both units have the fixed address 0x41).
// Shunt 5 mOhm and +/-10 A from the M5Stack docs. Uses the INA226 library by Rob Tillaart,
// which computes the calibration register from (max current, shunt) per the TI datasheet.
// begin() and read(): continuous conversion with the chip's power-on defaults (1.1 ms, no
// averaging), as used by the logger while no load is fitted and by tests/ina226.
// trigger() / ready() / fetch(): single-shot conversions with a chosen averaging, used by
// the load code (pv_load.cpp; settings from docs/paper/07 section 4, TI datasheet).
// After trigger() the chip no longer converts by itself: read() then returns old values
// until begin() is called again (begin() resets the chip to its power-on settings).
#pragma once
#include <INA226.h>
#include "pv_device.h"
#include "pv_pahub.h"

class PvIna226 : public PvDevice {
 public:
  PvIna226(const char *label, PvPaHub *hub, uint8_t channel, TwoWire &wire = Wire,
           uint8_t addr = INA226_ADDR, float shunt_ohm = INA226_10A_SHUNT_OHM,
           float max_current_a = INA226_10A_MAX_A)
      : label_(label), hub_(hub), ch_(channel), ina_(addr, &wire),
        shunt_(shunt_ohm), imax_(max_current_a) {}
  bool begin() override;   // select channel, probe, check the TI IDs, calibrate
  bool read() override;    // bus V, shunt mV, current, power
  // Starts one shunt + bus conversion. avg: INA226_4_SAMPLES, INA226_64_SAMPLES, ...;
  // ct: INA226_1100_us, INA226_140_us, ... (both conversions use it).
  bool trigger(uint8_t avg, uint8_t ct = INA226_1100_us);
  bool ready();            // true once: the triggered conversion has finished
  bool fetch();            // its bus V, shunt mV, current, power (starts nothing)
  // Bench step B4 only: n bus-voltage readings as fast as the I2C bus allows (continuous
  // 140 us conversions, no averaging; about 0.5 ms per reading at 100 kHz [EST]).
  // at_start() is called once everything is set up, just before the first reading (the
  // duty step goes there). t_us: micros() of each reading. Call begin() afterwards.
  uint16_t fastBusV(float *v, uint32_t *t_us, uint16_t n, void (*at_start)());
  const char *name() const override { return label_; }

  float busV = NAN, shuntmV = NAN, currentA = NAN, powerW = NAN;
  uint16_t manufacturerId = 0, dieId = 0;   // TI: 0x5449 ("TI"), 0x2260
  float currentLsbA = 0;

 private:
  bool selectChannel();
  const char *label_;
  PvPaHub *hub_;            // nullptr = unit wired directly to the bus
  uint8_t ch_;
  INA226 ina_;
  float shunt_, imax_;
  uint8_t avg_ = 0xFF, ct_ = 0xFF;   // settings now in the chip (0xFF = not known)
};
