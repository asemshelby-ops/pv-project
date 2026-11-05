// microSD card over SPI (VSPI pins, CS on GPIO13), FAT file system (Arduino SD).
#pragma once
#include <SD.h>
#include <SPI.h>
#include "pv_config.h"
#include "pv_device.h"

class PvSdCard : public PvDevice {
 public:
  explicit PvSdCard(uint32_t spi_hz = 4000000) : hz_(spi_hz) {}
  bool begin() override;           // start SPI and mount the card
  bool read() override;            // check that a card is still mounted
  const char *name() const override { return "microSD"; }

  // Append one line; writes `header` first if the file does not exist yet.
  // The file is closed after every line, so a power cut loses at most one row.
  bool appendLine(const char *path, const char *line, const char *header = nullptr);

  const char *typeText() const;
  uint64_t sizeMB() const { return sizeMB_; }

 private:
  uint32_t hz_;
  uint8_t type_ = CARD_NONE;
  uint64_t sizeMB_ = 0;
};
