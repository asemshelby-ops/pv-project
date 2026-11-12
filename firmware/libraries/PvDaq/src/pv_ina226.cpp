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

bool PvIna226::read() {
  if (!selectChannel()) return false;
  bool alive = ina_.isConnected();
  if (alive) {
    busV = ina_.getBusVoltage();
    shuntmV = ina_.getShuntVoltage_mV();
    currentA = ina_.getCurrent();
    powerW = ina_.getPower();
  }
  if (hub_) hub_->deselect();
  if (!alive) {
    busV = shuntmV = currentA = powerW = NAN;
    return setStatus(PvStatus::ReadError, "lost on the bus");
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
  busV = ina_.getBusVoltage();
  shuntmV = ina_.getShuntVoltage_mV();
  currentA = ina_.getCurrent();
  powerW = ina_.getPower();
  if (hub_) hub_->deselect();
  return setStatus(PvStatus::Ok);
}
