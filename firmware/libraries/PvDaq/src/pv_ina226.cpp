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
