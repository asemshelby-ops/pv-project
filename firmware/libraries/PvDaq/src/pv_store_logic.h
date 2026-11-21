/* Where a line of the logger goes: to the microSD card, or, while the card is missing or
 * a write fails, into a ring buffer on the ESP32's own flash (LittleFS), from which it is
 * copied to the card, in order, when the card is back. Plain C99, no Arduino: compiled
 * into the logger and, on the PC, into tests/test_logger_robustness.py with a FAKE card
 * and a FAKE flash that can be removed or told to fail.
 *
 * The rules (task of 2026-11-21):
 *   - the card is tried again at every row (pvstore_service);
 *   - while anything waits in the ring, NEW lines go to the ring too, so the lines of a
 *     file reach the card in the order they were made;
 *   - a line is on the card exactly once: before a line whose earlier write failed (or
 *     the first one after a restart) is written again, the end of its file is read, and
 *     if the line is already there it is not written a second time;
 *   - a write that was cut off leaves a fragment without a line end: the next write
 *     starts on a fresh line, so the fragment cannot join a good row;
 *   - ring full: the OLDEST segment (32 records) is given up, and counted;
 *   - the card is never formatted. Only the internal flash is, by its driver, if it
 *     holds no LittleFS at all (first boot).
 *
 * The ring is a row of segment files, each written by appending only (LittleFS appends
 * cheaply but rewrites a file to change its middle). A small state file holds the place
 * of the oldest record not yet on the card and the number given up. Everything is on
 * flash, so records survive a watchdog reset or a power cut.
 */
#ifndef PV_STORE_LOGIC_H
#define PV_STORE_LOGIC_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define PVSTORE_LINE_MAX 400       /* longest line, terminator included (= PVLOG_LINE_MAX) */
#define PVSTORE_PATH_MAX 20        /* "/YYYYMMDD.csv" */
#define PVSTORE_REC_MAX 440        /* one ring record: kind, key length, path, line */
#define PVSTORE_WORK_MAX 1024      /* header block + line, written in one append */
#define PVSTORE_SEG_RECORDS 32     /* records per segment file (5 min 20 s of rows) */
#define PVSTORE_MAX_SEGMENTS 128   /* ring capacity 4096 records: 11.4 h of 10 s rows */
#define PVSTORE_FLUSH_MAX 64       /* records copied to the card per row, at the most ... */
#define PVSTORE_FLUSH_BUDGET_MS 1000  /* ... and no new one is started after this time */
#define PVSTORE_NOTICES 6
#define PVSTORE_NOTICE_MAX 112

enum { PVK_ROW = 0, PVK_EVENT = 1, PVK_MARKER = 2, PVK_COUNT = 3 };   /* kinds of line */
enum { PVSTORE_LOST = 0, PVSTORE_SD = 1, PVSTORE_SD_RETRY = 2, PVSTORE_RING = 3 };

/* The driver table. Every function returns within its driver's own time-outs. */
typedef struct {
  void *ctx;
  uint32_t (*millis)(void *ctx);
  /* microSD card (FAT). path = "/name.csv". No function here may format the card. */
  int (*sd_mount)(void *ctx);                                  /* 1 = a card is mounted */
  int32_t (*sd_size)(void *ctx, const char *path);             /* bytes; -1 no such file; -2 error */
  int (*sd_append)(void *ctx, const char *path, const char *data, int n);  /* 1 = all n bytes are ON the card */
  int (*sd_tail)(void *ctx, const char *path, char *buf, int n);           /* last <= n bytes; count, -1 error */
  /* ring segments on the internal flash, named by their number */
  int (*fs_range)(void *ctx, uint32_t *first, uint32_t *last); /* 1 = segments exist, 0 = none, -1 = flash unusable */
  int32_t (*fs_size)(void *ctx, uint32_t seq);                 /* bytes; -1 no such segment */
  int (*fs_append)(void *ctx, uint32_t seq, const char *data, int n);      /* 1 = written */
  int (*fs_read)(void *ctx, uint32_t seq, uint32_t off, char *buf, int n); /* count, -1 error */
  int32_t (*fs_lines)(void *ctx, uint32_t seq, uint32_t off);  /* line feeds from byte off on; -1 error */
  int (*fs_remove)(void *ctx, uint32_t seq);
  int (*fs_save)(void *ctx, const char *text);                 /* the state file, replaced whole */
  int (*fs_load)(void *ctx, char *buf, int n);                 /* its text; length, -1 none */
  void (*log)(void *ctx, const char *text);                    /* a '#' line on Serial */
} pvstore_io_t;

typedef struct {
  const char *header[PVK_COUNT];   /* first line(s) of a file of each kind */
  uint8_t sd_up;                   /* the card answered at the last attempt */
  uint8_t mount_used;              /* the one mount attempt of this row is spent */
  uint8_t head_suspect;            /* the next card write may repeat a line already there */
  uint8_t ring_ok;                 /* the flash is usable */
  uint8_t last_torn;               /* the newest segment ends in a fragment: start another */
  uint8_t full_noted, lost_noted;  /* "ring_full" / "ring_error" were reported this outage */
  uint8_t read_errors;             /* flash reads of the oldest record that failed in a row */
  char seen[PVK_COUNT][PVSTORE_PATH_MAX];  /* file of each kind written since boot */
  uint32_t first_seq, last_seq, next_seq, segments;
  uint32_t first_off, first_done;  /* oldest waiting record: byte offset, records before it */
  uint8_t seg_n[PVSTORE_MAX_SEGMENTS];     /* records in each live segment (seq % MAX) */
  uint32_t waiting;                /* records in the ring, not yet on the card */
  uint32_t dropped;                /* given up since the ring was last empty (kept on flash) */
  uint32_t flushed_run;            /* copied to the card since the ring was last empty */
  uint32_t n_card, n_ring, n_flushed, n_dropped, n_dedup, n_mounts;   /* since boot */
  char notice[PVSTORE_NOTICES][PVSTORE_NOTICE_MAX];
  uint32_t notice_head, notice_count;
  char rec[PVSTORE_REC_MAX + 8];
  char work[PVSTORE_WORK_MAX];
} pvstore_t;

/* header_row is written at the top of a row file and again before the first row each
 * boot writes to it; the other two only at the top of a new file. The strings must stay
 * valid (they are not copied). */
void pvstore_init(pvstore_t *S, const char *header_row, const char *header_event,
                  const char *header_marker);

/* At boot: finds what an earlier run left in the ring, and mounts the card. */
void pvstore_begin(pvstore_t *S, const pvstore_io_t *io);

/* Once per row, before the row is stored: if the card is down, ONE attempt to mount it;
 * if it is up, copies waiting records to it, oldest first, within the flush limits. */
void pvstore_service(pvstore_t *S, const pvstore_io_t *io);

/* Stores one line (no line end). key_len: how many leading characters identify the
 * record when the end of the file is checked for it; 0 = the whole line.
 * Returns PVSTORE_SD, PVSTORE_SD_RETRY (after one re-mount), PVSTORE_RING or PVSTORE_LOST. */
int pvstore_put(pvstore_t *S, const pvstore_io_t *io, int kind, const char *path,
                const char *line, int key_len);

/* The steps of pvstore_put, for a caller whose line text depends on the outcome (the
 * row's status bits): */
int pvstore_direct(const pvstore_t *S);          /* card up and nothing waiting */
int pvstore_try_card(pvstore_t *S, const pvstore_io_t *io, int kind, const char *path,
                     const char *line, int key_len);       /* 1 = on the card */
int pvstore_remount(pvstore_t *S, const pvstore_io_t *io); /* 1 = card up again */
int pvstore_buffer(pvstore_t *S, const pvstore_io_t *io, int kind, const char *path,
                   const char *line, int key_len);         /* 1 = in the ring */

/* What happened, for /events.csv: "event", "detail". 1, or 0 if there is nothing. */
int pvstore_next_notice(pvstore_t *S, char *event, int event_size, char *detail, int detail_size);

uint32_t pvstore_waiting(const pvstore_t *S);
uint32_t pvstore_dropped(const pvstore_t *S);

/* For the PC tests. */
uint32_t pvstore_sizeof(void);
uint32_t pvstore_io_sizeof(void);
uint32_t pvstore_get(const pvstore_t *S, const char *what);

#ifdef __cplusplus
}
#endif
#endif
