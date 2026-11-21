#include "pv_ina226.h"

bool PvIna226::selectChannel() {
  if (!hub_) return true;
  if (!hub_->ok() || !hub_->select(ch_)) {
    return setStatus(PvStatus::NotFound, "PaHub not available");
  }
  return true;
}

bool PvIna226::begin() {
  if (!selectChannel()) return false;
  bool found = ina_.begin();
  if (found) {
    manufacturerId = ina_.getManufacturerID();
    dieId = ina_.getDieID();
  }
  if (hub_) hub_->deselect();
  if (!found) return setStatus(PvStatus::NotFound, "no ACK at 0x41 on this channel");
  if (manufacturerId != 0x5449 || dieId != 0x2260) {
    return setStatus(PvStatus::ReadError, "IDs are not TI INA226 (0x5449 / 0x2260)");
  }
  if (!selectChannel()) return false;
  ina_.reset();  // power-on settings: continuous conversion, even after trigger() was used
  avg_ = ct_ = 0xFF;
  int err = ina_.setMaxCurrentShunt(imax_, shunt_);
  currentLsbA = ina_.getCurrentLSB();
  if (hub_) hub_->deselect();
  if (err != INA226_ERR_NONE) {
    char d[48];
    snprintf(d, sizeof(d), "calibration error 0x%04X", err);
    return setStatus(PvStatus::ReadError, d);
  }
  return setStatus(PvStatus::Ok);
}

// Audit 2026-11-21: the INA226 library returns 0 for a register it could not read, so a
// unit that dropped off mid-read gave 0.0000 V / 0.0000 A, a wrong value that looks
// measured. The library does keep an error code per read: it is checked after each one.
bool PvIna226::readValues() {
  bool good = true;
  busV = ina_.getBusVoltage();
  good = good && ina_.getLastError() == 0;
  shuntmV = ina_.getShuntVoltage_mV();
  good = good && ina_.getLastError() == 0;
  currentA = ina_.getCurrent();
  good = good && ina_.getLastError() == 0;
  powerW = ina_.getPower();
  good = good && ina_.getLastError() == 0;
  return good;
}

bool PvIna226::read() {
  if (!selectChannel()) {
    busV = shuntmV = currentA = powerW = NAN;
    return false;
  }
  const bool alive = ina_.isConnected() && readValues();
  if (hub_) hub_->deselect();
  if (!alive) {
    busV = shuntmV = currentA = powerW = NAN;
    return setStatus(PvStatus::ReadError, "lost on the bus");
  }
  // A unit that lost power for a moment restarts with an empty calibration register: its
  // current and power registers then read exactly 0 while the shunt voltage shows current
  // (0.25 mV on 5 mOhm = 50 mA). begin() writes the calibration again at the next row.
  if (currentA == 0.0f && fabsf(shuntmV) > 0.25f) {
    busV = shuntmV = currentA = powerW = NAN;
    return setStatus(PvStatus::ReadError, "calibration lost (unit was re-powered?)");
  }
  return setStatus(PvStatus::Ok);
}

bool PvIna226::trigger(uint8_t avg, uint8_t ct) {
  if (!ok() && status() != PvStatus::ReadError) return false;  // never found: begin() first
  if (!selectChannel()) return false;
  const bool alive = ina_.isConnected();
  if (alive) {
    if (avg != avg_ || ct != ct_) {
      ina_.setAverage(avg);
      ina_.setBusVoltageConversionTime(ct);
      ina_.setShuntVoltageConversionTime(ct);
      avg_ = avg;
      ct_ = ct;
    }
    ina_.setModeShuntBusTrigger();  // writing the mode starts the conversion (TI datasheet)
  }
  if (hub_) hub_->deselect();
  if (!alive) {
    avg_ = ct_ = 0xFF;
    busV = shuntmV = currentA = powerW = NAN;
    return setStatus(PvStatus::ReadError, "lost on the bus");
  }
  return true;
}

bool PvIna226::ready() {
  if (!selectChannel()) return false;
  const bool done = ina_.isConversionReady();  // reading the flag clears it
  if (hub_) hub_->deselect();
  return done;
}

uint16_t PvIna226::fastBusV(float *v, uint32_t *t_us, uint16_t n, void (*at_start)()) {
  if (!selectChannel() || !ina_.isConnected()) return 0;
  ina_.setAverage(INA226_1_SAMPLE);
  ina_.setBusVoltageConversionTime(INA226_140_us);
  ina_.setModeBusContinuous();
  avg_ = ct_ = 0xFF;
  at_start();
  for (uint16_t k = 0; k < n; k++) {
    t_us[k] = micros();
    v[k] = ina_.getBusVoltage();
  }
  if (hub_) hub_->deselect();
  return n;
}

bool PvIna226::fetch() {
  if (!selectChannel()) return false;
  const bool good = readValues();
  if (hub_) hub_->deselect();
  if (!good) {
    busV = shuntmV = currentA = powerW = NAN;
    return setStatus(PvStatus::ReadError, "lost on the bus");
  }
  return setStatus(PvStatus::Ok);
}
