#include "pv_sdcard.h"

bool PvSdCard::begin() {
  SD.end();  // allows a retry after the card was removed or failed
  SPI.begin(pins::SD_SCK, pins::SD_MISO, pins::SD_MOSI, pins::SD_CS);
  if (!SD.begin(pins::SD_CS, SPI, hz_)) {
    type_ = CARD_NONE;
    return setStatus(PvStatus::NotFound, "mount failed (card, wiring, FAT32?)");
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
