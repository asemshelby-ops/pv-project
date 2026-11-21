/* See pv_store_logic.h. Plain C99: no Arduino, no heap, no waiting. */
#include "pv_store_logic.h"

#include <stdio.h>
#include <string.h>

#define EOL "\r\n" /* what the logger has always written (println) */
static const char KIND_CHAR[PVK_COUNT] = {'R', 'E', 'M'};

/* ---- notices -------------------------------------------------------------------------------- */

static void notice(pvstore_t *S, const char *event, const char *detail) {
  if (S->notice_count == PVSTORE_NOTICES) return;  /* full: this one is not kept */
  char *slot = S->notice[(S->notice_head + S->notice_count) % PVSTORE_NOTICES];
  snprintf(slot, PVSTORE_NOTICE_MAX, "%s\t%s", event, detail);
  S->notice_count++;
}

int pvstore_next_notice(pvstore_t *S, char *event, int event_size, char *detail, int detail_size) {
  if (S->notice_count == 0) return 0;
  const char *slot = S->notice[S->notice_head];
  const char *tab = strchr(slot, '\t');
  const int n = tab ? (int)(tab - slot) : 0;
  snprintf(event, (size_t)event_size, "%.*s", n, slot);
  snprintf(detail, (size_t)detail_size, "%s", tab ? tab + 1 : "");
  S->notice_head = (S->notice_head + 1) % PVSTORE_NOTICES;
  S->notice_count--;
  return 1;
}

/* ---- state file ------------------------------------------------------------------------------- */

static void save_state(pvstore_t *S, const pvstore_io_t *io) {
  char text[48];
  if (!S->ring_ok) return;
  snprintf(text, sizeof(text), "%lu %lu %lu\n", (unsigned long)S->first_seq,
           (unsigned long)S->first_off, (unsigned long)S->dropped);
  io->fs_save(io->ctx, text);  /* if this fails the place is still right in RAM */
}

/* ---- the card --------------------------------------------------------------------------------- */

static void card_down(pvstore_t *S) {
  if (S->sd_up) notice(S, "sd_lost", "card not answering or write failed: lines go to the LittleFS buffer");
  S->sd_up = 0;
}

/* Does the file end with this record? tail = the last n bytes of the file. The last
 * COMPLETE line (one that ends in a line feed) is compared over key_len characters. */
static int tail_has(const char *tail, int n, const char *line, int key_len) {
  const int line_len = (int)strlen(line);
  int end = n, start;
  if (n <= 0 || tail[n - 1] != '\n') return 0;  /* ends in a fragment: not a whole record */
  end--;
  if (end > 0 && tail[end - 1] == '\r') end--;
  start = end;
  while (start > 0 && tail[start - 1] != '\n') start--;
  if (key_len <= 0 || key_len > line_len) {  /* the whole line must be the same */
    return end - start == line_len && memcmp(tail + start, line, (size_t)line_len) == 0;
  }
  return end - start >= key_len && memcmp(tail + start, line, (size_t)key_len) == 0;
}

/* One line to one file on the card. 1 = it is there (written now, or found there). */
static int sd_write(pvstore_t *S, const pvstore_io_t *io, int kind, const char *path,
                    const char *line, int key_len) {
  const int first = strcmp(S->seen[kind], path) != 0;  /* first write of this boot to it */
  const int suspect = S->head_suspect;
  const int32_t size = io->sd_size(io->ctx, path);
  int lead_eol = 0, n;
  if (size == -2) return 0;
  if (size > 0 && (suspect || first)) {
    n = io->sd_tail(io->ctx, path, S->work, PVSTORE_LINE_MAX + 4);
    if (n < 0) return 0;
    if (suspect && tail_has(S->work, n, line, key_len)) {  /* already there: not again */
      S->n_dedup++;
      S->head_suspect = 0;
      return 1;
    }
    if (n > 0 && S->work[n - 1] != '\n') lead_eol = 1;  /* a cut-off line: leave it alone */
  }
  const int with_header = S->header[kind][0] && (size <= 0 || (first && kind == PVK_ROW));
  n = snprintf(S->work, sizeof(S->work), "%s%s%s%s" EOL, lead_eol ? EOL : "",
               with_header ? S->header[kind] : "", with_header ? EOL : "", line);
  if (n <= 0 || n >= (int)sizeof(S->work)) return 0;
  if (!io->sd_append(io->ctx, path, S->work, n)) return 0;
  snprintf(S->seen[kind], PVSTORE_PATH_MAX, "%s", path);
  S->head_suspect = 0;
  S->n_card++;
  return 1;
}

int pvstore_direct(const pvstore_t *S) { return S->sd_up && S->segments == 0; }

int pvstore_try_card(pvstore_t *S, const pvstore_io_t *io, int kind, const char *path,
                     const char *line, int key_len) {
  if (sd_write(S, io, kind, path, line, key_len)) return 1;
  card_down(S);
  S->head_suspect = 1;  /* the bytes may have reached the card all the same */
  return 0;
}

static int mount(pvstore_t *S, const pvstore_io_t *io, int quiet) {
  S->mount_used = 1;
  S->n_mounts++;
  if (!io->sd_mount(io->ctx)) return 0;
  if (!S->sd_up && !quiet) {
    char text[64];
    snprintf(text, sizeof(text), "card mounted; %lu lines wait in the buffer", (unsigned long)S->waiting);
    notice(S, "sd_back", text);
  }
  S->sd_up = 1;
  return 1;
}

int pvstore_remount(pvstore_t *S, const pvstore_io_t *io) {
  if (S->mount_used) return 0;  /* one mount attempt per row, whatever happens */
  return mount(S, io, 0);
}

/* ---- the ring ------------------------------------------------------------------------------------ */

static uint8_t *seg(pvstore_t *S, uint32_t seq) { return &S->seg_n[seq % PVSTORE_MAX_SEGMENTS]; }

/* Gives up the oldest segment: what was still waiting in it is counted as dropped. */
static void drop_oldest(pvstore_t *S, const pvstore_io_t *io) {
  const uint32_t left = *seg(S, S->first_seq) - S->first_done;
  io->fs_remove(io->ctx, S->first_seq);
  S->dropped += left;
  S->n_dropped += left;
  S->waiting -= left;
  *seg(S, S->first_seq) = 0;
  S->first_seq++;
  S->first_off = S->first_done = 0;
  S->segments--;
  S->head_suspect = 0;  /* the record that was suspect is gone */
  if (!S->full_noted) {
    notice(S, "ring_full", "LittleFS buffer full: the oldest lines are given up (count in ring_dropped)");
    S->full_noted = 1;
  }
  save_state(S, io);
}

int pvstore_buffer(pvstore_t *S, const pvstore_io_t *io, int kind, const char *path,
                   const char *line, int key_len) {
  int n, ok = 0;
  if (S->ring_ok) {
    n = snprintf(S->rec, sizeof(S->rec), "%c\t%d\t%s\t%s\n", KIND_CHAR[kind], key_len, path, line);
    if (n > 0 && n <= PVSTORE_REC_MAX) {
      if (S->segments == 0) {  /* an empty ring starts a new segment */
        S->first_seq = S->last_seq = S->next_seq++;
        S->first_off = S->first_done = 0;
        S->segments = 1;
        S->last_torn = 0;
        *seg(S, S->last_seq) = 0;
        save_state(S, io);
      } else if (*seg(S, S->last_seq) >= PVSTORE_SEG_RECORDS || S->last_torn) {
        if (S->segments >= PVSTORE_MAX_SEGMENTS) drop_oldest(S, io);  /* ring full */
        S->last_seq = S->next_seq++;
        S->segments++;
        S->last_torn = 0;
        *seg(S, S->last_seq) = 0;
      }
      ok = io->fs_append(io->ctx, S->last_seq, S->rec, n);
      if (!ok && S->segments > 1) {  /* flash full before the ring is: make room, once */
        drop_oldest(S, io);
        S->last_seq = S->next_seq++;  /* the failed append may have left a fragment */
        S->segments++;
        *seg(S, S->last_seq) = 0;
        ok = io->fs_append(io->ctx, S->last_seq, S->rec, n);
      }
      if (!ok) S->last_torn = 1;
    }
  }
  if (!ok) {  /* neither the card nor the flash took it */
    S->dropped++;
    S->n_dropped++;
    if (!S->lost_noted) {
      notice(S, "ring_error", "a line reached neither the card nor the LittleFS buffer (count in ring_dropped)");
      S->lost_noted = 1;
    }
    save_state(S, io);
    return 0;
  }
  (*seg(S, S->last_seq))++;
  S->waiting++;
  S->n_ring++;
  return 1;
}

/* Removes the oldest segment once everything in it is on the card (or unreadable). */
static void finish_first(pvstore_t *S, const pvstore_io_t *io) {
  const uint32_t left = *seg(S, S->first_seq) - S->first_done;
  if (left) {  /* records that could not be read back */
    S->dropped += left;
    S->n_dropped += left;
    S->waiting -= left;
  }
  io->fs_remove(io->ctx, S->first_seq);
  *seg(S, S->first_seq) = 0;
  S->first_seq++;
  S->first_off = S->first_done = 0;
  S->segments--;
}

/* Copies the oldest waiting record to the card. 1 = done (or skipped as unreadable),
 * 0 = the card refused it: stop for this row. */
static int flush_one(pvstore_t *S, const pvstore_io_t *io) {
  char *rec = S->rec, *eol, *p1, *p2, *p3;
  int kind = -1, key_len, len;
  const int n = io->fs_read(io->ctx, S->first_seq, S->first_off, rec, PVSTORE_REC_MAX);
  if (n < 0 && ++S->read_errors < 3) return 0;  /* flash read failed: again at the next row */
  S->read_errors = 0;
  eol = n > 0 ? memchr(rec, '\n', (size_t)n) : NULL;
  if (!eol) {  /* end of the segment, a fragment, or unreadable three times: it is done */
    finish_first(S, io);
    save_state(S, io);
    return 1;
  }
  *eol = 0;
  len = (int)(eol - rec) + 1;
  /* "K <tab> key_len <tab> path <tab> line" */
  p1 = strchr(rec, '\t');
  p2 = p1 ? strchr(p1 + 1, '\t') : NULL;
  p3 = p2 ? strchr(p2 + 1, '\t') : NULL;
  for (int k = 0; k < PVK_COUNT; k++)
    if (rec[0] == KIND_CHAR[k] && p1 == rec + 1) kind = k;
  if (kind < 0 || !p3 || p3 - p2 - 1 >= PVSTORE_PATH_MAX || p2[1] != '/') {
    S->dropped++;  /* not a record we wrote: skip it, count it */
    S->n_dropped++;
  } else {
    *p2 = *p3 = 0;
    key_len = 0;
    sscanf(p1 + 1, "%d", &key_len);
    if (!sd_write(S, io, kind, p2 + 1, p3 + 1, key_len)) {
      card_down(S);
      S->head_suspect = 1;
      return 0;
    }
    S->n_flushed++;
    S->flushed_run++;
  }
  S->first_off += (uint32_t)len;
  S->first_done++;
  S->waiting--;
  if (S->first_done >= *seg(S, S->first_seq)) finish_first(S, io);
  save_state(S, io);
  return 1;
}

void pvstore_service(pvstore_t *S, const pvstore_io_t *io) {
  const uint32_t start = io->millis(io->ctx);
  S->mount_used = 0;
  if (!S->sd_up) mount(S, io, 0);  /* the card is tried again at every row */
  if (!S->sd_up || S->segments == 0) return;
  for (int k = 0; k < PVSTORE_FLUSH_MAX && S->segments > 0; k++) {
    if (io->millis(io->ctx) - start >= PVSTORE_FLUSH_BUDGET_MS) break;
    if (!flush_one(S, io)) break;
  }
  if (S->segments == 0) {  /* the ring is empty again */
    char text[96];
    snprintf(text, sizeof(text), "%lu lines copied from the buffer to the card in order; %lu given up",
             (unsigned long)S->flushed_run, (unsigned long)S->dropped);
    notice(S, "ring_flushed", text);
    S->waiting = S->dropped = S->flushed_run = 0;
    S->full_noted = S->lost_noted = 0;
    save_state(S, io);
  }
}

int pvstore_put(pvstore_t *S, const pvstore_io_t *io, int kind, const char *path,
                const char *line, int key_len) {
  if (pvstore_direct(S)) {
    if (pvstore_try_card(S, io, kind, path, line, key_len)) return PVSTORE_SD;
    if (pvstore_remount(S, io) && pvstore_try_card(S, io, kind, path, line, key_len)) {
      return PVSTORE_SD_RETRY;
    }
  }
  return pvstore_buffer(S, io, kind, path, line, key_len) ? PVSTORE_RING : PVSTORE_LOST;
}

/* ---- start ----------------------------------------------------------------------------------------- */

void pvstore_init(pvstore_t *S, const char *header_row, const char *header_event,
                  const char *header_marker) {
  memset(S, 0, sizeof(*S));
  S->header[PVK_ROW] = header_row ? header_row : "";
  S->header[PVK_EVENT] = header_event ? header_event : "";
  S->header[PVK_MARKER] = header_marker ? header_marker : "";
  S->next_seq = 1;
}

void pvstore_begin(pvstore_t *S, const pvstore_io_t *io) {
  uint32_t lo = 0, hi = 0;
  unsigned long st_first = 0, st_off = 0, st_dropped = 0;
  const int have = io->fs_range(io->ctx, &lo, &hi);
  S->ring_ok = have >= 0;
  if (!S->ring_ok) notice(S, "ring_error", "LittleFS not usable: no buffer while the card is away");
  if (S->ring_ok) {  /* the saved place: "first segment, byte offset in it, lines given up" */
    const int n = io->fs_load(io->ctx, S->rec, 47);
    if (n > 0) {
      S->rec[n] = 0;
      sscanf(S->rec, "%lu %lu %lu", &st_first, &st_off, &st_dropped);
    }
    /* Segment numbers only ever grow, also over restarts, so a saved place can never
     * point into segments of a later outage. */
    if (st_first > S->next_seq) S->next_seq = (uint32_t)st_first;
  }
  if (have > 0) {
    char text[96];
    if (st_first > hi + 1) st_first = st_off = st_dropped = 0;  /* not about these segments */
    S->dropped = (uint32_t)st_dropped;
    /* segments before the saved place are already on the card: their removal was cut short */
    while (lo < st_first && lo <= hi) io->fs_remove(io->ctx, lo++);
    while (lo <= hi && hi - lo + 1 > PVSTORE_MAX_SEGMENTS) io->fs_remove(io->ctx, lo++);
    if (lo <= hi) {
      S->first_seq = lo;
      S->last_seq = hi;
      S->segments = hi - lo + 1;
      S->first_off = lo == st_first ? (uint32_t)st_off : 0;
      for (uint32_t s = lo; s <= hi; s++) {  /* complete lines in each segment: one pass each */
        int32_t c = io->fs_lines(io->ctx, s, s == lo ? S->first_off : 0);
        if (c < 0) c = 0;
        if (c > 255) c = 255;
        *seg(S, s) = (uint8_t)c;
        S->waiting += (uint32_t)c;
      }
      S->last_torn = 1;      /* never append to a segment an earlier run left open */
      S->head_suspect = 1;   /* its oldest record may have reached the card before the stop */
      snprintf(text, sizeof(text), "%lu lines from before the restart wait in the LittleFS buffer",
               (unsigned long)S->waiting);
      notice(S, "ring_found", text);
    }
    if (hi + 1 > S->next_seq) S->next_seq = hi + 1;
  }
  if (!mount(S, io, 1)) notice(S, "sd_lost", "no card at boot: lines go to the LittleFS buffer");
  S->mount_used = 0;
}

uint32_t pvstore_waiting(const pvstore_t *S) { return S->waiting; }
uint32_t pvstore_dropped(const pvstore_t *S) { return S->dropped; }

/* ---- for the PC tests -------------------------------------------------------------------------------- */

uint32_t pvstore_sizeof(void) { return (uint32_t)sizeof(pvstore_t); }
uint32_t pvstore_io_sizeof(void) { return (uint32_t)sizeof(pvstore_io_t); }

uint32_t pvstore_get(const pvstore_t *S, const char *what) {
  if (!strcmp(what, "sd_up")) return S->sd_up;
  if (!strcmp(what, "waiting")) return S->waiting;
  if (!strcmp(what, "dropped")) return S->dropped;
  if (!strcmp(what, "segments")) return S->segments;
  if (!strcmp(what, "first_seq")) return S->first_seq;
  if (!strcmp(what, "last_seq")) return S->last_seq;
  if (!strcmp(what, "n_card")) return S->n_card;
  if (!strcmp(what, "n_ring")) return S->n_ring;
  if (!strcmp(what, "n_flushed")) return S->n_flushed;
  if (!strcmp(what, "n_dropped")) return S->n_dropped;
  if (!strcmp(what, "n_dedup")) return S->n_dedup;
  if (!strcmp(what, "n_mounts")) return S->n_mounts;
  if (!strcmp(what, "ring_ok")) return S->ring_ok;
  return 0xFFFFFFFFu;
}
