// M5Stack PaHub V2.1 (PCA9548AP I2C switch, 6 channels, default 0x70).
// The switch has one command byte: bit n = 1 connects downstream channel n
// (TI/NXP PCA9548A datasheet). We select one channel at a time.
#pragma once
#include <Wire.h>
#include "pv_config.h"
#include "pv_device.h"

class PvPaHub : public PvDevice {
 public:
  explicit PvPaHub(TwoWire &wire = Wire, uint8_t addr = PAHUB_ADDR) : wire_(wire), addr_(addr) {}
  bool begin() override;           // probe the address, disconnect all channels
  bool read() override;            // read back the command byte (connected channels)
  const char *name() const override { return "PaHub"; }

  bool select(uint8_t channel);    // connect only this channel (0..5)
  bool deselect();                 // disconnect all channels
  uint8_t mask() const { return mask_; }

 private:
  bool write(uint8_t mask);
  TwoWire &wire_;
  uint8_t addr_;
  uint8_t mask_ = 0;
};
