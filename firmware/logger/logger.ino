// Phase 0 data logger. Every 10 s (millis schedule, no drift): read every sensor that
// is present, skip the missing ones, and write one CSV row to the microSD card and to
// Serial. Columns: the schema of CLAUDE.md + wind (data/schema.py COLUMNS), then p (the
// NASIM-GBT probability; no wind, double precision) and alarm, then five columns:
// D, load_mode, sweep_id (NaN, none, 0 while HAS_LOAD is 0: no load fitted), uptime_ms, status.
// alarm holds one bit per on-board detector (data/schema.py ALARM_BITS): 1 = NASIM-GBT,
// 2 = threshold rule, 4 = CUSUM; values add up, 0 = no alarm. All three see the same rows.
//
//   date,time,T1,T2,T3,T4,T5,T_ambient,humidity,irradiance,voltage,current,power,wind,p,alarm,
//   D,load_mode,sweep_id,uptime_ms,status
//
// Timestamps are UTC (the DS3231 holds UTC). Missing value = empty field. Lines starting
// with '#' on Serial are status messages, not data. Files: /YYYYMMDD.csv per day (UTC
// date), /markers.csv for button presses, /rom_scan.txt for the DS18B20 ROMs found at boot.
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
#include <esp_task_wdt.h>
#include <pv_load.h>
#include <pv_status.h>

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
static const uint8_t RETRY_EVERY = 6;         // retry missing devices every 6 samples (60 s)
static const uint8_t RESYNC_EVERY = 6;        // re-find the RTC second edge every 60 s
// A row with a missing detector input is skipped by the detector, which keeps its state.
// Only this many skipped rows in a row clear the state (the 10 s spacing the features
// assume is then too far off). The value is config.py DET_RESET_AFTER_FAILS, through the
// generated nasim_config.h: the offline analysis (analysis/ingest.py) uses the same one.
static const uint8_t DET_RESET_AFTER_FAILS = NASIM_DET_RESET_AFTER_FAILS;

// Back-sheet sensors T1..T5 by ROM (row 0 = T1 ... row 4 = T5).
// TODO(user): fill in all five rows after the water-bath cross-calibration and mounting.
// The ROMs are printed on Serial at every boot and appended to /rom_scan.txt on the card,
// e.g. 28-FF-64-1E-0F-00-00-A1 -> {0x28, 0xFF, 0x64, 0x1E, 0x0F, 0x00, 0x00, 0xA1}.
// Until ALL five rows are filled, T1..T5 take the sensors in bus-scan order (not tied to a
// position on the module) and every row carries the ST_TROM_UNMAPPED status bit.
static const uint8_t T_ROM[5][8] = {{0}, {0}, {0}, {0}, {0}};

// Measurement load (used only with HAS_LOAD 1). DS18B20 on the resistor heatsink, by ROM,
// same notation as T_ROM. TODO(user): fill in when the sensor is mounted (bench B5 prints
// it). While it is all zero the load stays in TRIP ("heatsink sensor missing"): both
// branches off, by design.
static const uint8_t HEATSINK_ROM[8] = {0};
// Loop watchdog: if loop() does not come round for this long the chip resets, the pins are
// released and the gate pull-downs switch the load off. [ASSUMPTION] well above the longest
// normal pass (device retries + a row + the RTC edge, a few seconds); check in bench B7.
static const uint32_t LOOP_WDT_MS = 15000;

// ---- devices -----------------------------------------------------------------------------
PvDs3231 rtc;
PvBme280 env;
PvPaHub hub;
PvIna226 panel("INA226 panel", &hub, PAHUB_CH_PANEL);
PvDs18b20 probes(pins::ONEWIRE);
PvPyr20Modbus pyr(Serial2, pins::RS485_RX, pins::RS485_TX);
PvSdCard sd;
PvDevice *const DEVICES[] = {&rtc, &env, &hub, &panel, &probes, &pyr, &sd};

gbt_detector_t det;               // NASIM-GBT
thr_detector_t det_thr;           // baseline: temperature-rise threshold
cusum_inc_t det_cusum;            // baseline: CUSUM on the temperature increment
int det_doy = -1;                 // day the state of the three detectors belongs to
uint8_t det_fails = 0;            // rows skipped by the detector in a row

uint8_t t_map[5][8] = {{0}};      // ROM behind each of T1..T5 in use (all zero = none yet)
bool t_rom_unmapped = true;       // true while t_map comes from the scan order, not T_ROM
char day_path[16] = "/nodate.csv";  // file of the last row that had a valid RTC time

PvLoad load(panel);               // serviced only with HAS_LOAD 1
float last_g = NAN;               // irradiance of the last row: input of the load's light rules
double last_T[5] = {NAN, NAN, NAN, NAN, NAN};  // T1..T5 of the last row: stored with each sweep
uint32_t row_sweep_id = 0;        // sweep_id column: id of the last finished sweep, 0 = none
bool conv_running = false;        // a DS18B20 conversion was started ahead of the row ...
uint32_t conv_ms = 0;             // ... at this millis()
bool load_on_marker = false;      // LOAD ON was typed: write its marker when the load holds

volatile uint32_t mark_ms = 0;    // last button press (ISR), 0 = none pending

// Millisecond UTC for marker rows. The DS3231 counts whole seconds, so the logger notes
// millis() at the moment the RTC second changes: UTC second anchor_s began at anchor_ms.
uint32_t anchor_s = 0;            // 0 = no anchor (RTC missing or not set)
uint32_t anchor_ms = 0;
const char *HEADER =
    "date,time,T1,T2,T3,T4,T5,T_ambient,humidity,irradiance,voltage,current,power,wind,p,alarm,"
    "D,load_mode,sweep_id,uptime_ms,status";

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

// True if the device can be read now. A device whose last read failed is read again at
// the next row (not only after the 60 s re-begin), so one bad read costs one row.
bool readable(const PvDevice &d) { return d.ok() || d.status() == PvStatus::ReadError; }

// Decides which ROM is behind each of T1..T5. With T_ROM filled in: exactly T_ROM.
// Otherwise by scan order, and the choice is then KEPT: a column that has a ROM never
// changes it, so a sensor that drops off the bus leaves its own column empty instead of
// shifting the others. Columns still free take the next ROM not used yet.
void mapProbes() {
  t_rom_unmapped = false;
  for (int k = 0; k < 5; k++) t_rom_unmapped |= romIsZero(T_ROM[k]);
  if (!t_rom_unmapped) {
    memcpy(t_map, T_ROM, sizeof(t_map));
    return;
  }
  for (uint8_t i = 0; i < probes.count; i++) {
    if (memcmp(probes.rom[i], HEATSINK_ROM, 8) == 0) continue;  // not a back-sheet sensor
    int free_k = -1;
    bool used = false;
    for (int k = 4; k >= 0; k--) {
      if (memcmp(t_map[k], probes.rom[i], 8) == 0) used = true;
      else if (romIsZero(t_map[k])) free_k = k;
    }
    if (!used && free_k >= 0) memcpy(t_map[free_k], probes.rom[i], 8);
  }
}

double backTemp(int k) {  // T1..T5 -> k = 0..4
  if (!readable(probes)) return NAN;
  int idx = probes.indexOf(t_map[k]);  // an all-zero row matches no sensor
  return idx < 0 ? NAN : probes.tempC[idx];
}

// One line of the boot ROM scan: to Serial as a '#' message and to /rom_scan.txt.
void scanLine(const char *line) {
  Serial.printf("# %s\n", line);
  if (sd.ok()) sd.appendLine("/rom_scan.txt", line);
}

// Every DS18B20 on the bus: index in scan order, ROM, CRC, clone screening, CSV column.
void reportRomScan() {
  char line[128], rom[24], when[40];  // when: 19 chars used, sized for the compiler's check
  if (rtc.ok() && rtc.read() && rtc.timeValid) {
    const DateTime t = rtc.time();
    snprintf(when, sizeof(when), "%04u-%02u-%02u %02u:%02u:%02u", t.year(), t.month(), t.day(),
             t.hour(), t.minute(), t.second());
  } else {
    snprintf(when, sizeof(when), "unknown (RTC)");
  }
  snprintf(line, sizeof(line), "boot ROM scan, UTC %s: %u DS18B20, T_ROM %s", when,
           probes.count, t_rom_unmapped ? "NOT filled in -> T1..T5 by scan order" : "used");
  scanLine(line);
  for (uint8_t i = 0; i < probes.count; i++) {
    char col[12] = "-";  // "T1".."T5"
    for (int k = 0; k < 5; k++)
      if (memcmp(t_map[k], probes.rom[i], 8) == 0) snprintf(col, sizeof(col), "T%d", k + 1);
    PvDs18b20::romText(probes.rom[i], rom);
    snprintf(line, sizeof(line), "%u,%s,%s,%s,%s", i, rom,
             PvDs18b20::romCrcOk(probes.rom[i]) ? "crc ok" : "CRC BAD",
             PvDs18b20::looksGenuine(probes.rom[i]) ? "genuine pattern" : "clone?", col);
    scanLine(line);
  }
  for (int k = 0; k < 5; k++) {
    if (probes.indexOf(t_map[k]) >= 0) continue;
    snprintf(line, sizeof(line), "T%d: %s", k + 1,
             t_rom_unmapped ? "no sensor for this column" : "its T_ROM address is NOT on the bus");
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
void sample(uint32_t n) {
  uint32_t status = 0;
  if (n % RETRY_EVERY == 0) {
    bool retried = false;
    for (PvDevice *d : DEVICES) {
      if (!d->ok() && d != &panel) retried |= d->begin();
    }
    if (!panel.ok() && hub.ok()) retried |= panel.begin();  // the INA226 needs the PaHub
    mapProbes();  // a re-scan may have found a sensor for a column that had none
    if (retried || n == 0) printStatus();
  }

  // Read order: the timestamp, then at once the irradiance (the fastest-changing input,
  // one short Modbus exchange), then the slower devices; the DS18B20 conversion (750 ms)
  // comes last. uptime_ms is taken with the timestamp, after any device retry above.
  const uint32_t uptime_ms = millis();  // logged: orders the rows even without the RTC
  if (readable(rtc)) rtc.read();
  if (readable(pyr)) pyr.read();
  if (readable(env)) env.read();
  const float row_d = load.L.d_a;  // duty of branch A at the reading (HAS_LOAD only)
  if (HAS_LOAD) load.rowReading();   // 64-average, triggered (also one hold step)
  else if (readable(panel)) panel.read();
  if (HAS_LOAD && conv_running) {    // conversion started ~800 ms ago in loadLoop()
    while (millis() - conv_ms < PvDs18b20::CONVERSION_MS + 10) load.service(last_g, heatsinkTemp());
    probes.fetch();
    conv_running = false;
  } else if (probes.count) {
    probes.read();
  }

  const bool have_time = rtc.ok() && rtc.timeValid;
  const DateTime t = rtc.time();                    // UTC: every logged timestamp
  const DateTime lt = t + TimeSpan(UTC_OFFSET_S);   // local time: NASIM inputs only
  double T[5];
  for (int k = 0; k < 5; k++) T[k] = backTemp(k);
  double ta = env.ok() ? env.temperatureC : NAN;
  double rh = env.ok() ? env.humidityPct : NAN;
  double g = pyr.ok() ? pyr.irradiance : NAN;
  double v = panel.ok() ? panel.busV : NAN;
  double i = panel.ok() ? panel.currentA : NAN;
  double p_el = panel.ok() ? panel.powerW : NAN;
  last_g = g;
  for (int k = 0; k < 5; k++) last_T[k] = T[k];

  // Detectors: same input as the replay test; they need time, T1..T5, ambient, irradiance.
  double p = NAN;
  int alarm = 0;
  int minute = have_time ? lt.hour() * 60 + lt.minute() : -1;
  bool complete = have_time && !isnan(ta) && !isnan(g);
  for (int k = 0; k < 5; k++) complete = complete && !isnan(T[k]);
  bool in_window = minute >= NASIM_START_MIN && minute < NASIM_END_MIN;
  if (!have_time) status |= ST_RTC_INVALID;
  if (isnan(ta)) status |= ST_AMBIENT_FAIL;
  if (isnan(g)) status |= ST_IRRAD_FAIL;
  if (isnan(v)) status |= ST_PANEL_FAIL;
  for (int k = 0; k < 5; k++)
    if (isnan(T[k])) status |= ST_TEMP_FAIL;
  if (t_rom_unmapped) status |= ST_TROM_UNMAPPED;
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

  // CSV row
  String row;
  row.reserve(220);
  if (have_time) {
    char dt[40];  // 19 chars used; sized for the widest values the compiler can see
    snprintf(dt, sizeof(dt), "%04u-%02u-%02u,%02u:%02u:%02u", t.year(), t.month(), t.day(),
             t.hour(), t.minute(), t.second());
    row += dt;
  } else {
    row += ',';
  }
  for (int k = 0; k < 5; k++) addField(row, T[k], 4);
  addField(row, ta, 2);
  addField(row, rh, 2);
  addField(row, g, 0);
  addField(row, v, 4);
  addField(row, i, 4);
  addField(row, p_el, 4);
#if HAS_ANEMOMETER
#error "HAS_ANEMOMETER 1: no anemometer driver exists yet"
#else
  row += ",NaN";  // no anemometer in this phase: "NaN" = not measured (empty = read failed)
#endif
  addField(row, p, 6);
  row += ',';
  row += alarm;
  if (HAS_LOAD) {  // D, load_mode, sweep_id
    row += ',';
    row += String(row_d, 4);
    row += ',';
    row += load_mode_text(load.L.mode);
    row += ',';
    row += row_sweep_id;
    row += ',';
  } else {
    row += ",NaN,none,0,";  // no load fitted
  }
  row += uptime_ms;
  row += ',';

  // A row without RTC time goes to the file of the last row that had one (its neighbours
  // and uptime_ms give its time back), or to /nodate.csv if there was none since boot.
  if (have_time) {
    snprintf(day_path, sizeof(day_path), "/%04u%02u%02u.csv", t.year(), t.month(), t.day());
  }
  // status is the last field, so a second write attempt can be flagged in the row itself.
  static bool sd_lost = false;  // the row before this one did not reach the card
  if (sd_lost) status |= ST_SD_RETRY;
  const int attempts = sd.ok() ? 2 : 1;  // a card that was working gets one re-mount + retry
  bool written = false;
  String line;
  for (int a = 0; a < attempts && !written; a++) {
    if (a > 0) status |= ST_SD_RETRY;
    line = row + String(status);
    written = (a == 0 ? (sd.ok() || sd.begin()) : sd.begin()) &&
              sd.appendLine(day_path, line.c_str(), HEADER);
  }
  if (!written && !sd_lost) printStatus();
  sd_lost = !written;
  Serial.println(line);
}

// Polls the RTC until its second changes and records that edge (blocks up to ~1.1 s).
// The edge is found to within one RTC read over I2C, a few ms [EST: check on the bench].
// Repeated every RESYNC_EVERY samples so the ESP32 crystal drift between edges stays small.
void syncRtcEdge() {
  anchor_s = 0;
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
  if (sd.ok()) sd.appendLine("/markers.csv", line, "date,time,millis");
}

void logMarker() {
  uint32_t ms = mark_ms;
  mark_ms = 0;
  writeMarker(ms, nullptr);
}

// ---- measurement load (called only with HAS_LOAD 1) -------------------------------------------
float heatsinkTemp() {  // NaN = no valid reading -> the logic trips
  if (!readable(probes)) return NAN;
  const int idx = probes.indexOf(HEATSINK_ROM);  // an all-zero ROM matches no sensor
  return idx < 0 ? NAN : probes.tempC[idx];
}

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
void setup() {
  pvOutputsOff();
  Serial.begin(115200);
  delay(300);
  pinMode(pins::MARKER_BUTTON, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(pins::MARKER_BUTTON), onMarker, FALLING);
  Wire.begin(pins::I2C_SDA, pins::I2C_SCL, I2C_CLOCK_HZ);
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
  for (PvDevice *d : DEVICES) {
    if (d != &panel) d->begin();
  }
  if (hub.ok()) panel.begin();  // the INA226 needs the PaHub
  mapProbes();
  reportRomScan();
  if (HAS_LOAD) {
    if (probes.count) probes.read();  // a first heatsink temperature before the load may start
    if (!load.begin()) Serial.println("# load: LEDC attach FAILED: both branches are held off");
    esp_task_wdt_config_t wdt = {LOOP_WDT_MS, 0, true};  // time-out, no idle task, reset
    if (esp_task_wdt_reconfigure(&wdt) != ESP_OK) esp_task_wdt_init(&wdt);
    enableLoopWDT();
    Serial.println("# load: active. Commands: GAMMA <minutes> | LOAD OFF | LOAD ON");
  }
  Serial.println(HEADER);
}

void loop() {
  static uint32_t next = millis();
  static uint32_t n = 0;
  if (mark_ms) logMarker();
  if (HAS_LOAD) loadLoop(next);
  if ((int32_t)(millis() - next) < 0) return;
  next += SAMPLE_PERIOD_MS;                 // fixed grid: no drift from the sampling time
  if ((int32_t)(millis() - next) >= 0) {    // more than one period late: skip, don't burst
    Serial.println("# warning: sample period overrun, schedule resynchronised");
    next = millis() + SAMPLE_PERIOD_MS;
  }
  sample(n);
  if (n % RESYNC_EVERY == 0 || !anchor_s) syncRtcEdge();  // after the row: may block ~1 s
  n++;
}
