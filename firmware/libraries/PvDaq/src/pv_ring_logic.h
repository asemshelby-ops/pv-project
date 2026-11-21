// Bookkeeping of the flash ring buffer (pv_flashring.cpp): rows that could not be written
// to the SD card wait in numbered segment files in the ESP32's own flash. This file only
// counts: which segment the next row goes to, and when the oldest one must be deleted.
// Plain C with no Arduino calls, tested on the PC (tests/test_ring_logic.py).
//
// Segments first .. next-1 exist, oldest first. A segment takes rows for ONE day file and
// at most seg_rows of them. A segment found at boot is never appended to (it may end in a
// half-written row after a power cut): the first row after a boot starts a new one.
// Names start with fring_ (flash ring): ring_t is NASIM's sample buffer (export/c/nasim.h).
#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
  uint32_t first, next;   // sequence numbers: oldest segment, and the one after the newest
  uint32_t open_rows;     // rows in the newest segment, if it was started in this boot
  uint32_t open;          // 1 = the newest segment was started in this boot and takes rows
} fring_t;

void fring_init(fring_t *r, uint32_t first, uint32_t next);  // first == next: empty
uint32_t fring_count(const fring_t *r);                      // segments waiting

// The segment the next row goes to. *is_new = 1: that file does not exist yet.
// new_target: the row belongs to another day file than the row before it.
uint32_t fring_append(fring_t *r, uint32_t seg_rows, int new_target, int *is_new);

// 1 = delete the oldest segment before storing more: fewer than `reserve` bytes are free
// (used, total: the file system's own figures), or max_segments are waiting.
int fring_must_drop(const fring_t *r, uint32_t used, uint32_t total, uint32_t reserve,
                   uint32_t max_segments);

// Takes the oldest segment out of the count and returns its number (the caller deletes
// the file: dropped for room, or copied to the card). Call only when fring_count() > 0.
uint32_t fring_drop_oldest(fring_t *r);

uint32_t fring_sizeof(void);  // for the PC test: its mirror of fring_t must match

#ifdef __cplusplus
}
#endif
