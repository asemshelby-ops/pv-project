#include "pv_flashring.h"

#include <dirent.h>
#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>

static const char DIR_PATH[] = "/littlefs/ring";   // LittleFS is mounted at /littlefs
static const char STATE_PATH[] = "/littlefs/ring/state.txt";

void PvFlashRing::segPath(char *out, size_t size, uint32_t seq) {
  snprintf(out, size, "%s/%08lu.seg", DIR_PATH, (unsigned long)seq);
}

bool PvFlashRing::begin() {
  formatted = false;
  if (!LittleFS.begin(false)) {      // no file system there yet: make one (internal flash only)
    if (!LittleFS.begin(true)) return setStatus(PvStatus::NotFound, "mount and format failed");
    formatted = true;
  }
  mkdir(DIR_PATH, 0777);             // "already exists" is fine
  struct stat st;
  if (stat(DIR_PATH, &st) != 0) return setStatus(PvStatus::ReadError, "cannot create /ring");
  read();
  return setStatus(PvStatus::Ok, formatted ? "formatted (first use)" : "");
}

bool PvFlashRing::read() {
  totalBytes = LittleFS.totalBytes();
  usedBytes = LittleFS.usedBytes();
  return true;
}

int PvFlashRing::range(uint32_t *first, uint32_t *last) {
  if (!ok()) return -1;
  DIR *d = opendir(DIR_PATH);
  if (!d) return -1;
  bool any = false;
  for (struct dirent *e = readdir(d); e; e = readdir(d)) {
    unsigned long seq;
    char ext[8];
    if (sscanf(e->d_name, "%8lu.%3s", &seq, ext) != 2 || strcmp(ext, "seg") != 0) continue;
    if (!any || seq < *first) *first = (uint32_t)seq;
    if (!any || seq > *last) *last = (uint32_t)seq;
    any = true;
  }
  closedir(d);
  return any ? 1 : 0;
}

int32_t PvFlashRing::size(uint32_t seq) {
  char path[48];
  struct stat st;
  segPath(path, sizeof(path), seq);
  return ok() && stat(path, &st) == 0 ? (int32_t)st.st_size : -1;
}

bool PvFlashRing::append(uint32_t seq, const char *data, size_t n) {
  if (!ok()) return false;
  char path[48];
  segPath(path, sizeof(path), seq);
  FILE *f = fopen(path, "a");
  if (!f) return false;
  bool good = fwrite(data, 1, n, f) == n;
  good = good && fflush(f) == 0;
  good = good && fsync(fileno(f)) == 0;
  good = (fclose(f) == 0) && good;   // LittleFS commits the file when it is closed
  return good;
}

int PvFlashRing::readAt(uint32_t seq, uint32_t off, char *buf, size_t n) {
  if (!ok()) return -1;
  char path[48];
  segPath(path, sizeof(path), seq);
  FILE *f = fopen(path, "r");
  if (!f) return -1;
  size_t got = 0;
  bool good = fseek(f, (long)off, SEEK_SET) == 0;
  if (good) {
    got = fread(buf, 1, n, f);
    good = !ferror(f);
  }
  fclose(f);
  return good ? (int)got : -1;
}

int32_t PvFlashRing::countLines(uint32_t seq, uint32_t off) {
  if (!ok()) return -1;
  char path[48], buf[256];
  segPath(path, sizeof(path), seq);
  FILE *f = fopen(path, "r");
  if (!f) return -1;
  int32_t lines = 0;
  if (fseek(f, (long)off, SEEK_SET) == 0) {
    for (size_t got = fread(buf, 1, sizeof(buf), f); got > 0; got = fread(buf, 1, sizeof(buf), f)) {
      for (size_t k = 0; k < got; k++) lines += buf[k] == '\n';
    }
  }
  const bool good = !ferror(f);
  fclose(f);
  return good ? lines : -1;
}

bool PvFlashRing::remove(uint32_t seq) {
  char path[48];
  segPath(path, sizeof(path), seq);
  return ok() && unlink(path) == 0;
}

bool PvFlashRing::save(const char *text) {
  if (!ok()) return false;
  FILE *f = fopen(STATE_PATH, "w");  // LittleFS keeps the old content until the close succeeds
  if (!f) return false;
  const size_t n = strlen(text);
  bool good = fwrite(text, 1, n, f) == n;
  good = good && fflush(f) == 0;
  good = (fclose(f) == 0) && good;
  return good;
}

int PvFlashRing::load(char *buf, size_t n) {
  if (!ok()) return -1;
  FILE *f = fopen(STATE_PATH, "r");
  if (!f) return -1;
  const size_t got = fread(buf, 1, n, f);
  fclose(f);
  return (int)got;
}
