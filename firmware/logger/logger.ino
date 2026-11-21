// Phase 0 data logger. Every 10 s (millis schedule, no drift): read every sensor, leave
// the fields of a failed one empty, and write one CSV row to the microSD card and to
// Serial. Columns: the schema of CLAUDE.md + wind (data/schema.py COLUMNS), then p (the
// NASIM-GBT probability; no wind, double precision) and alarm, then five columns:
// D, load_mode, sweep_id (NaN, none, 0 while HAS_LOAD is 0: no load fitted), uptime_ms, status.
// alarm holds one bit per on-board detector (data/schema.py ALARM_BITS): 1 = NASIM-GBT,
// 2 = threshold rule, 4 = CUSUM; values add up, 0 = no alarm. All three see the same rows.
// CSV v2 (2026-11-21) appends, after status: T6, T7, pyr_mV, sens_fail, one fail counter
// per sensor (f_T1 ... f_sd), ring_rows, ring_dropped, cycle_ms. The header and the row
// text are built in PvDaq/src/pv_log_logic.c (pvlog_header, pvlog_format_row); every block
// of rows starts with the line "# nasim-logger-csv v2" and the header.
//
// ROBUSTNESS (docs/paper/13-logger-robustness.md): what is read, retried, counted and
// reported is decided in pv_log_logic.c, which is also compiled on the PC and tested with
// fake drivers (tests/test_logger_robustness.py). This sketch only connects it to the
// real drivers (the io... functions below), runs the detectors and writes the files.
//
// CARD AND WATCHDOG (same document, sections 5 and 6): rows, events and markers go to the
// card through pv_store_logic.c. While the card is missing or a write fails they wait in
// a ring buffer on the ESP32's own flash (LittleFS) and are copied to the card, in order,
// when it is back; the card is tried again at every row and is NEVER formatted here. The
// task watchdog watches loop(): if it does not come round for LOOP_WDT_MS the chip resets,
// and the next boot writes the reset reason and the last stage reached to /events.csv.
//
// Timestamps are UTC (the DS3231 holds UTC). Missing value = empty field. Lines starting
// with '#' on Serial are status messages, not data. Files: /YYYYMMDD.csv per day (UTC
// date), /markers.csv for button presses, /rom_scan.txt for the DS18B20 ROMs found at boot,
// /events.csv for things that are reported once (a boot, a sensor failing, an unknown ROM,
// the card going and coming back).
// With the measurement load (HAS_LOAD 1 in pv_config.h; docs/paper/07): /SWYYYYMMDD.csv and
// /SSYYYYMMDD.csv for the I-V sweeps, /load_log.csv for mode changes and trips, and two more
// kinds of rows in /markers.csv, written by the serial commands LOAD OFF and LOAD ON
// (controlled open-circuit tests) at the millisecond the load really switched.
// A row is never dropped: without a valid RTC time it is written with empty date and time,
// its uptime_ms and the ST_RTC_INVALID status bit (bits: PvDaq/src/pv_status.h), into the
// last day file used since boot, or /nodate.csv if there was none.
// Build: python firmware/build_all.py logger   (adds the include paths to export/c).
//
// SAFETY: the pump output is held LOW. Both load outputs are held LOW while HAS_LOAD is 0;
// with HAS_LOAD 1 they start LOW and go LOW again on every fault (PvDaq/src/pv_load_logic.h).
// The alarms are only logged; NASIM is trained and the baselines are tuned on SYNTHETIC
// data: all must be redone on real data before they drive anything. The load never acts
// on an alarm.
#include <PvDaq.h>
#include <esp_system.h>
#include <esp_task_wdt.h>
#include <pv_flashring.h>
#include <pv_load.h>
#include <pv_log_logic.h>
#include <pv_status.h>
#include <pv_store_logic.h>

extern "C" {  // export/c is C, compiled here through nasim_core.c / nasim_model.c
#include "nasim.h"
#include "nasim_tuned.h"
void nasim_score_nowind(real *input, real *output);
}

// ---- configuration -------------------------------------------------------------------
// Detector settings: generated, never typed here. nasim_tuned.h = the run's tuned.json
// (analysis/export_model.py); nasim_config.h = config.py.
static const real NASIM_P_THRESHOLD = NASIM_TUNED_P_THRESHOLD;
static const int NASIM_PERSIST = NASIM_GBT_PERSIST;
static const real THR_RISE_C = NASIM_TUNED_THR_RISE_C;        // threshold rule, degC in 10 min
static const real CUSUM_KAPPA_C = NASIM_TUNED_CUSUM_KAPPA_C;  // CUSUM allowance, degC per row
static const real CUSUM_H_C = NASIM_TUNED_CUSUM_H_C;          // CUSUM alarm level, degC
static const int NASIM_START_MIN = 6 * 60;    // detector window = synthetic day window,
static const int NASIM_END_MIN = 18 * 60;     // config.DAY_START / DAY_END (local time)
static const int32_t UTC_OFFSET_S = 3 * 3600; // Makkah = UTC+3: NASIM's time inputs are local
static const uint8_t RESYNC_EVERY = 6;        // re-find the RTC second edge every 60 s
// A sensor that failed is started and read again at the very next row (pv_log_logic.c).
static const uint32_t FAIL_REPORT_AFTER = 3;  // failed rows in a row before the ONE report
static const uint32_t ROM_SCAN_EVERY = 6;     // 1-Wire bus scan for unknown ROMs every 60 s
static const uint8_t I2C_RESTART_AFTER = 3;   // rows with RTC, BME280 and PaHub all silent
// A row with a missing detector input is skipped by the detector, which keeps its state.
// Only this many skipped rows in a row clear the state (the 10 s spacing the features
// assume is then too far off). The value is config.py DET_RESET_AFTER_FAILS, through the
// generated nasim_config.h: the offline analysis (analysis/ingest.py) uses the same one.
static const uint8_t DET_RESET_AFTER_FAILS = NASIM_DET_RESET_AFTER_FAILS;

// DS18B20 columns by ROM: row 0 = T1 ... row 6 = T7. T1..T5 are the back-sheet sensors
// (the detectors' inputs); T6 and T7 are the two further probes on the bus.
// TODO(user): fill in the rows after the water-bath cross-calibration and mounting. The
// boot ROM scan (Serial and /rom_scan.txt) prints every ROM it finds as a ready-made row,
// e.g. 28-FF-64-1E-0F-00-00-A1 -> {0x28, 0xFF, 0x64, 0x1E, 0x0F, 0x00, 0x00, 0xA1}.
// A column is ALWAYS the probe with the ROM of its row, never a position in the bus
// search. A row left at zero means "no probe for this column": the column stays empty;
// for T1..T5 the row also carries ST_TROM_UNMAPPED and the detectors do not run. A probe
// that is unplugged leaves only its own column empty.
static const uint8_t T_ROM[PVLOG_N_T][8] = {{0}, {0}, {0}, {0}, {0}, {0}, {0}};

// Measurement load (used only with HAS_LOAD 1). DS18B20 on the resistor heatsink, by ROM,
// same notation as T_ROM. TODO(user): fill in when the sensor is mounted (bench B5 prints
// it). While it is all zero the load stays in TRIP ("heatsink sensor missing"): both
// branches off, by design.
static const uint8_t HEATSINK_ROM[8] = {0};
// Loop watchdog (always on since 2026-11-21): if loop() does not come round for this long
// the chip resets and logging starts again by itself. 30 s = three row periods, and well
// above the longest pass that is still normal: every sensor at its time-out (about 7 s
// [EST], doc 13 section 2) + a failed card mount + the 1 s flush + the RTC edge (1.1 s).
// With the load fitted the value stays at the 15 s chosen for it on 2026-11-13: a reset
// releases the pins and the gate pull-downs switch the load off. [ASSUMPTION] both; the
// 15 s must be checked again in bench B7, because a pass can now also hold a card flush.
static const uint32_t LOOP_WDT_MS = HAS_LOAD ? 15000 : 30000;
static const uint32_t HEALTH_EVERY = 360;     // rows between two "health" events (1 h)
// Button presses and LOAD OFF / LOAD ON marks: one file, one format (BRINGUP.md step 9).
static const char MARKERS_PATH[] = "/markers.csv";
static const char MARKERS_HEADER[] = "date,time,millis";

// ---- devices -----------------------------------------------------------------------------
PvDs3231 rtc;
PvBme280 env;
PvPaHub hub;
PvIna226 panel("INA226 panel", &hub, PAHUB_CH_PANEL);
PvDs18b20 probes(pins::ONEWIRE);
PvPyr20Modbus pyr(Serial2, pins::RS485_RX, pins::RS485_TX);
PvPyr20Analog pyr_analog(pins::PYR20_ANALOG);  // the same sensor's 0-2 V output (GPIO 34)
PvSdCard sd;
PvFlashRing flash_ring;           // LittleFS on the internal flash: the ring behind the card
PvDevice *const DEVICES[] = {&rtc, &env, &hub, &panel, &probes, &pyr, &pyr_analog, &sd, &flash_ring};

pvlog_t plog;                     // fail counters, events, time base (pv_log_logic.c)
pvstore_t store;                  // card first, LittleFS ring behind it (pv_store_logic.c)

// Where the program is, written before every call that talks to hardware. The three words
// live in RTC memory, which a watchdog or software reset does not clear (a power cut
// does), so the boot after a watchdog reset can say which call did not come back.
enum Stage : uint32_t { AT_SETUP, AT_IDLE, AT_RTC, AT_PYR20, AT_BME280, AT_PAHUB, AT_INA226,
                        AT_DS18B20, AT_DETECTORS, AT_STORE, AT_RTC_EDGE, AT_LOAD, AT_COUNT };
const char *const STAGE_NAME[AT_COUNT] = {"setup", "between rows", "DS3231", "PYR20", "BME280",
                                          "PaHub", "INA226", "DS18B20", "detectors",
                                          "card / LittleFS", "RTC second edge", "load"};
static const uint32_t CRUMB_MAGIC = 0x4E415349;  // "NASI": the words below were written by us
RTC_NOINIT_ATTR uint32_t crumb_magic;
RTC_NOINIT_ATTR uint32_t crumb_stage;
RTC_NOINIT_ATTR uint32_t crumb_ms;
void at(Stage s) {
  crumb_stage = s;
  crumb_ms = millis();
}

gbt_detector_t det;               // NASIM-GBT
thr_detector_t det_thr;           // baseline: temperature-rise threshold
cusum_inc_t det_cusum;            // baseline: CUSUM on the temperature increment
int det_doy = -1;                 // day the state of the three detectors belongs to
uint8_t det_fails = 0;            // rows skipped by the detector in a row

PvLoad load(panel);               // serviced only with HAS_LOAD 1
float last_g = NAN;               // irradiance of the last row: input of the load's light rules
double last_T[5] = {NAN, NAN, NAN, NAN, NAN};  // T1..T5 of the last row: stored with each sweep
uint32_t row_sweep_id = 0;        // sweep_id column: id of the last finished sweep, 0 = none
bool conv_running = false;        // a DS18B20 conversion was started ahead of the row ...
uint32_t conv_ms = 0;             // ... at this millis()
float heatsink_c = NAN;           // load heatsink probe, read with each row (NaN = no reading)
float row_d = NAN;                // duty of branch A at the row's reading (HAS_LOAD only)
bool load_on_marker = false;      // LOAD ON was typed: write its marker when the load holds

volatile uint32_t mark_ms = 0;    // last button press (ISR), 0 = none pending

// Millisecond UTC for marker rows. The DS3231 counts whole seconds, so the logger notes
// millis() at the moment the RTC second changes: UTC second anchor_s began at anchor_ms.
uint32_t anchor_s = 0;            // 0 = no anchor (RTC missing or not set)
uint32_t anchor_ms = 0;

// ---- helpers ------------------------------------------------------------------------------
void IRAM_ATTR onMarker() {
  static uint32_t last = 0;
  uint32_t now = millis();
  if (now - last > 200) mark_ms = now;  // 200 ms debounce
  last = now;
}

// Appends ",value" (or "," if NaN) to the row.
void addField(String &row, double v, int decimals) {
  row += ',';
  if (!isnan(v)) row += String(v, decimals);
}

bool romIsZero(const uint8_t *r) {
  for (int i = 0; i < 8; i++)
    if (r[i]) return false;
  return true;
}

// True if the device can be read now (used by the load code for the PYR20).
bool readable(const PvDevice &d) { return d.ok() || d.status() == PvStatus::ReadError; }

float heatsinkTemp();

// ---- the driver table of pv_log_logic.c: each function is one bounded driver call -------------
// Longest time each can take is listed in docs/paper/13-logger-robustness.md, section 2.
PvDevice *deviceOf(int dev) {
  switch (dev) {
    case PV_SENSOR_RTC: return &rtc;
    case PV_SENSOR_ENV: return &env;
    case PV_SENSOR_HUB: return &hub;
    case PV_SENSOR_INA: return &panel;
    case PV_SENSOR_PYR: return &pyr;
    default: return nullptr;
  }
}
uint32_t ioMillis(void *) { return millis(); }
int ioBegin(void *, int dev) {
  static const Stage stage[] = {AT_BME280, AT_RTC, AT_PAHUB, AT_INA226, AT_PYR20};
  if (dev >= PV_SENSOR_ENV && dev <= PV_SENSOR_PYR) at(stage[dev - PV_SENSOR_ENV]);
  PvDevice *d = deviceOf(dev);
  return d && d->begin();
}
const char *ioDetail(void *, int dev) {
  PvDevice *d = deviceOf(dev);
  return d ? d->detail() : "";
}
int ioRtcRead(void *, uint32_t *unix_s, int *time_valid) {
  at(AT_RTC);
  if (!rtc.read()) return 0;
  *unix_s = rtc.time().unixtime();
  *time_valid = rtc.timeValid;
  return 1;
}
int ioPyrRead(void *, float *g) {
  at(AT_PYR20);
  if (!pyr.read()) return 0;
  *g = pyr.irradiance;
  return 1;
}
int ioPyrAnalog(void *, float *mv) {
  pyr_analog.read();  // 64 ADC samples, a few ms; an ADC cannot "fail"
  *mv = (float)pyr_analog.millivolts;
  return 1;
}
int ioEnvRead(void *, float *t_c, float *rh) {
  at(AT_BME280);
  if (!env.read()) return 0;
  *t_c = env.temperatureC;
  *rh = env.humidityPct;
  return 1;
}
int ioHubCheck(void *) {
  at(AT_PAHUB);
  return hub.read();
}
int ioInaRead(void *, float *v, float *i, float *p) {
  at(AT_INA226);
  row_d = load.L.d_a;  // duty of branch A at the reading (HAS_LOAD only)
  // With the load: a 64-average triggered reading (also one hold step). Without: a plain read.
  if (!(HAS_LOAD ? load.rowReading() : panel.read())) return 0;
  *v = panel.busV;
  *i = panel.currentA;
  *p = panel.powerW;
  return 1;
}
int ioDsConvert(void *) {
  at(AT_DS18B20);
  if (HAS_LOAD && conv_running) {  // conversion started ~800 ms ago in loadLoop()
    while (millis() - conv_ms < PvDs18b20::CONVERSION_MS + 10) load.service(last_g, heatsinkTemp());
    conv_running = false;
    return 1;
  }
  return probes.convert();  // blocks until the probes are done, 750 ms at most
}
int ioDsRead(void *, const uint8_t *rom, float *t_c) {
  *t_c = probes.readRom(rom);
  return !isnan(*t_c);
}
int ioDsScan(void *, uint8_t *roms, int max) {
  at(AT_DS18B20);
  probes.begin();  // 1-Wire search of the whole bus
  const int n = min((int)probes.count, max);
  memcpy(roms, probes.rom, 8 * n);
  return n;
}
void ioLog(void *, const char *text) { Serial.printf("# %s\n", text); }
const pvlog_io_t PIO = {nullptr,    ioMillis,   ioBegin,     ioDetail, ioRtcRead,
                        ioPyrRead,  ioPyrAnalog, ioEnvRead,  ioHubCheck, ioInaRead,
                        ioDsConvert, ioDsRead,  ioDsScan,    ioLog};

// ---- the driver table of pv_store_logic.c: the card (never formatted) and the flash ring ------
int sioMount(void *) { return sd.begin(); }  // mounts only; two attempts 100 ms apart
int32_t sioSize(void *, const char *path) { return sd.fileSize(path); }
int sioAppend(void *, const char *path, const char *data, int n) { return sd.appendBytes(path, data, (size_t)n); }
int sioTail(void *, const char *path, char *buf, int n) { return sd.tail(path, buf, (size_t)n); }
int sioRange(void *, uint32_t *first, uint32_t *last) { return flash_ring.range(first, last); }
int32_t sioSegSize(void *, uint32_t seq) { return flash_ring.size(seq); }
int sioSegAppend(void *, uint32_t seq, const char *data, int n) { return flash_ring.append(seq, data, (size_t)n); }
int sioSegRead(void *, uint32_t seq, uint32_t off, char *buf, int n) { return flash_ring.readAt(seq, off, buf, (size_t)n); }
int32_t sioSegLines(void *, uint32_t seq, uint32_t off) {
  feedLoopWDT();  // called once per segment at boot: up to 128 files after a long outage
  return flash_ring.countLines(seq, off);
}
int sioSegRemove(void *, uint32_t seq) { return flash_ring.remove(seq); }
int sioSave(void *, const char *text) { return flash_ring.save(text); }
int sioLoad(void *, char *buf, int n) { return flash_ring.load(buf, (size_t)n); }
const pvstore_io_t SIO = {nullptr,      ioMillis,     sioMount,     sioSize,   sioAppend,
                          sioTail,      sioRange,     sioSegSize,   sioSegAppend, sioSegRead,
                          sioSegLines,  sioSegRemove, sioSave,      sioLoad,   ioLog};

// Column of a ROM: "T1".."T7", "heatsink", or "-" if no table lists it.
void romColumn(const uint8_t *rom, char *out, size_t size) {
  snprintf(out, size, "-");
  for (int k = 0; k < PVLOG_N_T; k++)
    if (memcmp(T_ROM[k], rom, 8) == 0) snprintf(out, size, "T%d", k + 1);
  if (!romIsZero(HEATSINK_ROM) && memcmp(HEATSINK_ROM, rom, 8) == 0) snprintf(out, size, "heatsink");
}

// One line of the boot ROM scan: to Serial as a '#' message and to /rom_scan.txt.
void scanLine(const char *line) {
  Serial.printf("# %s\n", line);
  if (sd.ok()) sd.appendLine("/rom_scan.txt", line);
}

// Every DS18B20 on the bus: index in the bus search, ROM, CRC, clone screening and the CSV
// column its ROM has in T_ROM. A ROM in no table is printed as a row to paste into T_ROM.
void reportRomScan() {
  char line[128], rom[24], when[40];  // when: 19 chars used, sized for the compiler's check
  if (rtc.ok() && rtc.read() && rtc.timeValid) {
    const DateTime t = rtc.time();
    snprintf(when, sizeof(when), "%04u-%02u-%02u %02u:%02u:%02u", t.year(), t.month(), t.day(),
             t.hour(), t.minute(), t.second());
  } else {
    snprintf(when, sizeof(when), "unknown (RTC)");
  }
  int filled = 0;
  for (int k = 0; k < PVLOG_N_T; k++) filled += !romIsZero(T_ROM[k]);
  snprintf(line, sizeof(line), "boot ROM scan, UTC %s: %u DS18B20 on the bus, T_ROM has %d of %d rows",
           when, probes.count, filled, PVLOG_N_T);
  scanLine(line);
  for (uint8_t i = 0; i < probes.count; i++) {
    char col[12];
    const uint8_t *r = probes.rom[i];
    romColumn(r, col, sizeof(col));
    PvDs18b20::romText(r, rom);
    snprintf(line, sizeof(line), "%u,%s,%s,%s,%s", i, rom,
             PvDs18b20::romCrcOk(r) ? "crc ok" : "CRC BAD",
             PvDs18b20::looksGenuine(r) ? "genuine pattern" : "clone?", col);
    scanLine(line);
    if (col[0] == '-') {  // not in T_ROM: not logged. Ready to paste:
      snprintf(line, sizeof(line),
               "  NOT in T_ROM -> {0x%02X, 0x%02X, 0x%02X, 0x%02X, 0x%02X, 0x%02X, 0x%02X, 0x%02X}",
               r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]);
      scanLine(line);
    }
  }
  for (int k = 0; k < PVLOG_N_T; k++) {
    if (romIsZero(T_ROM[k])) {
      snprintf(line, sizeof(line), "T%d: no ROM in T_ROM, the column stays empty", k + 1);
    } else if (probes.indexOf(T_ROM[k]) < 0) {
      snprintf(line, sizeof(line), "T%d: its T_ROM address is NOT on the bus", k + 1);
    } else {
      continue;
    }
    scanLine(line);
  }
}

int dayOfYear(const DateTime &t) {
  return (int)((t.unixtime() - DateTime(t.year(), 1, 1).unixtime()) / 86400UL) + 1;
}

void printStatus() {
  Serial.print("# status:");
  for (PvDevice *d : DEVICES) {
    Serial.printf(" %s=%s", d->name(), d->statusText());
    if (d->detail()[0]) Serial.printf(" (%s)", d->detail());
    Serial.print(';');
  }
  Serial.println();
}

// The three detectors always start, skip and reset together.
void resetDetectors() {
  gbt_reset(&det);
  thr_reset(&det_thr);
  cusum_inc_reset(&det_cusum);
}

// ---- one sample ---------------------------------------------------------------------------
// RTC, BME280 and PaHub share the main I2C bus. If all three are silent for several rows
// the bus itself may be stuck (a chip holding SDA low after a glitch): start the I2C
// driver again. [ASSUMPTION] that this frees such a bus; to be tried on the board.
void watchI2C(uint32_t sens_fail) {
  static uint8_t dead_rows = 0;
  const uint32_t all = (1u << PV_SENSOR_RTC) | (1u << PV_SENSOR_ENV) | (1u << PV_SENSOR_HUB);
  dead_rows = (sens_fail & all) == all ? dead_rows + 1 : 0;
  if (dead_rows < I2C_RESTART_AFTER) return;
  dead_rows = 0;
  Wire.end();
  Wire.begin(pins::I2C_SDA, pins::I2C_SCL, I2C_CLOCK_HZ);
  Wire.setTimeOut(I2C_TIMEOUT_MS);
  pvlog_event(&plog, "i2c_restart", "RTC; BME280 and PaHub all silent: I2C driver started again");
}

void sample(uint32_t n) {
  // Read order (pv_log_logic.c): the timestamp, then at once the irradiance (the fastest-
  // changing input, one short Modbus exchange), then the slower devices; the DS18B20
  // conversion (750 ms) comes last. A sensor that failed at the last row is started
  // again here, at this row. uptime_ms is taken with the timestamp.
  pvlog_row_t row;
  pvlog_read(&plog, &PIO, &row);
  if (HAS_LOAD) heatsink_c = romIsZero(HEATSINK_ROM) ? NAN : probes.readRom(HEATSINK_ROM);
  watchI2C(row.sens_fail);

  const bool have_time = row.have_time;
  const DateTime t(row.unix_s);                     // UTC: every logged timestamp
  const DateTime lt = t + TimeSpan(UTC_OFFSET_S);   // local time: NASIM inputs only
  double T[5];
  for (int k = 0; k < 5; k++) T[k] = row.t[k];
  const double ta = row.ta, g = row.g;
  last_g = row.g;
  for (int k = 0; k < 5; k++) last_T[k] = T[k];
  uint32_t status = row.status;  // the sensor bits; the detector bits are added below

  // Detectors: same input as the replay test; they need time, T1..T5, ambient, irradiance.
  at(AT_DETECTORS);
  double p = NAN;
  int alarm = 0;
  int minute = have_time ? lt.hour() * 60 + lt.minute() : -1;
  bool complete = have_time && !isnan(ta) && !isnan(g);
  for (int k = 0; k < 5; k++) complete = complete && !isnan(T[k]);
  bool in_window = minute >= NASIM_START_MIN && minute < NASIM_END_MIN;
  if (complete && in_window) {
    det_fails = 0;
    int doy = dayOfYear(lt);
    if (doy != det_doy) {  // a new day starts with empty buffers, as in training
      resetDetectors();
      det_doy = doy;
    }
    sample_t s;
    for (int k = 0; k < N_BACK; k++) s.t_back[k] = T[k];
    s.ta = ta;
    s.g = g;
    s.wind = NAN;
    s.doy = doy;
    s.minute = minute + lt.second() / 60.0;
    if (gbt_update(&det, &s)) alarm |= NASIM_ALARM_NASIM;
    if (thr_update(&det_thr, &s)) alarm |= NASIM_ALARM_THRESHOLD;
    if (cusum_inc_update(&det_cusum, &s)) alarm |= NASIM_ALARM_CUSUM;
    p = det.p;  // NAN while the 10-min buffers fill
  } else if (have_time && !in_window) {  // planned: the detector rests outside the window
    if (det_doy != -1) resetDetectors();
    det_doy = -1;
    det_fails = 0;
  } else {  // invalid row (input or time missing): skip it, keep the detector state
    status |= ST_DET_SKIPPED;
    if (det_fails < 255) det_fails++;
    if (det_fails == DET_RESET_AFTER_FAILS && det_doy != -1) {
      resetDetectors();  // too long a hole in the 10 s sequence the features assume
      det_doy = -1;
      status |= ST_DET_RESET;
      Serial.printf("# detector reset: %u rows in a row without valid inputs\n",
                    (unsigned)DET_RESET_AFTER_FAILS);
    }
  }

  // What the sensors did not measure: detector outputs and the load's columns.
  row.p = p;
  row.alarm = alarm;
#if HAS_ANEMOMETER
#error "HAS_ANEMOMETER 1: no anemometer driver exists yet (pv_log_logic.c writes wind = NaN)"
#endif
  if (HAS_LOAD) {  // D, load_mode, sweep_id; without a load they stay NaN, none, 0
    row.d = row_d;
    row.load_mode = load_mode_text(load.L.mode);
    row.sweep_id = row_sweep_id;
  }

  if (n && n % HEALTH_EVERY == 0) {  // once an hour: memory, so a slow loss shows in the log
    char text[112];
    flash_ring.read();
    snprintf(text, sizeof(text), "heap free %u B; lowest %u B; loop stack never used %u B; "
             "LittleFS %u of %u B used", (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMinFreeHeap(),
             (unsigned)uxTaskGetStackHighWaterMark(NULL), (unsigned)flash_ring.usedBytes,
             (unsigned)flash_ring.totalBytes);
    pvlog_event(&plog, "health", text);
  }

  // The row and this row's events go to the card, or wait in the LittleFS ring
  // (pvlog_commit, pv_log_logic.c). A row without RTC time goes to the file of the last
  // row that had one, or to /nodate.csv if there was none since boot.
  static int last_where = PVSTORE_SD;
  char line[PVLOG_LINE_MAX];
  row.status = status;
  at(AT_STORE);
  const int where = pvlog_commit(&plog, &store, &SIO, &row, line, sizeof(line));
  Serial.println(line);
  if (where != last_where || n == 0) printStatus();  // the card went, or came back
  last_where = where;
  at(AT_IDLE);
}

// Polls the RTC until its second changes and records that edge (blocks up to ~1.1 s).
// The edge is found to within one RTC read over I2C, a few ms [EST: check on the bench].
// Repeated every RESYNC_EVERY samples so the ESP32 crystal drift between edges stays small.
void syncRtcEdge() {
  anchor_s = 0;
  at(AT_RTC_EDGE);
  if (!rtc.ok() || !rtc.read() || !rtc.timeValid) return;
  const uint32_t s0 = rtc.time().unixtime();
  const uint32_t start = millis();
  while (millis() - start < 1100) {
    if (HAS_LOAD) load.service(last_g, heatsinkTemp());  // the hold loop goes on meanwhile
    if (!rtc.read()) return;
    const uint32_t s = rtc.time().unixtime();
    if (s != s0) {
      anchor_ms = millis();
      anchor_s = s;
      return;
    }
  }
}

// UTC of a millis() value, from the last RTC second edge. False if there is none.
// unix_s: whole UTC seconds (Unix time); milli: the milliseconds within that second.
bool utcOf(uint32_t ms, uint32_t &unix_s, unsigned &milli) {
  if (!anchor_s) return false;
  const int64_t utc_ms = (int64_t)anchor_s * 1000 + (int32_t)(ms - anchor_ms);
  unix_s = (uint32_t)(utc_ms / 1000);
  milli = (unsigned)(utc_ms % 1000);
  return true;
}

// One row in /markers.csv: UTC date, UTC time with milliseconds, and millis() of the event.
// what: nullptr = a button press; "load off" / "load on" = the load was switched by command
// (the name goes to Serial and to the day's log sheet; the file format is the same for all).
void writeMarker(uint32_t ms, const char *what) {
  char line[64];
  uint32_t unix_s;
  unsigned milli;
  if (utcOf(ms, unix_s, milli)) {
    const DateTime t(unix_s);
    snprintf(line, sizeof(line), "%04u-%02u-%02u,%02u:%02u:%02u.%03u,%lu", t.year(), t.month(),
             t.day(), t.hour(), t.minute(), t.second(), milli, (unsigned long)ms);
  } else {
    snprintf(line, sizeof(line), ",,%lu", (unsigned long)ms);
  }
  if (what) Serial.printf("# marker (%s): %s\n", what, line);
  else Serial.printf("# marker: %s\n", line);
  // through the store, like a row: a press while the card is away waits in the ring
  at(AT_STORE);
  pvstore_put(&store, &SIO, PVK_MARKER, MARKERS_PATH, line, 0);
  at(AT_IDLE);
}

void logMarker() {
  uint32_t ms = mark_ms;
  mark_ms = 0;
  writeMarker(ms, nullptr);
}

// ---- measurement load (called only with HAS_LOAD 1) -------------------------------------------
// NaN = no valid reading -> the logic trips. The probe is read by its ROM with every row.
float heatsinkTemp() { return heatsink_c; }

// Mode changes, trips, commands: to Serial and to /load_log.csv.
void loadLog(const char *text) {
  char line[176];
  uint32_t unix_s;
  unsigned milli;
  const uint32_t ms = millis();
  if (utcOf(ms, unix_s, milli)) {
    const DateTime t(unix_s);
    snprintf(line, sizeof(line), "%04u-%02u-%02u,%02u:%02u:%02u,%lu,%s", t.year(), t.month(),
             t.day(), t.hour(), t.minute(), t.second(), (unsigned long)ms, text);
  } else {
    snprintf(line, sizeof(line), ",,%lu,%s", (unsigned long)ms, text);
  }
  Serial.printf("# load: %s\n", line);
  if (sd.ok()) sd.appendLine("/load_log.csv", line, "date,time,uptime_ms,event");
}

// Closes the finished sweep and appends it to the card: every point to /SWYYYYMMDD.csv and
// one summary line to /SSYYYYMMDD.csv (UTC date of the sweep). Raw values; no fit here.
// sweep_id is the UTC time of the sweep start in Unix seconds: unique over days and over
// restarts without any counter kept on the card. Without RTC time it is the count since
// boot and the files are /SWnodate.csv and /SSnodate.csv.
void storeSweep() {
  if (readable(pyr)) pyr.read();
  const uint32_t flags = load_sweep_close(&load.L, pyr.ok() ? pyr.irradiance : NAN);
  const load_sweep_t &sw = load.L.sweep;
  // buffers sized for the widest values the compiler can see, not for the 14 / 19 chars used
  char points_path[40] = "/SWnodate.csv", summary_path[40] = "/SSnodate.csv", when[48] = ",";
  uint32_t id = sw.id, unix_s;
  unsigned milli;
  if (utcOf(sw.t_start_ms, unix_s, milli)) {
    const DateTime t(unix_s);
    id = unix_s;
    snprintf(points_path, sizeof(points_path), "/SW%04u%02u%02u.csv", t.year(), t.month(), t.day());
    snprintf(summary_path, sizeof(summary_path), "/SS%04u%02u%02u.csv", t.year(), t.month(), t.day());
    snprintf(when, sizeof(when), "%04u-%02u-%02u,%02u:%02u:%02u", t.year(), t.month(), t.day(),
             t.hour(), t.minute(), t.second());
  }
  row_sweep_id = id;

  String points;  // all points in one write: the file is opened once
  points.reserve(sw.n * 40);
  for (uint32_t k = 0; k < sw.n; k++) {
    char line[48];
    snprintf(line, sizeof(line), "%s%lu,%lu,%.5f,%.4f,%.4f", k ? "\n" : "", (unsigned long)id,
             (unsigned long)k, sw.d[k], sw.v[k], sw.i[k]);
    points += line;
  }
  String sum;
  sum.reserve(160);
  sum += id;
  sum += ',';
  sum += when;
  sum += ',';
  sum += sw.t_start_ms;
  for (int k = 0; k < 5; k++) addField(sum, last_T[k], 4);
  addField(sum, sw.g_before, 0);
  addField(sum, sw.g_after, 0);
  addField(sum, sw.v_oc, 4);
  addField(sum, sw.n ? sw.v[sw.best] : NAN, 4);
  addField(sum, sw.n ? sw.i[sw.best] : NAN, 4);
  addField(sum, sw.n ? sw.d[sw.best] : NAN, 5);
  sum += ',';
  sum += flags;
  const bool saved =
      sd.ok() && (sw.n == 0 || sd.appendLine(points_path, points.c_str(), "sweep_id,k,D,V,I")) &&
      sd.appendLine(summary_path, sum.c_str(),
                    "sweep_id,date,time,uptime_ms,T1,T2,T3,T4,T5,G_before,G_after,V_oc,V_best,"
                    "I_best,D_best,flags");
  Serial.printf("# sweep%s: %s (%lu ms)\n", saved ? "" : " NOT SAVED", sum.c_str(),
                (unsigned long)(sw.t_end_ms - sw.t_start_ms));
}

void loadCommand(const char *c) {
  if (!strncmp(c, "GAMMA", 5)) {  // GAMMA <minutes>: a sweep every 15 s; GAMMA 0 ends it
    const long minutes = constrain(atol(c + 5), 0L, 120L);
    load_gamma(&load.L, millis(), (uint32_t)minutes);
    char text[40];
    snprintf(text, sizeof(text), "gamma run: %ld min", minutes);
    loadLog(text);
  } else if (!strcmp(c, "LOAD OFF")) {  // controlled test: open circuit until LOAD ON
    const bool was_holding = load.L.mode == LOAD_HOLD;
    load_manual_off(&load.L, millis(), 1);
    load.service(last_g, heatsinkTemp());  // both duties to 0 on the pins now
    const uint32_t ms = millis();
    load_on_marker = false;
    if (was_holding) writeMarker(ms, "load off");
    loadLog(was_holding ? "command LOAD OFF" : "command LOAD OFF (load was not holding: no marker)");
  } else if (!strcmp(c, "LOAD ON")) {
    load_on_marker = load.L.manual_off;  // its marker is written when the load holds again
    load_manual_off(&load.L, millis(), 0);
    loadLog("command LOAD ON");
  } else {
    Serial.println("# commands: GAMMA <minutes> | LOAD OFF | LOAD ON");
  }
}

void readCommands() {
  static char buf[24];
  static uint8_t n = 0;
  while (Serial.available()) {
    const char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      buf[n] = 0;
      if (n) loadCommand(buf);
      n = 0;
    } else if (n < sizeof(buf) - 1) {
      buf[n++] = (char)toupper(c);
    }
  }
}

// Reports what changed in the load since the last call.
void noteLoad() {
  static uint32_t mode = LOAD_OFF, frozen = 0, gamma = 0;
  const load_t &L = load.L;
  char text[112];
  if (L.mode != mode) {
    if (L.mode == LOAD_TRIP) {
      snprintf(text, sizeof(text), "TRIP: %s (heatsink %.1f C; last I %.2f A)",
               load_trip_text(L.trip_reason), heatsinkTemp(), L.i_last);
    } else {
      snprintf(text, sizeof(text), "%s -> %s", load_mode_text(mode), load_mode_text(L.mode));
    }
    loadLog(text);
  }
  if (L.ina_frozen != frozen) {
    loadLog(L.ina_frozen ? "INA226 readings failing: duty frozen, sweeps suspended"
                         : "INA226 readings back");
  }
  if (gamma && !L.gamma_active) loadLog("gamma run ended");
  if (load_on_marker && L.mode == LOAD_HOLD) {
    writeMarker(millis(), "load on");
    load_on_marker = false;
  }
  mode = L.mode;
  frozen = L.ina_frozen;
  gamma = L.gamma_active;
}

// One pass of the load: commands, the logic (hold step, faults), a sweep if one is due and
// fits before the next row, and the DS18B20 conversion started 800 ms ahead of the row.
void loadLoop(uint32_t next_row_ms) {
  readCommands();
  load.service(last_g, heatsinkTemp());
  noteLoad();
  int32_t to_row = (int32_t)(next_row_ms - millis());
  if (to_row > 0 && load_sweep_due(&load.L, millis(), (uint32_t)to_row)) {
    if (readable(pyr)) pyr.read();  // G just before the sweep
    const float g_before = pyr.ok() ? pyr.irradiance : NAN;
    to_row = (int32_t)(next_row_ms - millis());
    if (to_row > 0 && load.sweepStart((uint32_t)to_row, g_before)) {
      load.finishSweep();  // blocks ~1.7 s; the marker button is an interrupt
      if (load.L.sweep_done) storeSweep();
      noteLoad();
    }
  }
  to_row = (int32_t)(next_row_ms - millis());
  if (!conv_running && to_row <= 800 && probes.count) {
    conv_running = probes.startConversion();
    conv_ms = millis();
  }
}

// ---- Arduino ---------------------------------------------------------------------------------
// FIRST ACTION of setup(): the pump gate (GPIO 32) LOW, then both load gates. The LOW level
// is written before each pin becomes an output (pvOutputsOff, PvDaq.h). Nothing may be
// placed above that call; tests/test_load_logic.py checks that it is the first statement.
const char *resetText(esp_reset_reason_t why) {
  switch (why) {
    case ESP_RST_POWERON: return "POWERON";
    case ESP_RST_EXT: return "EXT_PIN";
    case ESP_RST_SW: return "SOFTWARE";
    case ESP_RST_PANIC: return "PANIC";
    case ESP_RST_INT_WDT: return "INT_WDT";
    case ESP_RST_TASK_WDT: return "TASK_WDT";
    case ESP_RST_WDT: return "OTHER_WDT";
    case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
    case ESP_RST_BROWNOUT: return "BROWNOUT";
    default: return "OTHER";
  }
}

// The first event of every boot: why the chip started and, after a reset that leaves RTC
// memory alone (watchdog, panic, software), the last stage the loop had reached.
void bootEvent() {
  const esp_reset_reason_t why = esp_reset_reason();
  char text[120];
  const bool crumb = crumb_magic == CRUMB_MAGIC && crumb_stage < AT_COUNT &&
                     why != ESP_RST_POWERON && why != ESP_RST_BROWNOUT;
  if (crumb) {
    snprintf(text, sizeof(text), "reset=%s (%d); before it: %s at uptime %lu ms; heap %u B",
             resetText(why), (int)why, STAGE_NAME[crumb_stage], (unsigned long)crumb_ms,
             (unsigned)ESP.getFreeHeap());
  } else {
    snprintf(text, sizeof(text), "reset=%s (%d); heap %u B", resetText(why), (int)why,
             (unsigned)ESP.getFreeHeap());
  }
  pvlog_event(&plog, "boot", text);
  crumb_magic = CRUMB_MAGIC;
  at(AT_SETUP);
}

void setup() {
  pvOutputsOff();
  Serial.begin(115200);
  delay(300);
  // The watchdog on this task (the Arduino loop task), from here on. setup() feeds it
  // between its slow steps; afterwards the Arduino core feeds it each time loop() returns.
  esp_task_wdt_config_t wdt = {LOOP_WDT_MS, 0, true};  // time-out, no idle task, reset
  if (esp_task_wdt_reconfigure(&wdt) != ESP_OK) esp_task_wdt_init(&wdt);
  enableLoopWDT();
  pinMode(pins::MARKER_BUTTON, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(pins::MARKER_BUTTON), onMarker, FALLING);
  Wire.begin(pins::I2C_SDA, pins::I2C_SCL, I2C_CLOCK_HZ);
  Wire.setTimeOut(I2C_TIMEOUT_MS);  // no I2C transaction may take longer than this
  pvlog_cfg_t cfg = {};
  memcpy(cfg.t_rom, T_ROM, sizeof(cfg.t_rom));
  memcpy(cfg.known_rom[0], HEATSINK_ROM, 8);  // belongs on the bus, is not a column
  cfg.fail_report_after = FAIL_REPORT_AFTER;
  cfg.scan_every = ROM_SCAN_EVERY;
  pvlog_init(&plog, &cfg);
  bootEvent();
  gbt_init(&det, nasim_score_nowind, /*use_wind=*/0, /*hybrid=*/1, NASIM_P_THRESHOLD,
           NASIM_PERSIST);
  thr_init(&det_thr, THR_RISE_C);
  cusum_inc_init(&det_cusum, CUSUM_KAPPA_C, CUSUM_H_C);
  Serial.println("# PV Phase 0 logger. NASIM-GBT (no wind, double) trained on SYNTHETIC data.");
  Serial.printf("# alarm = %d NASIM-GBT (p >= %.3f, %d rows) + %d threshold (rise >= %.2f C in "
                "10 min) + %d CUSUM (kappa %.3f C, h %.2f C); settings from SYNTHETIC data\n",
                NASIM_ALARM_NASIM, (double)NASIM_P_THRESHOLD, NASIM_PERSIST,
                NASIM_ALARM_THRESHOLD, (double)THR_RISE_C, NASIM_ALARM_CUSUM,
                (double)CUSUM_KAPPA_C, (double)CUSUM_H_C);
  Serial.println("# timestamps are UTC (DS3231)");
  // A first begin() of everything for the boot report. Whatever is missing now is started
  // again by the first row, and by every row after it, until it answers.
  for (PvDevice *d : DEVICES) {
    feedLoopWDT();
    if (d != &panel && d != &sd) d->begin();  // the flash ring too (formats itself once)
  }
  if (hub.ok()) panel.begin();  // the INA226 needs the PaHub
  feedLoopWDT();
  if (flash_ring.formatted) pvlog_event(&plog, "ring_formatted", "internal flash had no LittleFS: formatted (first use)");
  // The store finds what an earlier run left in the ring and mounts the card (no format).
  pvstore_init(&store, pvlog_file_head(), PVLOG_EVENTS_HEADER, MARKERS_HEADER);
  pvstore_begin(&store, &SIO);
  feedLoopWDT();
  reportRomScan();
  feedLoopWDT();
  if (HAS_LOAD) {
    // a first heatsink temperature before the load may start (NaN keeps it in TRIP)
    if (!romIsZero(HEATSINK_ROM)) probes.convert();
    heatsink_c = romIsZero(HEATSINK_ROM) ? NAN : probes.readRom(HEATSINK_ROM);
    if (!load.begin()) Serial.println("# load: LEDC attach FAILED: both branches are held off");
    Serial.println("# load: active. Commands: GAMMA <minutes> | LOAD OFF | LOAD ON");
  }
  Serial.printf("# watchdog on the loop task: %lu ms; LittleFS ring: %s, %lu lines waiting\n",
                (unsigned long)LOOP_WDT_MS, flash_ring.ok() ? "ready" : "NOT USABLE",
                (unsigned long)pvstore_waiting(&store));
  Serial.println(PVLOG_VERSION_LINE);
  Serial.println(pvlog_header());
  at(AT_IDLE);
}

void loop() {
  static uint32_t next = millis();
  static uint32_t n = 0;
  if (mark_ms) logMarker();
  if (HAS_LOAD) at(AT_LOAD);
  if (HAS_LOAD) loadLoop(next);
  if ((int32_t)(millis() - next) < 0) return;
  next += SAMPLE_PERIOD_MS;                 // fixed grid: no drift from the sampling time
  if ((int32_t)(millis() - next) >= 0) {    // more than one period late: skip, don't burst
    Serial.println("# warning: sample period overrun, schedule resynchronised");
    next = millis() + SAMPLE_PERIOD_MS;
  }
  sample(n);
  if (n % RESYNC_EVERY == 0 || !anchor_s) syncRtcEdge();  // after the row: may block ~1 s
  at(AT_IDLE);
  n++;
}
