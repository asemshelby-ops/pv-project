// Ring buffer for 10 s rows in the ESP32's own flash (LittleFS on the "spiffs" partition of
// the default partition table, 1.4 MB), used only while the SD card cannot be written.
// Rows wait in segment files /rb/<number>.csv; when the card is back the segments are
// copied to the day files in the order they were written and deleted. When the flash is
// full the OLDEST segment is deleted: the newest data is kept.
//
// A segment file: first line "@" + the SD path its rows belong to (e.g. @/20261120.csv),
// then the rows exactly as they would have been written to the card.
// The counting is in pv_ring_logic.c (tested on the PC); this file is the file handling.
//
// Not covered: /markers.csv, /load_log.csv, the sweep files and /rom_scan.txt are written
// to the SD card only.
// Known limit: a power cut between "segment copied" and "segment deleted" copies that
// segment again at the next boot: up to RING_SEG_ROWS identical rows, twice in the day file.
#pragma once
#include <LittleFS.h>
#include "pv_config.h"
#include "pv_device.h"
#include "pv_ring_logic.h"
#include "pv_sdcard.h"

class PvFlashRing : public PvDevice {
 public:
  // Mounts the file system and counts the segments left by earlier boots. format_if_needed:
  // create the file system if there is none (first boot; takes seconds [EST] -> call it
  // before the watchdog is on). This ERASES the "spiffs" partition, which this project
  // uses for nothing else.
  bool begin() override { return begin(false); }
  bool begin(bool format_if_needed);
  bool read() override;            // refreshes usedBytes / totalBytes
  const char *name() const override { return "flash buffer"; }

  // Stores one row for the SD file `target`. Deletes the oldest segments first if room is
  // needed. False if the row could not be stored (it is then lost).
  bool store(const char *target, const char *line);

  bool pending() const { return ok_mounted_ && fring_count(&ring_) > 0; }
  uint32_t segments() const { return fring_count(&ring_); }

  // Copies the oldest segment to the card (header: first line of a new day file) and
  // deletes it. False if nothing was copied: no segment, or the card write failed (the
  // segment then stays).
  bool flushOldest(PvSdCard &sd, const char *header);

  uint32_t droppedSegments = 0;    // deleted for room since boot = data lost
  uint32_t usedBytes = 0, totalBytes = 0;

 private:
  static void pathOf(uint32_t seq, char *out, size_t n);
  fring_t ring_ = {0, 0, 0, 0};
  bool ok_mounted_ = false;
  char target_[40] = "";           // SD path of the open segment
};
