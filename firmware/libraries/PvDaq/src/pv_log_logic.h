/* The logger's decisions for one 10 s row, without any hardware in them: which sensor is
 * read and retried, what a failed read leaves in the row, the fail counters, the events
 * that are logged once, the DS18B20 columns by ROM, and the text of the CSV row.
 * Plain C99, no Arduino: the same file is compiled into the logger and, on the PC, into
 * tests/test_logger_robustness.py, where FAKE drivers stand in for the sensors and can be
 * told to fail, time out or disappear.
 *
 * The hardware is reached only through pvlog_io_t, a table of driver functions. Every
 * driver function must return within its own time-out (firmware/libraries/PvDaq drivers;
 * the limits are listed in docs/paper/13-logger-robustness.md) and reports 1 = ok or
 * 0 = failed. Nothing here waits, loops on a device or allocates memory.
 *
 * Rules (task of 2026-11-21):
 *   - a failed read leaves its own fields EMPTY and sets its bits; no other field moves;
 *   - a sensor that failed is started again (begin) and read again at the NEXT row;
 *   - after `fail_report_after` failed rows in a row a sensor is reported ONCE (event
 *     sensor_fail), and once more when it is back (event sensor_ok);
 *   - T1..T7 are read by the ROM codes of the T_ROM table, never by the bus search order;
 *     a ROM found on the bus that is in no table is reported once (event unknown_rom).
 */
#ifndef PV_LOG_LOGIC_H
#define PV_LOG_LOGIC_H

#include <stdint.h>
#include "pv_status.h"

#ifdef __cplusplus
extern "C" {
#endif

#define PVLOG_CSV_VERSION 2
/* First line of every block of rows this firmware writes (a new file, and the first rows
 * after each boot). Files without it are older: 16 or 21 columns (data/schema.py). */
#define PVLOG_VERSION_LINE "# nasim-logger-csv v2"
#define PVLOG_EVENTS_HEADER "date,time,uptime_ms,event,detail"
#define PVLOG_EVENTS_PATH "/events.csv"

#define PVLOG_N_T 7            /* DS18B20 columns T1..T7 (rows of T_ROM) */
#define PVLOG_N_KNOWN 2        /* other probes that belong on the bus (load heatsink) */
#define PVLOG_SCAN_MAX 12      /* ROMs taken from one bus scan */
#define PVLOG_WARN_MAX 8       /* unknown ROMs remembered, so each is reported once */
#define PVLOG_LINE_MAX 400     /* a CSV row never exceeds this, terminator included */
#define PVLOG_PATH_MAX 20      /* "/YYYYMMDD.csv" */
#define PVLOG_EVENT_MAX 128    /* "event,detail" text of one event */
#define PVLOG_EVENT_QUEUE 16   /* events waiting to be written (every sensor can fail in the
                                  same row); more than this in one row are counted, not kept */

/* The driver table. ctx is passed back unchanged. dev is a PV_SENSOR_* number
 * (pv_status.h); only RTC, ENV, HUB, INA and PYR have a begin(). */
typedef struct {
  void *ctx;
  uint32_t (*millis)(void *ctx);
  int (*begin)(void *ctx, int dev);                       /* detect + configure again */
  const char *(*detail)(void *ctx, int dev);              /* the driver's last error text */
  int (*rtc_read)(void *ctx, uint32_t *unix_s, int *time_valid);   /* UTC seconds */
  int (*pyr_read)(void *ctx, float *g_wm2);               /* PYR20, RS485 Modbus */
  int (*pyr_analog)(void *ctx, float *mv);                /* PYR20 0-2 V output, millivolts */
  int (*env_read)(void *ctx, float *t_c, float *rh_pct);  /* rh = NaN on a BMP280 */
  int (*hub_check)(void *ctx);                            /* the PaHub answers */
  int (*ina_read)(void *ctx, float *v, float *i, float *p);
  int (*ds_convert)(void *ctx);                           /* all probes: start + wait */
  int (*ds_read)(void *ctx, const uint8_t *rom, float *t_c);   /* one probe, by its ROM */
  int (*ds_scan)(void *ctx, uint8_t *roms, int max);      /* 8 bytes per ROM; count or -1 */
  void (*log)(void *ctx, const char *text);               /* a '#' line on Serial */
} pvlog_io_t;

typedef struct {
  uint8_t t_rom[PVLOG_N_T][8];          /* T1..T7; an all-zero row = no probe fitted */
  uint8_t known_rom[PVLOG_N_KNOWN][8];  /* expected on the bus but not a column */
  uint32_t fail_report_after;           /* N: failed rows in a row before the one report */
  uint32_t scan_every;                  /* bus scan for unknown ROMs every this many rows */
} pvlog_cfg_t;

/* One 10 s row. pvlog_read() fills the measured part; the caller adds the rest. */
typedef struct {
  double p;                    /* NASIM probability, NaN = empty            (caller) */
  uint32_t uptime_ms;
  uint32_t unix_s;             /* UTC, valid only if have_time */
  int32_t have_time;
  float t[PVLOG_N_T];          /* T1..T7, NaN = empty field */
  float ta, rh, g, v, i, p_el;
  float pyr_mv;
  float d;                     /* load duty; NaN = "NaN" (no load fitted)   (caller) */
  int32_t alarm;               /*                                           (caller) */
  uint32_t sweep_id;           /*                                           (caller) */
  uint32_t status;             /* ST_* bits; the caller may add detector bits */
  uint32_t sens_fail;          /* bit n = sensor n (PV_SENSOR_*) failed at this row */
  const char *load_mode;       /* "none" if NULL                            (caller) */
} pvlog_row_t;

typedef struct {
  pvlog_cfg_t cfg;
  uint32_t n;                              /* rows read so far */
  uint8_t up[PV_SENSOR_COUNT];             /* device started and its last read was good */
  uint32_t total[PV_SENSOR_COUNT];         /* failed rows since boot: the f_* columns */
  uint32_t run[PV_SENSOR_COUNT];           /* failed rows in a row */
  uint8_t reported[PV_SENSOR_COUNT];       /* the one sensor_fail event was written */
  uint8_t warned[PVLOG_WARN_MAX][8];       /* unknown ROMs already reported */
  uint32_t n_warned;
  uint8_t warn_full;
  /* time base: the last accepted RTC time and the uptime it was read at */
  uint8_t base_valid, cand_valid;
  uint32_t base_unix, base_ms, cand_unix, cand_ms;
  char day_path[PVLOG_PATH_MAX];           /* file of the last row that had a time */
  uint32_t cycle_start_ms, cycle_ms;       /* cycle_ms: what the row before this one took */
  char events[PVLOG_EVENT_QUEUE][PVLOG_EVENT_MAX];
  uint32_t ev_head, ev_count, ev_missed;
} pvlog_t;

void pvlog_init(pvlog_t *L, const pvlog_cfg_t *cfg);

/* Reads every sensor once, in the fixed order RTC, PYR20, BME280, PaHub + INA226, DS18B20.
 * Fills the measured fields, status and sens_fail of *r; updates counters; queues events. */
void pvlog_read(pvlog_t *L, const pvlog_io_t *io, pvlog_row_t *r);

/* The SD card as the 13th "sensor": ok = this row reached the card at once. Returns the
 * sens_fail bit to add to the row (0 if ok). */
uint32_t pvlog_note_sd(pvlog_t *L, int ok);

/* File the row belongs to: /YYYYMMDD.csv (UTC date). A row without time goes to the file
 * of the last row that had one, or /nodate.csv if there was none since boot. */
void pvlog_day_path(pvlog_t *L, const pvlog_row_t *r, char *path);

/* Header line and one CSV row (no line end). Both return the length written, 0 if the
 * buffer is too small. ring_rows / ring_dropped: state of the LittleFS buffer (0 without). */
const char *pvlog_header(void);
int pvlog_format_row(const pvlog_t *L, const pvlog_row_t *r, uint32_t ring_rows,
                     uint32_t ring_dropped, char *out, int size);

/* Events. pvlog_event() queues one ("event", free text; commas in the text become ';').
 * pvlog_next_event() takes the oldest out as a finished /events.csv line: 1, or 0 if none. */
void pvlog_event(pvlog_t *L, const char *event, const char *detail);
int pvlog_next_event(pvlog_t *L, uint32_t now_ms, char *line, int size);

/* Call when the row is stored: cycle_ms of the NEXT row = time this one took. */
void pvlog_end_cycle(pvlog_t *L, uint32_t now_ms);

/* UTC "YYYY-MM-DD,HH:MM:SS" (19 characters) of Unix seconds. */
void pvlog_utc_text(uint32_t unix_s, char *out, int size);
/* UTC estimate for an uptime from the last accepted RTC time: 1, or 0 if there is none. */
int pvlog_time_at(const pvlog_t *L, uint32_t ms, uint32_t *unix_s);

const char *pvlog_sensor_name(int sensor);
int pvlog_rom_is_zero(const uint8_t *rom);
void pvlog_rom_text(const uint8_t *rom, char *out);   /* "28-xx-..-xx", out >= 24 chars */

/* For the PC tests (the Python mirrors of the structs check their sizes with these). */
uint32_t pvlog_sizeof(void);
uint32_t pvlog_row_sizeof(void);
uint32_t pvlog_cfg_sizeof(void);
uint32_t pvlog_io_sizeof(void);
uint32_t pvlog_get(const pvlog_t *L, const char *what, int sensor);

#ifdef __cplusplus
}
#endif
#endif
