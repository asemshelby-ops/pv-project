// The ESP32's own flash as a small file system (LittleFS, on the partition the default
// 4 MB layout calls "spiffs": 1.4 MB), used by the logger for ONE thing: the ring buffer
// that holds rows while the microSD card is away (pv_store_logic.c decides what goes in
// and out; this file only reads and writes the segment files it asks for).
// Files: /ring/NNNNNNNN.seg (segment number, 8 digits) and /ring/state.txt.
// Like the card driver, it uses the C file functions so that a failed write is seen.
#pragma once
#include <LittleFS.h>
#include "pv_device.h"

class PvFlashRing : public PvDevice {
 public:
  // Mounts the partition. If it holds no LittleFS (first boot, or an old SPIFFS image) it
  // is formatted ONCE, and `formatted` says so. This is the internal flash only: nothing
  // here can touch the microSD card.
  bool begin() override;
  bool read() override;            // refreshes totalBytes / usedBytes
  const char *name() const override { return "LittleFS ring"; }

  int range(uint32_t *first, uint32_t *last);       // 1 = segments exist, 0 = none, -1 = not usable
  int32_t size(uint32_t seq);                       // bytes; -1 = no such segment
  bool append(uint32_t seq, const char *data, size_t n);
  int readAt(uint32_t seq, uint32_t off, char *buf, size_t n);   // count; -1 = error
  int32_t countLines(uint32_t seq, uint32_t off);   // line feeds from byte off on; -1 = error
  bool remove(uint32_t seq);
  bool save(const char *text);                      // the state file, replaced as a whole
  int load(char *buf, size_t n);                    // its length; -1 = none

  bool formatted = false;
  size_t totalBytes = 0, usedBytes = 0;

 private:
  static void segPath(char *out, size_t size, uint32_t seq);
};
