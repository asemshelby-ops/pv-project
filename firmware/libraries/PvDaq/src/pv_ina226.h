// M5Stack Unit INA226-10A behind a PaHub channel (both units have the fixed address 0x41).
// Shunt 5 mOhm and +/-10 A from the M5Stack docs. Uses the INA226 library by Rob Tillaart,
// which computes the calibration register from (max current, shunt) per the TI datasheet.
// Conversion time and averaging are left at the chip's power-on defaults.
// TODO (PROJECT_STATE §4, open): choose averaging / conversion time for 10 s logging
// and for I-V sweeps from the TI datasheet.
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
};
