// PYR20 pyranometer, both outputs (INFWIN UM-PYR20 manual):
//   * RS485 Modbus RTU, default address 1, 9600 8N1; register 0x0000 = solar
//     radiation, UINT16, 0-2000 for 0-2000 W/m2; function code 3 or 4.
//   * 0-2 V analog output, SR = 1000 * V  (1 mV = 1 W/m2).
// The Modbus RTU frame and CRC-16 are written here by hand (no library) so the
// test sketch can print every byte that goes over the wire.
#pragma once
#include <HardwareSerial.h>
#include "pv_config.h"
#include "pv_device.h"

class PvPyr20Modbus : public PvDevice {
 public:
  PvPyr20Modbus(HardwareSerial &port, int rx_pin, int tx_pin, uint8_t slave = PYR20_SLAVE,
                uint32_t baud = PYR20_BAUD)
      : port_(port), rx_(rx_pin), tx_(tx_pin), slave_(slave), baud_(baud) {}
  bool begin() override;           // open the UART, then one read() to detect the sensor
  bool read() override;            // FC PYR20_FC, register 0x0000, 1 register
  const char *name() const override { return "PYR20 Modbus"; }

  // Generic read of n holding (fc 3) or input (fc 4) registers. Fills out[0..n-1].
  bool readRegisters(uint8_t fc, uint16_t reg, uint16_t n, uint16_t *out);
  static uint16_t crc16(const uint8_t *buf, size_t len);  // Modbus CRC-16 (poly 0xA001, init 0xFFFF)

  uint16_t raw = 0;               // register value
  float irradiance = NAN;         // W/m2 (= raw, per the manual)

  // Last frames, for debugging (test sketch prints them).
  uint8_t tx[8];
  size_t txLen = 0;
  uint8_t rx[64];
  size_t rxLen = 0;
  bool echoRemoved = false;       // some RS485 modules echo the request

 private:
  HardwareSerial &port_;
  int rx_, tx_;
  uint8_t slave_;
  uint32_t baud_;
};

class PvPyr20Analog : public PvDevice {
 public:
  explicit PvPyr20Analog(int pin, uint16_t samples = 64) : pin_(pin), samples_(samples) {}
  bool begin() override;           // configure the ADC (presence cannot be detected)
  bool read() override;            // average `samples` calibrated readings
  const char *name() const override { return "PYR20 analog"; }

  uint32_t millivolts = 0;
  float irradiance = NAN;          // W/m2 = mV (SR = 1000 * V)
  bool inRange = false;            // ESP32 ADC at 11/12 dB: 150-2450 mV usable (ESP-IDF docs)

 private:
  int pin_;
  uint16_t samples_;
};
