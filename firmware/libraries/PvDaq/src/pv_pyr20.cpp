#include "pv_pyr20.h"

// ---- Modbus RTU -------------------------------------------------------------------

uint16_t PvPyr20Modbus::crc16(const uint8_t *buf, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < len; i++) {
    crc ^= buf[i];
    for (int b = 0; b < 8; b++) crc = (crc & 1) ? (crc >> 1) ^ 0xA001 : crc >> 1;
  }
  return crc;  // sent low byte first
}

bool PvPyr20Modbus::begin() {
  port_.begin(baud_, SERIAL_8N1, rx_, tx_);
  if (read()) return true;
  if (status() == PvStatus::ReadError && rxLen == 0) {
    return setStatus(PvStatus::NotFound, "no reply (wiring, A/B swapped, power?)");
  }
  return false;
}

bool PvPyr20Modbus::read() {
  uint16_t v;
  if (!readRegisters(PYR20_FC, PYR20_REG_SR, 1, &v)) return false;
  raw = v;
  irradiance = (float)v;  // 1 count = 1 W/m2
  return true;
}

bool PvPyr20Modbus::readRegisters(uint8_t fc, uint16_t reg, uint16_t n, uint16_t *out) {
  // Request: slave, fc, reg hi, reg lo, n hi, n lo, crc lo, crc hi
  tx[0] = slave_;
  tx[1] = fc;
  tx[2] = reg >> 8;
  tx[3] = reg & 0xFF;
  tx[4] = n >> 8;
  tx[5] = n & 0xFF;
  uint16_t crc = crc16(tx, 6);
  tx[6] = crc & 0xFF;
  tx[7] = crc >> 8;
  txLen = 8;

  while (port_.available()) port_.read();  // drop stale bytes
  port_.write(tx, txLen);
  port_.flush();                            // wait until the last bit has left

  // Reply: slave, fc, byte count (2n), data..., crc lo, crc hi -> 5 + 2n bytes.
  // Exception reply: slave, fc|0x80, code, crc lo, crc hi -> 5 bytes.
  const size_t expected = 5 + 2 * n;
  rxLen = 0;
  echoRemoved = false;
  uint32_t start = millis(), last = start;
  while (millis() - start < 300) {         // sensor response delay is 0 ms by default
    while (port_.available() && rxLen < sizeof(rx)) {
      rx[rxLen++] = port_.read();
      last = millis();
    }
    if (rxLen >= txLen && memcmp(rx, tx, txLen) == 0) {  // echo of our own request
      memmove(rx, rx + txLen, rxLen - txLen);
      rxLen -= txLen;
      echoRemoved = true;
    }
    if (rxLen >= expected) break;
    if (rxLen >= 5 && (rx[1] & 0x80)) break;
    if (rxLen > 0 && millis() - last > 5) break;  // > 3.5 characters of silence at 9600 baud
    delay(1);
  }

  if (rxLen == 0) return setStatus(PvStatus::ReadError, "no reply");
  if (rxLen < 5) return setStatus(PvStatus::ReadError, "reply too short");
  uint16_t rcrc = rx[rxLen - 2] | (rx[rxLen - 1] << 8);
  if (crc16(rx, rxLen - 2) != rcrc) return setStatus(PvStatus::ReadError, "CRC mismatch");
  if (rx[0] != slave_) return setStatus(PvStatus::ReadError, "reply from another address");
  if (rx[1] == (fc | 0x80)) {
    char d[40];
    snprintf(d, sizeof(d), "Modbus exception code %u", rx[2]);
    return setStatus(PvStatus::ReadError, d);
  }
  if (rx[1] != fc || rx[2] != 2 * n || rxLen != expected) {
    return setStatus(PvStatus::ReadError, "unexpected reply format");
  }
  for (uint16_t i = 0; i < n; i++) out[i] = (rx[3 + 2 * i] << 8) | rx[4 + 2 * i];  // big-endian
  return setStatus(PvStatus::Ok);
}

// ---- 0-2 V analog ---------------------------------------------------------------------

bool PvPyr20Analog::begin() {
  analogReadResolution(12);
  analogSetPinAttenuation(pin_, ADC_11db);  // widest range: 150-2450 mV (ESP-IDF docs)
  return setStatus(PvStatus::Ok, "presence cannot be detected on an analog pin");
}

bool PvPyr20Analog::read() {
  uint32_t sum = 0;
  for (uint16_t i = 0; i < samples_; i++) sum += analogReadMilliVolts(pin_);  // eFuse-calibrated
  millivolts = sum / samples_;
  irradiance = (float)millivolts;  // SR = 1000 * V  ->  W/m2 = mV
  inRange = millivolts >= 150 && millivolts <= 2450;
  return setStatus(PvStatus::Ok);
}
