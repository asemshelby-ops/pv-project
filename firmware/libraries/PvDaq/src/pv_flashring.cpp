#include "pv_flashring.h"

static const char *const RING_DIR = "/rb";

void PvFlashRing::pathOf(uint32_t seq, char *out, size_t n) {
  snprintf(out, n, "%s/%08lu.csv", RING_DIR, (unsigned long)seq);
}

bool PvFlashRing::begin(bool format_if_needed) {
  ok_mounted_ = false;
  LittleFS.end();
  if (!LittleFS.begin(format_if_needed)) {
    return setStatus(PvStatus::NotFound, "LittleFS mount failed (partition table without spiffs?)");
  }
  if (!LittleFS.exists(RING_DIR) && !LittleFS.mkdir(RING_DIR)) {
    return setStatus(PvStatus::ReadError, "cannot create /rb");
  }
  // Segments left by earlier boots: the lowest and the highest number in /rb.
  uint32_t lo = 0, hi = 0, found = 0;
  File dir = LittleFS.open(RING_DIR);
  for (File f = dir.openNextFile(); f; f = dir.openNextFile()) {
    char *end = nullptr;
    const uint32_t seq = strtoul(f.name(), &end, 10);
    if (end == f.name() || strcmp(end, ".csv") != 0) continue;  // not one of ours
    lo = found ? min(lo, seq) : seq;
    hi = found ? max(hi, seq) : seq;
    found++;
  }
  dir.close();
  fring_init(&ring_, lo, found ? hi + 1 : 0);
  target_[0] = 0;
  ok_mounted_ = true;
  return read();
}

bool PvFlashRing::read() {
  if (!ok_mounted_) return false;
  usedBytes = LittleFS.usedBytes();
  totalBytes = LittleFS.totalBytes();
  char d[64];
  snprintf(d, sizeof(d), "%lu segments waiting, %lu of %lu kB used", (unsigned long)segments(),
           (unsigned long)(usedBytes / 1024), (unsigned long)(totalBytes / 1024));
  return setStatus(PvStatus::Ok, d);
}

bool PvFlashRing::store(const char *target, const char *line) {
  if (!ok_mounted_) return false;
  char path[24];
  while (fring_must_drop(&ring_, LittleFS.usedBytes(), LittleFS.totalBytes(), RING_RESERVE_BYTES,
                        RING_MAX_SEGMENTS)) {
    pathOf(fring_drop_oldest(&ring_), path, sizeof(path));
    LittleFS.remove(path);  // a number without a file (never created) is simply skipped
    droppedSegments++;
  }
  int is_new = 0;
  const uint32_t seq = fring_append(&ring_, RING_SEG_ROWS, strcmp(target, target_) != 0, &is_new);
  snprintf(target_, sizeof(target_), "%s", target);
  pathOf(seq, path, sizeof(path));
  File f = LittleFS.open(path, FILE_APPEND);
  if (!f) return setStatus(PvStatus::ReadError, "segment: open for append failed");
  bool good = true;
  if (is_new) good = f.print('@') > 0 && f.println(target) > 0;
  good = good && f.println(line) > 0;
  f.close();
  if (!good) return setStatus(PvStatus::ReadError, "segment: write failed");
  return read();
}

bool PvFlashRing::flushOldest(PvSdCard &sd, const char *header) {
  if (!pending() || !sd.ok()) return false;
  char path[24];
  pathOf(ring_.first, path, sizeof(path));
  File f = LittleFS.open(path, FILE_READ);
  String target, rows;
  if (f) {
    target = f.readStringUntil('\n');
    rows = f.readString();
    f.close();
  }
  target.trim();
  rows.trim();  // appendLine() adds the last line end itself
  // A segment that is missing, empty or without its "@path" line holds nothing usable.
  const bool usable = target.length() > 2 && target[0] == '@' && target[1] == '/' && rows.length();
  if (usable && !sd.appendLine(target.c_str() + 1, rows.c_str(), header)) return false;
  LittleFS.remove(path);
  fring_drop_oldest(&ring_);
  if (!pending()) target_[0] = 0;
  read();
  return true;
}
