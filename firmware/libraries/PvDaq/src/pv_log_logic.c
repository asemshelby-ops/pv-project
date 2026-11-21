/* See pv_log_logic.h. Plain C99: no Arduino, no heap, no waiting. */
#include "pv_log_logic.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

/* Accepted RTC times: 2025-01-01 .. 2100-01-01 UTC. Anything else is a bad read or a
 * clock that was never set (a DS3231 without its cell restarts at 2000-01-01). */
#define T_MIN_UNIX 1735689600u
#define T_MAX_UNIX 4102444800u
/* The RTC and millis() must tell the same story from one row to the next. A time that
 * is more than this away from "last accepted time + elapsed uptime" is a suspect read. */
#define RTC_JUMP_S 60
/* A suspect time is accepted when the next read confirms it to within this. */
#define RTC_CONFIRM_S 3

static const char *const SENSOR_NAME[PV_SENSOR_COUNT] = {
    "T1", "T2", "T3", "T4", "T5", "T6", "T7", "env", "rtc", "hub", "ina", "pyr", "sd"};

static const char HEADER[] =
    "date,time,T1,T2,T3,T4,T5,T_ambient,humidity,irradiance,voltage,current,power,wind,p,alarm,"
    "D,load_mode,sweep_id,uptime_ms,status,"
    "T6,T7,pyr_mV,sens_fail,"
    "f_T1,f_T2,f_T3,f_T4,f_T5,f_T6,f_T7,f_env,f_rtc,f_hub,f_ina,f_pyr,f_sd,"
    "ring_rows,ring_dropped,cycle_ms";

const char *pvlog_header(void) { return HEADER; }

const char *pvlog_sensor_name(int sensor) {
  return sensor >= 0 && sensor < PV_SENSOR_COUNT ? SENSOR_NAME[sensor] : "?";
}

int pvlog_rom_is_zero(const uint8_t *rom) {
  for (int k = 0; k < 8; k++)
    if (rom[k]) return 0;
  return 1;
}

void pvlog_rom_text(const uint8_t *rom, char *out) {
  for (int k = 0; k < 8; k++) snprintf(out + 3 * k, 4, k < 7 ? "%02X-" : "%02X", rom[k]);
}

/* Days since 1970-01-01 -> civil date (proleptic Gregorian; H. Hinnant's algorithm). */
void pvlog_utc_text(uint32_t unix_s, char *out, int size) {
  const uint32_t days = unix_s / 86400u, rem = unix_s % 86400u;
  const uint32_t z = days + 719468u;
  const uint32_t era = z / 146097u;
  const uint32_t doe = z - era * 146097u;                                   /* [0, 146096] */
  const uint32_t yoe = (doe - doe / 1460u + doe / 36524u - doe / 146096u) / 365u;
  const uint32_t doy = doe - (365u * yoe + yoe / 4u - yoe / 100u);          /* [0, 365] */
  const uint32_t mp = (5u * doy + 2u) / 153u;                               /* [0, 11] */
  const uint32_t d = doy - (153u * mp + 2u) / 5u + 1u;
  const uint32_t m = mp < 10u ? mp + 3u : mp - 9u;
  const uint32_t y = yoe + era * 400u + (m <= 2u ? 1u : 0u);
  snprintf(out, (size_t)size, "%04u-%02u-%02u,%02u:%02u:%02u", (unsigned)y, (unsigned)m,
           (unsigned)d, (unsigned)(rem / 3600u), (unsigned)(rem % 3600u / 60u),
           (unsigned)(rem % 60u));
}

int pvlog_time_at(const pvlog_t *L, uint32_t ms, uint32_t *unix_s) {
  if (!L->base_valid) return 0;
  *unix_s = L->base_unix + (uint32_t)((int32_t)(ms - L->base_ms) / 1000);
  return 1;
}

void pvlog_init(pvlog_t *L, const pvlog_cfg_t *cfg) {
  memset(L, 0, sizeof(*L));
  L->cfg = *cfg;
  if (L->cfg.fail_report_after == 0) L->cfg.fail_report_after = 3;
  if (L->cfg.scan_every == 0) L->cfg.scan_every = 6;
  snprintf(L->day_path, sizeof(L->day_path), "/nodate.csv");
  /* A ROM typed into two rows would put one probe in two columns: say so at boot. */
  for (int a = 0; a < PVLOG_N_T; a++) {
    for (int b = a + 1; b < PVLOG_N_T; b++) {
      if (!pvlog_rom_is_zero(cfg->t_rom[a]) && memcmp(cfg->t_rom[a], cfg->t_rom[b], 8) == 0) {
        char text[64];
        snprintf(text, sizeof(text), "T_ROM rows T%d and T%d hold the same ROM", a + 1, b + 1);
        pvlog_event(L, "config", text);
      }
    }
  }
}

/* ---- events ---------------------------------------------------------------------------- */

void pvlog_event(pvlog_t *L, const char *event, const char *detail) {
  if (L->ev_count == PVLOG_EVENT_QUEUE) {  /* full: count it, never block or overwrite */
    L->ev_missed++;
    return;
  }
  char *slot = L->events[(L->ev_head + L->ev_count) % PVLOG_EVENT_QUEUE];
  int n = snprintf(slot, PVLOG_EVENT_MAX, "%s,", event);
  if (n < 0 || n >= PVLOG_EVENT_MAX) n = PVLOG_EVENT_MAX - 1;
  for (const char *c = detail ? detail : ""; *c && n < PVLOG_EVENT_MAX - 1; c++) {
    /* one CSV field, one line: no commas, no control characters */
    slot[n++] = *c == ',' ? ';' : ((unsigned char)*c < 0x20 ? ' ' : *c);
  }
  slot[n] = 0;
  L->ev_count++;
}

int pvlog_next_event(pvlog_t *L, uint32_t now_ms, char *line, int size) {
  char when[24] = ",";  /* empty date and time */
  uint32_t unix_s;
  if (L->ev_count == 0 && L->ev_missed) {  /* tell how many could not be queued */
    char text[48];
    snprintf(text, sizeof(text), "%lu events not listed (queue full)", (unsigned long)L->ev_missed);
    L->ev_missed = 0;
    pvlog_event(L, "events_missed", text);
  }
  if (L->ev_count == 0) return 0;
  if (pvlog_time_at(L, now_ms, &unix_s)) pvlog_utc_text(unix_s, when, sizeof(when));
  snprintf(line, (size_t)size, "%s,%lu,%s", when, (unsigned long)now_ms, L->events[L->ev_head]);
  L->ev_head = (L->ev_head + 1) % PVLOG_EVENT_QUEUE;
  L->ev_count--;
  return 1;
}

/* ---- fail counters ------------------------------------------------------------------------ */

/* One row's result for one sensor. Returns its sens_fail bit (0 if ok). The event is
 * written once when the run reaches N, and once when the sensor is back after that. */
static uint32_t note(pvlog_t *L, int s, int ok, const char *detail) {
  char text[96];
  if (ok) {
    if (L->reported[s]) {
      snprintf(text, sizeof(text), "%s back after %lu failed rows", SENSOR_NAME[s],
               (unsigned long)L->run[s]);
      pvlog_event(L, "sensor_ok", text);
    }
    L->run[s] = 0;
    L->reported[s] = 0;
    return 0;
  }
  L->total[s]++;
  if (L->run[s] < 0xFFFFFFFFu) L->run[s]++;
  if (L->run[s] >= L->cfg.fail_report_after && !L->reported[s]) {
    L->reported[s] = 1;
    snprintf(text, sizeof(text), "%s failed %lu rows in a row: %s", SENSOR_NAME[s],
             (unsigned long)L->run[s], detail && detail[0] ? detail : "no valid reading");
    pvlog_event(L, "sensor_fail", text);
  }
  return 1u << s;
}

uint32_t pvlog_note_sd(pvlog_t *L, int ok) {
  return note(L, PV_SENSOR_SD, ok, "row not written to the card at once");
}

/* A device that failed is started again before its next read: begin() also restores what
 * a power glitch erased in the chip (INA226 calibration, BME280 sampling mode). */
static int ready(pvlog_t *L, const pvlog_io_t *io, int dev) {
  if (!L->up[dev]) L->up[dev] = io->begin(io->ctx, dev) ? 1 : 0;
  return L->up[dev];
}

/* ---- DS18B20 by ROM ------------------------------------------------------------------------ */

static int rom_known(const pvlog_t *L, const uint8_t *rom) {
  for (int k = 0; k < PVLOG_N_T; k++)
    if (memcmp(L->cfg.t_rom[k], rom, 8) == 0) return 1;
  for (int k = 0; k < PVLOG_N_KNOWN; k++)
    if (memcmp(L->cfg.known_rom[k], rom, 8) == 0) return 1;
  return 0;
}

/* Every ROM on the bus that no table lists is reported once per boot. */
static void scan_for_unknown(pvlog_t *L, const pvlog_io_t *io) {
  uint8_t roms[PVLOG_SCAN_MAX][8];
  const int n = io->ds_scan(io->ctx, &roms[0][0], PVLOG_SCAN_MAX);
  for (int i = 0; i < n && i < PVLOG_SCAN_MAX; i++) {
    if (pvlog_rom_is_zero(roms[i]) || rom_known(L, roms[i])) continue;
    int seen = 0;
    for (uint32_t w = 0; w < L->n_warned; w++) seen |= memcmp(L->warned[w], roms[i], 8) == 0;
    if (seen) continue;
    if (L->n_warned == PVLOG_WARN_MAX) {
      if (!L->warn_full) pvlog_event(L, "unknown_rom", "more unknown ROMs on the bus than are listed");
      L->warn_full = 1;
      continue;
    }
    memcpy(L->warned[L->n_warned++], roms[i], 8);
    char rom[24], text[80];
    pvlog_rom_text(roms[i], rom);
    snprintf(text, sizeof(text), "%s is on the bus but in no T_ROM row: not logged", rom);
    pvlog_event(L, "unknown_rom", text);
  }
}

/* ---- one row ------------------------------------------------------------------------------ */

/* Is this RTC time believable? It must be in range and agree with the last accepted time
 * plus the uptime that passed. One odd read is refused; two that agree are accepted. */
static int accept_time(pvlog_t *L, uint32_t unix_s, uint32_t ms, const char **why) {
  if (unix_s < T_MIN_UNIX || unix_s >= T_MAX_UNIX) {
    *why = "time out of range (RTC not set?)";
    return 0;
  }
  if (L->base_valid) {
    const int32_t expect_off = (int32_t)(ms - L->base_ms) / 1000;
    const int32_t diff = (int32_t)(unix_s - L->base_unix) - expect_off;
    if (diff > RTC_JUMP_S || diff < -RTC_JUMP_S) {
      int confirmed = 0;
      if (L->cand_valid) {
        const int32_t d2 = (int32_t)(unix_s - L->cand_unix) - (int32_t)(ms - L->cand_ms) / 1000;
        confirmed = d2 <= RTC_CONFIRM_S && d2 >= -RTC_CONFIRM_S;
      }
      L->cand_valid = 1;
      L->cand_unix = unix_s;
      L->cand_ms = ms;
      if (!confirmed) {
        *why = "time jump not accepted (waits for a second read that agrees)";
        return 0;
      }
      char text[72];
      snprintf(text, sizeof(text), "RTC time moved by %ld s and was confirmed by a second read",
               (long)diff);
      pvlog_event(L, "rtc_jump", text);
    }
  }
  L->cand_valid = 0;
  L->base_valid = 1;
  L->base_unix = unix_s;
  L->base_ms = ms;
  return 1;
}

void pvlog_read(pvlog_t *L, const pvlog_io_t *io, pvlog_row_t *r) {
  uint32_t st = 0, mask = 0;
  int ok;
  memset(r, 0, sizeof(*r));
  r->p = (double)NAN;
  r->ta = r->rh = r->g = r->v = r->i = r->p_el = r->pyr_mv = r->d = NAN;
  for (int k = 0; k < PVLOG_N_T; k++) r->t[k] = NAN;
  L->cycle_start_ms = r->uptime_ms = io->millis(io->ctx);

  /* 1. Time. */
  {
    uint32_t unix_s = 0;
    int valid = 0;
    const char *why = NULL;
    ok = ready(L, io, PV_SENSOR_RTC) && io->rtc_read(io->ctx, &unix_s, &valid);
    L->up[PV_SENSOR_RTC] = ok ? 1 : 0;
    if (!ok) why = io->detail(io->ctx, PV_SENSOR_RTC);
    else if (!valid) why = "time not set (run firmware/tools/set_rtc.py)";
    ok = ok && valid && accept_time(L, unix_s, r->uptime_ms, &why);
    mask |= note(L, PV_SENSOR_RTC, ok, why);
    r->have_time = ok;
    r->unix_s = ok ? unix_s : 0;
    if (!ok) st |= ST_RTC_INVALID;
  }

  /* 2. Irradiance at once: the fastest-changing input. Then its analog output (ADC). */
  ok = ready(L, io, PV_SENSOR_PYR) && io->pyr_read(io->ctx, &r->g) && !isnan(r->g);
  L->up[PV_SENSOR_PYR] = ok ? 1 : 0;
  mask |= note(L, PV_SENSOR_PYR, ok, io->detail(io->ctx, PV_SENSOR_PYR));
  if (!ok) {
    r->g = NAN;
    st |= ST_IRRAD_FAIL;
  }
  if (!io->pyr_analog(io->ctx, &r->pyr_mv)) r->pyr_mv = NAN;

  /* 3. Ambient. */
  ok = ready(L, io, PV_SENSOR_ENV) && io->env_read(io->ctx, &r->ta, &r->rh) && !isnan(r->ta);
  L->up[PV_SENSOR_ENV] = ok ? 1 : 0;
  mask |= note(L, PV_SENSOR_ENV, ok, io->detail(io->ctx, PV_SENSOR_ENV));
  if (!ok) {
    r->ta = r->rh = NAN;
    st |= ST_AMBIENT_FAIL;
  }

  /* 4. Panel V, I, P: the INA226 sits behind the PaHub. Without the PaHub the INA226 is
   *    not tried and not counted; it is started again when the PaHub is back (the units
   *    lose their calibration when the PaHub loses power). */
  ok = L->up[PV_SENSOR_HUB] ? io->hub_check(io->ctx) : io->begin(io->ctx, PV_SENSOR_HUB);
  L->up[PV_SENSOR_HUB] = ok ? 1 : 0;
  mask |= note(L, PV_SENSOR_HUB, ok, io->detail(io->ctx, PV_SENSOR_HUB));
  if (!ok) {
    L->up[PV_SENSOR_INA] = 0;
    st |= ST_PANEL_FAIL;
  } else {
    ok = ready(L, io, PV_SENSOR_INA) && io->ina_read(io->ctx, &r->v, &r->i, &r->p_el) &&
         !isnan(r->v) && !isnan(r->i) && !isnan(r->p_el);
    L->up[PV_SENSOR_INA] = ok ? 1 : 0;
    mask |= note(L, PV_SENSOR_INA, ok, io->detail(io->ctx, PV_SENSOR_INA));
    if (!ok) st |= ST_PANEL_FAIL;
  }
  if (st & ST_PANEL_FAIL) r->v = r->i = r->p_el = NAN;

  /* 5. DS18B20: one conversion for all, then each column asks for ITS ROM. A probe that
   *    is missing leaves its own column empty; no other column can move. */
  {
    int fitted = 0;
    for (int k = 0; k < PVLOG_N_T; k++) fitted |= !pvlog_rom_is_zero(L->cfg.t_rom[k]);
    if (fitted) io->ds_convert(io->ctx);  /* a failed conversion shows in every read below */
    for (int k = 0; k < PVLOG_N_T; k++) {
      if (pvlog_rom_is_zero(L->cfg.t_rom[k])) {  /* no ROM typed in for this column */
        if (k < 5) st |= ST_TROM_UNMAPPED | ST_TEMP_FAIL;  /* T6, T7: simply not fitted */
        continue;
      }
      float t = NAN;
      ok = io->ds_read(io->ctx, L->cfg.t_rom[k], &t) && !isnan(t);
      mask |= note(L, PV_SENSOR_T1 + k, ok, "no valid reading from its ROM");
      r->t[k] = ok ? t : NAN;
      if (!ok && k < 5) st |= ST_TEMP_FAIL;
    }
    if (L->n % L->cfg.scan_every == 0) scan_for_unknown(L, io);
  }

  r->status = st;
  r->sens_fail = mask;
  L->n++;
}

void pvlog_day_path(pvlog_t *L, const pvlog_row_t *r, char *path) {
  if (r->have_time) {
    char date[24];
    pvlog_utc_text(r->unix_s, date, sizeof(date));  /* "YYYY-MM-DD,..." */
    snprintf(L->day_path, sizeof(L->day_path), "/%.4s%.2s%.2s.csv", date, date + 5, date + 8);
  }
  snprintf(path, PVLOG_PATH_MAX, "%s", L->day_path);
}

void pvlog_end_cycle(pvlog_t *L, uint32_t now_ms) { L->cycle_ms = now_ms - L->cycle_start_ms; }

/* ---- CSV row -------------------------------------------------------------------------------- */

typedef struct {
  char *out;
  int size, n, overflow;
} text_t;

static void put(text_t *t, const char *s) {
  const int len = (int)strlen(s);
  if (t->n + len >= t->size) {
    t->overflow = 1;
    return;
  }
  memcpy(t->out + t->n, s, (size_t)len + 1);
  t->n += len;
}

/* ",value" with `decimals` digits, or "," alone if the value is NaN (empty = no reading). */
static void put_num(text_t *t, double v, int decimals) {
  char b[40] = ",";
  if (!isnan(v)) snprintf(b, sizeof(b), ",%.*f", decimals, v);
  put(t, b);
}

static void put_uint(text_t *t, uint32_t v) {
  char b[16];
  snprintf(b, sizeof(b), ",%lu", (unsigned long)v);
  put(t, b);
}

int pvlog_format_row(const pvlog_t *L, const pvlog_row_t *r, uint32_t ring_rows,
                     uint32_t ring_dropped, char *out, int size) {
  text_t t = {out, size, 0, 0};
  char b[40];
  if (size < 2) return 0;
  out[0] = 0;
  /* the 21 columns that existed before CSV v2, unchanged */
  if (r->have_time) {
    pvlog_utc_text(r->unix_s, b, sizeof(b));
    put(&t, b);
  } else {
    put(&t, ",");  /* empty date, empty time */
  }
  for (int k = 0; k < 5; k++) put_num(&t, r->t[k], 4);
  put_num(&t, r->ta, 2);
  put_num(&t, r->rh, 2);
  put_num(&t, r->g, 0);
  put_num(&t, r->v, 4);
  put_num(&t, r->i, 4);
  put_num(&t, r->p_el, 4);
  put(&t, ",NaN");  /* wind: no anemometer in this phase. "NaN" = not measured */
  put_num(&t, r->p, 6);
  snprintf(b, sizeof(b), ",%ld", (long)r->alarm);
  put(&t, b);
  if (isnan(r->d)) put(&t, ",NaN");  /* no load fitted */
  else put_num(&t, r->d, 4);
  put(&t, ",");
  put(&t, r->load_mode ? r->load_mode : "none");
  put_uint(&t, r->sweep_id);
  put_uint(&t, r->uptime_ms);
  put_uint(&t, r->status);
  /* appended in CSV v2 */
  put_num(&t, r->t[5], 4);
  put_num(&t, r->t[6], 4);
  put_num(&t, r->pyr_mv, 0);
  put_uint(&t, r->sens_fail);
  for (int s = 0; s < PV_SENSOR_COUNT; s++) put_uint(&t, L->total[s]);
  put_uint(&t, ring_rows);
  put_uint(&t, ring_dropped);
  put_uint(&t, L->cycle_ms);
  return t.overflow ? 0 : t.n;
}

/* ---- for the PC tests ------------------------------------------------------------------------- */

uint32_t pvlog_sizeof(void) { return (uint32_t)sizeof(pvlog_t); }
uint32_t pvlog_row_sizeof(void) { return (uint32_t)sizeof(pvlog_row_t); }
uint32_t pvlog_cfg_sizeof(void) { return (uint32_t)sizeof(pvlog_cfg_t); }
uint32_t pvlog_io_sizeof(void) { return (uint32_t)sizeof(pvlog_io_t); }

uint32_t pvlog_get(const pvlog_t *L, const char *what, int sensor) {
  if (sensor < 0 || sensor >= PV_SENSOR_COUNT) sensor = 0;
  if (!strcmp(what, "total")) return L->total[sensor];
  if (!strcmp(what, "run")) return L->run[sensor];
  if (!strcmp(what, "reported")) return L->reported[sensor];
  if (!strcmp(what, "up")) return L->up[sensor];
  if (!strcmp(what, "n")) return L->n;
  if (!strcmp(what, "cycle_ms")) return L->cycle_ms;
  if (!strcmp(what, "events")) return L->ev_count;
  return 0xFFFFFFFFu;
}
