#include "pv_sdcard.h"

#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>

static const char MOUNT[] = "/sd";  // where begin() mounts the card in the file system

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

// Opens for append, writes up to three pieces, and reports success only if every byte was
// taken, flushed out of the C library's buffer, synced to the card and the file closed.
static bool appendPieces(const char *path, const char *a, size_t na, const char *b, size_t nb,
                         const char *c, size_t nc) {
  char full[48];
  snprintf(full, sizeof(full), "%s%s", MOUNT, path);
  FILE *f = fopen(full, "a");
  if (!f) return false;
  bool good = (na == 0 || fwrite(a, 1, na, f) == na) && (nb == 0 || fwrite(b, 1, nb, f) == nb) &&
              (nc == 0 || fwrite(c, 1, nc, f) == nc);
  good = good && fflush(f) == 0;
  good = good && fsync(fileno(f)) == 0;  // FAT: data sectors and the directory entry
  good = (fclose(f) == 0) && good;
  return good;
}

bool PvSdCard::appendLine(const char *path, const char *line, const char *header) {
  if (!ok()) return false;
  const bool with_header = header && fileSize(path) < 0;  // a new file starts with the header
  char head[400] = "";
  if (with_header) snprintf(head, sizeof(head), "%s\r\n", header);
  if (!appendPieces(path, head, strlen(head), line, strlen(line), "\r\n", 2)) {
    return setStatus(PvStatus::ReadError, "write failed");
  }
  return setStatus(PvStatus::Ok);
}

bool PvSdCard::appendBytes(const char *path, const char *data, size_t n) {
  if (!ok()) return false;
  if (!appendPieces(path, data, n, nullptr, 0, nullptr, 0)) {
    return setStatus(PvStatus::ReadError, "write failed");
  }
  return setStatus(PvStatus::Ok);
}

int32_t PvSdCard::fileSize(const char *path) {
  if (!ok()) return -2;
  char full[48];
  struct stat st;
  snprintf(full, sizeof(full), "%s%s", MOUNT, path);
  // Any failure counts as "no such file": the append that follows is the real test of
  // the card, and at worst a header line is written once more.
  return stat(full, &st) == 0 ? (int32_t)st.st_size : -1;
}

int PvSdCard::tail(const char *path, char *buf, size_t n) {
  if (!ok()) return -1;
  char full[48];
  snprintf(full, sizeof(full), "%s%s", MOUNT, path);
  FILE *f = fopen(full, "r");
  if (!f) return -1;
  long size = -1;
  if (fseek(f, 0, SEEK_END) == 0) size = ftell(f);
  size_t got = 0;
  bool good = size >= 0 && fseek(f, size > (long)n ? size - (long)n : 0, SEEK_SET) == 0;
  if (good) {
    got = fread(buf, 1, n, f);
    good = !ferror(f);
  }
  fclose(f);
  return good ? (int)got : -1;
}

const char *PvSdCard::typeText() const {
  switch (type_) {
    case CARD_MMC: return "MMC";
    case CARD_SD: return "SDSC";
    case CARD_SDHC: return "SDHC/SDXC";
    default: return "none";
  }
}
