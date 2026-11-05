#include "pv_sdcard.h"

bool PvSdCard::begin() { return mount(false); }

bool PvSdCard::formatIfNoFilesystem() { return mount(true); }

bool PvSdCard::mount(bool format_if_empty) {
  SD.end();  // allows a retry after the card was removed or failed
  SPI.begin(pins::SD_SCK, pins::SD_MISO, pins::SD_MOSI, pins::SD_CS);
  mountAttempts = 1;
  bool mounted = SD.begin(pins::SD_CS, SPI, hz_, "/sd", 5, format_if_empty);
  if (!mounted) {
    mountAttempts = 2;
    // Bench 2026-11-05: after an ESP32 reset (card stays powered) every second first mount
    // failed and the next attempt succeeded. One retry after a short pause fixes that.
    SD.end();
    delay(100);
    mounted = SD.begin(pins::SD_CS, SPI, hz_, "/sd", 5, format_if_empty);
  }
  if (!mounted) {
    type_ = CARD_NONE;
    return setStatus(PvStatus::NotFound, format_if_empty
                                             ? "format/mount failed"
                                             : "mount failed (card, wiring, FAT32?)");
  }
  type_ = SD.cardType();
  if (type_ == CARD_NONE) return setStatus(PvStatus::NotFound, "no card");
  sizeMB_ = SD.cardSize() / (1024ULL * 1024ULL);
  return setStatus(PvStatus::Ok);
}

bool PvSdCard::read() {
  if (SD.cardType() == CARD_NONE) return setStatus(PvStatus::NotFound, "no card");
  return setStatus(PvStatus::Ok);
}

bool PvSdCard::appendLine(const char *path, const char *line, const char *header) {
  if (!ok()) return false;
  bool is_new = !SD.exists(path);
  File f = SD.open(path, FILE_APPEND);
  if (!f) return setStatus(PvStatus::ReadError, "open for append failed");
  bool good = true;
  if (is_new && header) good = f.println(header) > 0;
  good = good && f.println(line) > 0;
  f.close();
  if (!good) return setStatus(PvStatus::ReadError, "write failed");
  return setStatus(PvStatus::Ok);
}

const char *PvSdCard::typeText() const {
  switch (type_) {
    case CARD_MMC: return "MMC";
    case CARD_SD: return "SDSC";
    case CARD_SDHC: return "SDHC/SDXC";
    default: return "none";
  }
}
