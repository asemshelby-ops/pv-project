#include "pv_ring_logic.h"

void fring_init(fring_t *r, uint32_t first, uint32_t next) {
  r->first = first;
  r->next = next;
  r->open_rows = 0;
  r->open = 0;
}

uint32_t fring_count(const fring_t *r) { return r->next - r->first; }

uint32_t fring_append(fring_t *r, uint32_t seg_rows, int new_target, int *is_new) {
  *is_new = !r->open || new_target || r->open_rows >= seg_rows;
  if (*is_new) {
    r->next++;
    r->open = 1;
    r->open_rows = 0;
  }
  r->open_rows++;
  return r->next - 1;
}

int fring_must_drop(const fring_t *r, uint32_t used, uint32_t total, uint32_t reserve,
                   uint32_t max_segments) {
  if (fring_count(r) == 0) return 0;
  return fring_count(r) >= max_segments || used >= total || total - used < reserve;
}

uint32_t fring_drop_oldest(fring_t *r) {
  const uint32_t seq = r->first++;
  if (r->first == r->next) r->open = 0;  // that was the newest one too
  return seq;
}

uint32_t fring_sizeof(void) { return (uint32_t)sizeof(fring_t); }
