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

  // Mounts like begin(), but if the card has NO valid FAT volume (FatFs error 13) it first
  // creates one (ESP32 core: SD.begin(..., format_if_empty) -> f_mkfs, FAT type chosen by
  // card size). ERASES such a card. A card with a readable FAT volume is left untouched.
  // Only called on an explicit user command (tests/sdcard); the logger never formats.
  bool formatIfNoFilesystem();

  // Append one line; writes `header` first if the file does not exist yet.
  // The file is closed after every line, so a power cut loses at most one row.
  bool appendLine(const char *path, const char *line, const char *header = nullptr);

  // The same card through the C file functions (the card is mounted at /sd), for the
  // logger's store (pv_store_logic.c). Why not the Arduino File class: its write() only
  // fills a buffer and its close() returns nothing, so a write that failed could not be
  // seen (audit 2026-11-21). Here every step is checked, and the data and the directory
  // entry are on the card (fsync) before true is returned.
  bool appendBytes(const char *path, const char *data, size_t n);
  int32_t fileSize(const char *path);                 // bytes; -1 = no such file; -2 = no card mounted
  int tail(const char *path, char *buf, size_t n);    // the last <= n bytes; count, -1 = error

  const char *typeText() const;
  uint64_t sizeMB() const { return sizeMB_; }
  uint8_t mountAttempts = 0;       // attempts used by the last begin() (1 or 2)

 private:
  bool mount(bool format_if_empty);
  uint32_t hz_;
  uint8_t type_ = CARD_NONE;
  uint64_t sizeMB_ = 0;
};
