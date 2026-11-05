#include "pv_pahub.h"

bool PvPaHub::write(uint8_t mask) {
  wire_.beginTransmission(addr_);
  wire_.write(mask);
  if (wire_.endTransmission() != 0) {
    return setStatus(PvStatus::ReadError, "no ACK when writing the channel byte");
  }
  mask_ = mask;
  return setStatus(PvStatus::Ok);
}

bool PvPaHub::begin() {
  wire_.beginTransmission(addr_);
  if (wire_.endTransmission() != 0) return setStatus(PvStatus::NotFound, "no ACK at 0x70");
  return deselect();
}

bool PvPaHub::read() {
  if (wire_.requestFrom(addr_, (uint8_t)1) != 1) {
    return setStatus(PvStatus::ReadError, "command byte not readable");
  }
  mask_ = wire_.read();
  return setStatus(PvStatus::Ok);
}

bool PvPaHub::select(uint8_t channel) {
  if (channel >= PAHUB_CHANNELS) return setStatus(PvStatus::ReadError, "channel out of range");
  return write((uint8_t)(1u << channel));
}

bool PvPaHub::deselect() { return write(0); }
