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
// SD card out or failing: the rows wait in the ESP32's own flash (PvDaq/src/pv_flashring.h,
// status bit ST_BUFFERED) and are copied to their day files, in order, when the card is back.
// Watchdog: in every build the chip resets if loop() stops coming round (LOOP_WDT_MS); the
// reason of every start is printed and appended to /boot_log.csv.
// Build: python firmware/build_all.py logger   (adds the include paths to export/c).
//
// With the pump (ENABLE_PUMP 1 in pv_config.h; PvDaq/src/pv_pump.h): the serial commands
// PUMP <percent> | PUMP OFF | PUMP RESET drive it for the bucket test, with soft start and
// the over-current / dry-run cut-off from the pump's INA226. /pump_log.csv = starts, stops
// and trips; /PPYYYYMMDD.csv = one row per 10 s row in which the pump ran.
//
// SAFETY: the pump output is held LOW while ENABLE_PUMP is 0; with ENABLE_PUMP 1 it starts
// LOW, only a typed command turns it on, and nothing in this sketch pumps by itself (no
// alarm, no temperature). Both load outputs are held LOW while HAS_LOAD is 0;
// with HAS_LOAD 1 they start LOW and go LOW again on every fault (PvDaq/src/pv_load_logic.h).
// The alarms are only logged; NASIM is trained and the baselines are tuned on SYNTHETIC
// data: all must be redone on real data before they drive anything. The load never acts
// on an alarm.
#include <Preferences.h>
#include <PvDaq.h>
#include <esp_system.h>
#include <esp_task_wdt.h>
#include <pv_flashring.h>
#include <pv_load.h>
#include <pv_pump.h>
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
static const uint8_t RETRY_EVERY = 6;         // slow devices (1-Wire, PYR20, SD): retry every 60 s
static const uint8_t RESYNC_EVERY = 6;        // re-find the RTC second edge every 60 s
// A row with a missing detector input is skipped by the detector, which keeps its state.
// Only this many skipped rows in a row clear the state (the 10 s spacing the features
// assume is then too far off). The value is config.py DET_RESET_AFTER_FAILS, through the
// generated nasim_config.h: the offline analysis (analysis/ingest.py) uses the same one.
static const uint8_t DET_RESET_AFTER_FAILS = NASIM_DET_RESET_AFTER_FAILS;

// Back-sheet sensors T1..T5 by ROM (row 0 = T1 ... row 4 = T5). Three sources, in this order:
//  1. this table, when ALL five rows are filled in (the permanent record: it is in git).
//     TODO(user): fill in after the water-bath cross-calibration and mounting. The ROMs are
//     printed on Serial at every boot and appended to /rom_scan.txt on the card,
//     e.g. 28-FF-64-1E-0F-00-00-A1 -> {0x28, 0xFF, 0x64, 0x1E, 0x0F, 0x00, 0x00, 0xA1}.
//  2. the map stored in the ESP32's flash (NVS) by the serial commands TROM SAVE / TROM SET,
//     when it holds five different ROMs. No rebuild needed; it survives resets and power
//     cuts, but NOT "erase flash" and not a change of board: copy it into the table above.
//  3. bus-scan order (not tied to a position on the module, and not the same after a boot
//     with a sensor missing); every row then carries the ST_TROM_UNMAPPED status bit.
static const uint8_t T_ROM[5][8] = {{0}, {0}, {0}, {0}, {0}};

// Measurement load (used only with HAS_LOAD 1). DS18B20 on the resistor heatsink, by ROM,
// same notation as T_ROM. TODO(user): fill in when the sensor is mounted (bench B5 prints
// it). While it is all zero the load stays in TRIP ("heatsink sensor missing"): both
// branches off, by design.
static const uint8_t HEATSINK_ROM[8] = {0};
// Loop watchdog (esp_task_wdt on the loop task, every build): if the loop stalls for this
// long the chip resets, the pins are released and the gate pull-downs switch the load and the
// pump off; logging then starts again by itself. The watchdog is also fed between the stages
// of a row, so the limit applies to ONE stage (a device retry, the reads, the card write,
// one segment copied back), not to their sum. [ASSUMPTION] 15 s is well above the longest
// stage (an SD mount that fails twice, a few seconds [EST]); check in bench B7.
static const uint32_t LOOP_WDT_MS = 15000;

// ---- devices -----------------------------------------------------------------------------
PvDs3231 rtc;
PvBme280 env;
PvPaHub hub;
PvIna226 panel("INA226 panel", &hub, PAHUB_CH_PANEL);
PvDs18b20 probes(pins::ONEWIRE);
PvPyr20Modbus pyr(Serial2, pins::RS485_RX, pins::RS485_TX);
PvSdCard sd;
PvFlashRing ring;                 // rows wait here while the card cannot be written
PvDevice *const DEVICES[] = {&rtc, &env, &hub, &panel, &probes, &pyr, &sd};
PvDevice *const I2C_RETRY[] = {&rtc, &env, &hub};   // re-begun at every row while not Ok (+ panel)
PvDevice *const SLOW_RETRY[] = {&probes, &pyr, &sd}; // re-begun every RETRY_EVERY rows

gbt_detector_t det;               // NASIM-GBT
thr_detector_t det_thr;           // baseline: temperature-rise threshold
cusum_inc_t det_cusum;            // baseline: CUSUM on the temperature increment
int det_doy = -1;                 // day the state of the three detectors belongs to
uint8_t det_fails = 0;            // rows skipped by the detector in a row

uint8_t t_map[5][8] = {{0}};      // ROM behind each of T1..T5 in use (all zero = none yet)
uint8_t t_saved[5][8] = {{0}};    // the map stored in NVS (TROM commands); all zero = none
bool t_rom_unmapped = true;       // true while t_map comes from the scan order
const char *t_map_source = "scan order";  // for the boot report
Preferences nvs;                  // namespace "pvdaq", key "t_rom" = 5 x 8 bytes
char day_path[16] = "/nodate.csv";  // file of the last row that had a valid RTC time

PvLoad load(panel);               // serviced only with HAS_LOAD 1
PvIna226 pump_ina("INA226 pump", &hub, PAHUB_CH_PUMP);  // talked to only with ENABLE_PUMP 1
PvPump pump(pump_ina);            // serviced only with ENABLE_PUMP 1
bool pump_ran = false;            // the pump was on at some time since the last 10 s row
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
const char *PUMP_HEADER = "date,time,uptime_ms,pump_duty,pump_V,pump_I,pump_P,pump_state";
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
// the next row, so one bad read costs one row (the I2C devices are also re-begun, above).
bool readable(const PvDevice &d) { return d.ok() || d.status() == PvStatus::ReadError; }

// True if the table holds five ROMs, all different.
bool mapComplete(const uint8_t m[5][8]) {
  for (int k = 0; k < 5; k++) {
    if (romIsZero(m[k])) return false;
    for (int j = 0; j < k; j++)
      if (memcmp(m[k], m[j], 8) == 0) return false;
  }
  return true;
}

// Decides which ROM is behind each of T1..T5. With T_ROM filled in: exactly T_ROM. Else
// with a complete saved map (NVS): exactly that. Otherwise by scan order, and the choice
// is then KEPT for this boot: a column that has a ROM never changes it, so a sensor that
// drops off the bus leaves its own column empty instead of shifting the others. Columns
// still free take the next ROM not used yet.
void mapProbes() {
  const uint8_t (*fixed)[8] = mapComplete(T_ROM) ? T_ROM : mapComplete(t_saved) ? t_saved : nullptr;
  t_rom_unmapped = !fixed;
  t_map_source = fixed == T_ROM ? "T_ROM table" : fixed ? "saved map (TROM command)" : "scan order";
  if (fixed) {
    memcpy(t_map, fixed, sizeof(t_map));
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
  if (t_rom_unmapped) {
    snprintf(line, sizeof(line), "boot ROM scan, UTC %s: %u DS18B20, T_ROM NOT filled in -> "
             "T1..T5 by scan order", when, probes.count);
  } else {
    snprintf(line, sizeof(line), "boot ROM scan, UTC %s: %u DS18B20, T1..T5 from the %s", when,
             probes.count, t_map_source);
  }
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
             t_rom_unmapped ? "no sensor for this column" : "its address is NOT on the bus");
    scanLine(line);
  }
}

const char *resetReasonText(esp_reset_reason_t r) {
  switch (r) {
    case ESP_RST_POWERON: return "power on";
    case ESP_RST_SW: return "software restart";
    case ESP_RST_PANIC: return "crash (panic)";
    case ESP_RST_INT_WDT: return "interrupt watchdog";
    case ESP_RST_TASK_WDT: return "task watchdog (the loop stalled)";
    case ESP_RST_WDT: return "other watchdog";
    case ESP_RST_BROWNOUT: return "brown-out (supply voltage dipped)";
    case ESP_RST_DEEPSLEEP: return "wake from deep sleep";
    case ESP_RST_EXT: return "reset pin";
    default: return "unknown";  // also what a reset through the USB-serial lines reads as
  }
}

// One line per start in /boot_log.csv: when (UTC, if the RTC has it) and why the chip started.
// A run of "task watchdog" or "brown-out" lines is the thing to look for after a field day.
void reportBoot() {
  const esp_reset_reason_t r = esp_reset_reason();
  char line[96], when[40] = ",";
  if (rtc.ok() && rtc.read() && rtc.timeValid) {
    const DateTime t = rtc.time();
    snprintf(when, sizeof(when), "%04u-%02u-%02u,%02u:%02u:%02u", t.year(), t.month(), t.day(),
             t.hour(), t.minute(), t.second());
  }
  snprintf(line, sizeof(line), "%s,%d,%s", when, (int)r, resetReasonText(r));
  Serial.printf("# start: %s\n", line);
  if (sd.ok()) sd.appendLine("/boot_log.csv", line, "date,time,reset_code,reset_reason");
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

// What must go on while the logger waits for something (a DS18B20 conversion, the RTC edge).
float heatsinkTemp();
void background() {
  if (HAS_LOAD) load.service(last_g, heatsinkTemp());
  if (ENABLE_PUMP) {
    pump.service();
    pump_ran |= pump.P.d > 0.0f;
  }
}

// ---- one sample ---------------------------------------------------------------------------
void sample(uint32_t n) {
  uint32_t status = 0;
  // Devices that are not Ok are started again before the row. The I2C devices (RTC, BME280,
  // PaHub, INA226) at EVERY row: begin() on a silent address fails in a few ms [EST], so a
  // glitch costs one row, not six. This also covers the cases a plain re-read cannot: a
  // BME280 that lost its settings in a brown-out, and an INA226 marked "not found" because
  // the PaHub did not answer once. The slow ones (1-Wire scan, PYR20 time-out, SD mount)
  // keep the 60 s rhythm; the SD card is re-mounted at the row's write anyway.
  bool retried = false;
  for (PvDevice *d : I2C_RETRY) {
    if (!d->ok()) retried |= d->begin();
  }
  if (!panel.ok() && hub.ok()) retried |= panel.begin();  // the INA226 needs the PaHub
  // The pump unit likewise, but never while the pump runs: begin() resets the chip.
  if (ENABLE_PUMP && !pump_ina.ok() && hub.ok() && pump.P.d <= 0.0f) retried |= pump_ina.begin();
  if (n % RETRY_EVERY == 0) {
    for (PvDevice *d : SLOW_RETRY) {
      if (!d->ok()) retried |= d->begin();
    }
    mapProbes();  // a re-scan may have found a sensor for a column that had none
  }
  if (retried || n == 0) printStatus();
  feedLoopWDT();

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
    while (millis() - conv_ms < PvDs18b20::CONVERSION_MS + 10) background();
    probes.fetch();
    conv_running = false;
  } else if (probes.count && ENABLE_PUMP) {  // same conversion, but the pump is watched meanwhile
    if (probes.startConversion()) {
      const uint32_t t0 = millis();
      while (millis() - t0 < PvDs18b20::CONVERSION_MS + 10) background();
      probes.fetch();
    }
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
  // status is the last field, so what happened to the write can be flagged in the row itself.
  feedLoopWDT();
  static bool row_lost = false;  // the row before this one is neither on the card nor in flash
  if (row_lost) status |= ST_SD_RETRY;
  const bool was_ok = sd.ok();
  const bool card = was_ok || sd.begin();
  bool written = false;
  String line;
  // Straight to the card, unless older rows still wait in the flash buffer: then this row
  // queues behind them, so the day file stays in time order.
  if (card && !ring.pending()) {
    line = row + String(status);
    written = sd.appendLine(day_path, line.c_str(), HEADER);
    if (!written && was_ok) {  // a card that was working gets one re-mount + retry
      status |= ST_SD_RETRY;
      line = row + String(status);
      written = sd.begin() && sd.appendLine(day_path, line.c_str(), HEADER);
    }
  }
  bool buffered = false;
  if (!written) {
    line = row + String(status | ST_BUFFERED);
    buffered = ring.store(day_path, line.c_str());
    if (!buffered) line = row + String(status);  // lost: only Serial has it
  }
  if (!written && !buffered && !row_lost) printStatus();
  row_lost = !written && !buffered;
  Serial.println(line);
  feedLoopWDT();

  // Card back: copy waiting segments to it, oldest first, a few per row.
  static bool was_pending = false;
  for (uint32_t k = 0; k < RING_FLUSH_PER_ROW && sd.ok() && ring.pending(); k++) {
    if (!ring.flushOldest(sd, HEADER)) break;
    feedLoopWDT();
  }
  if (ENABLE_PUMP) pumpRow(have_time, t, uptime_ms);
  if (ring.pending() != was_pending) {
    was_pending = ring.pending();
    Serial.printf("# flash buffer: %s (%s; %lu segments dropped for room since boot)\n",
                  was_pending ? "SD not writable, rows are kept in flash" : "all rows copied to the SD card",
                  ring.detail(), (unsigned long)ring.droppedSegments);
  }
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
    background();  // the hold loop and the pump watch go on meanwhile
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

// One event line (mode change, trip, command) to Serial and to an event file on the card.
void eventLog(const char *path, const char *tag, const char *text) {
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
  Serial.printf("# %s: %s\n", tag, line);
  if (sd.ok()) sd.appendLine(path, line, "date,time,uptime_ms,event");
}

void loadLog(const char *text) { eventLog("/load_log.csv", "load", text); }
void pumpLog(const char *text) { eventLog("/pump_log.csv", "pump", text); }

// ---- pump (called only with ENABLE_PUMP 1) -------------------------------------------------------
// PUMP <percent>   run at this PWM duty (soft start); below PUMP_D_MIN is refused
// PUMP OFF         stop now
// PUMP RESET       clear a trip, after the cause was looked at
// PUMP             print the state
void pumpCommand(const char *c) {
  while (*c == ' ') c++;
  char text[96];
  if (!*c) {
    Serial.printf("# pump: %s, duty %.0f %%, %.2f V, %.3f A, trip: %s; INA226 %s\n",
                  pump_state_text(pump.P.state), 100.0 * pump.P.d, pump.busV, pump.currentA,
                  pump_trip_text(pump.P.trip_reason), pump_ina.statusText());
  } else if (!strcmp(c, "OFF")) {
    pump.off();
    pumpLog("command PUMP OFF");
  } else if (!strcmp(c, "RESET")) {
    pump.clear();
    pumpLog("command PUMP RESET");
  } else if (isdigit((unsigned char)*c)) {
    const float d = constrain(atol(c), 0L, 100L) / 100.0f;
    if (pump.P.state == PUMP_TRIP) {
      Serial.println("# pump: tripped. Find the cause, then PUMP RESET");
    } else if (d > 0.0f && d < PUMP_D_MIN) {
      Serial.printf("# pump: refused, below the lowest running duty (%.0f %%)\n", 100.0 * PUMP_D_MIN);
    } else {
      pump.request(d);
      snprintf(text, sizeof(text), "command PUMP %.0f %%", 100.0 * d);
      pumpLog(text);
    }
  } else {
    Serial.println("# commands: PUMP <percent> | PUMP OFF | PUMP RESET | PUMP");
  }
}

// One pass of the pump: the protection logic, and a log line when its state changed.
void pumpLoop() {
  static uint32_t state = PUMP_IDLE;
  background();
  const pump_t &P = pump.P;
  if (P.state == state) return;
  char text[112];
  if (P.state == PUMP_TRIP) {
    snprintf(text, sizeof(text), "TRIP: %s (last I %.3f A). Pump off until PUMP RESET",
             pump_trip_text(P.trip_reason), P.i_last);
    pumpLog(text);
  } else if (P.state != PUMP_RUN || state != PUMP_RAMP) {  // ramp -> run is not worth a line
    snprintf(text, sizeof(text), "%s -> %s (request %.0f %%)", pump_state_text(state),
             pump_state_text(P.state), 100.0 * P.d_req);
    pumpLog(text);
  }
  state = P.state;
}

// One line in /PPYYYYMMDD.csv for a 10 s row in which the pump ran: the duty at the row and
// the last current reading of the protection (mean supply current, 16 averages). Raw values.
void pumpRow(bool have_time, const DateTime &t, uint32_t uptime_ms) {
  if (!pump_ran) return;
  pump_ran = pump.P.d > 0.0f;
  char path[40] = "/PPnodate.csv";  // sized for the widest values the compiler can see
  String row;
  row.reserve(96);
  if (have_time) {
    char dt[40];
    snprintf(path, sizeof(path), "/PP%04u%02u%02u.csv", t.year(), t.month(), t.day());
    snprintf(dt, sizeof(dt), "%04u-%02u-%02u,%02u:%02u:%02u", t.year(), t.month(), t.day(),
             t.hour(), t.minute(), t.second());
    row += dt;
  } else {
    row += ',';
  }
  row += ',';
  row += uptime_ms;
  addField(row, pump.P.d, 4);
  addField(row, pump.busV, 4);
  addField(row, pump.currentA, 4);
  addField(row, pump.powerW, 4);
  row += ',';
  row += pump_state_text(pump.P.state);
  Serial.printf("# pump row: %s\n", row.c_str());
  if (sd.ok()) sd.appendLine(path, row.c_str(), PUMP_HEADER);
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
    Serial.println("# commands: GAMMA <minutes> | LOAD OFF | LOAD ON | TROM ...");
  }
}

// ---- T1..T5 map in NVS ------------------------------------------------------------------------
void loadSavedMap() {
  memset(t_saved, 0, sizeof(t_saved));
  if (!nvs.begin("pvdaq", /*readOnly=*/false)) {
    Serial.println("# TROM: NVS not available, no saved map");
    return;
  }
  if (nvs.getBytesLength("t_rom") == sizeof(t_saved)) nvs.getBytes("t_rom", t_saved, sizeof(t_saved));
}

bool storeSavedMap() { return nvs.putBytes("t_rom", t_saved, sizeof(t_saved)) == sizeof(t_saved); }

// TROM                 print the sensors on the bus and the column of each
// TROM SAVE            keep today's assignment for good (all five columns must have a sensor)
// TROM SET <k> <ROM>   column Tk (1..5) = this sensor, e.g. TROM SET 3 28-FF-64-1E-0F-00-00-A1
// TROM CLEAR           forget the saved map (back to scan order, unless T_ROM is filled in)
void tromCommand(const char *c) {
  while (*c == ' ') c++;
  if (!*c) {
    reportRomScan();
  } else if (!strcmp(c, "SAVE")) {
    for (int k = 0; k < 5; k++) {
      if (probes.indexOf(t_map[k]) < 0) {
        Serial.printf("# TROM SAVE refused: T%d has no sensor on the bus now\n", k + 1);
        return;
      }
    }
    memcpy(t_saved, t_map, sizeof(t_saved));
    Serial.println(storeSavedMap() ? "# TROM: assignment saved" : "# TROM: NVS write FAILED");
    mapProbes();
    reportRomScan();
  } else if (!strncmp(c, "SET", 3)) {
    unsigned k = 0, b[8];
    if (sscanf(c + 3, " %u %2x-%2x-%2x-%2x-%2x-%2x-%2x-%2x", &k, &b[0], &b[1], &b[2], &b[3], &b[4],
               &b[5], &b[6], &b[7]) != 9 || k < 1 || k > 5) {
      Serial.println("# TROM SET <1..5> <ROM as 28-xx-xx-xx-xx-xx-xx-xx>");
      return;
    }
    uint8_t rom[8];
    for (int i = 0; i < 8; i++) rom[i] = (uint8_t)b[i];
    if (rom[0] != 0x28 || !PvDs18b20::romCrcOk(rom)) {  // a typing error breaks the CRC
      Serial.println("# TROM SET refused: not a DS18B20 address (family 28) or its CRC is wrong");
      return;
    }
    for (int j = 0; j < 5; j++) {
      if (j != (int)k - 1 && memcmp(t_saved[j], rom, 8) == 0) {
        Serial.printf("# TROM SET refused: that sensor is already T%d\n", j + 1);
        return;
      }
    }
    memcpy(t_saved[k - 1], rom, 8);
    if (!storeSavedMap()) Serial.println("# TROM: NVS write FAILED");
    int n_set = 0;
    for (int j = 0; j < 5; j++) n_set += !romIsZero(t_saved[j]);
    Serial.printf("# TROM: T%u set, %d of 5 columns set%s%s\n", k, n_set,
                  n_set < 5 ? " (the map is used once all five are set)" : "",
                  probes.indexOf(rom) < 0 ? "; WARNING: that sensor is not on the bus now" : "");
    if (n_set == 5) {
      mapProbes();
      reportRomScan();
    }
  } else if (!strcmp(c, "CLEAR")) {
    memset(t_saved, 0, sizeof(t_saved));
    nvs.remove("t_rom");
    memset(t_map, 0, sizeof(t_map));
    mapProbes();
    reportRomScan();
  } else {
    Serial.println("# commands: TROM | TROM SAVE | TROM SET <1..5> <ROM> | TROM CLEAR");
  }
}

void command(const char *c) {
  if (!strncmp(c, "TROM", 4)) tromCommand(c + 4);
  else if (ENABLE_PUMP && !strncmp(c, "PUMP", 4)) pumpCommand(c + 4);
  else if (HAS_LOAD) loadCommand(c);
  else Serial.println("# commands: TROM | TROM SAVE | TROM SET <1..5> <ROM> | TROM CLEAR");
}

void readCommands() {
  static char buf[48];  // the longest command: TROM SET <k> <ROM>, 35 characters
  static uint8_t n = 0;
  while (Serial.available()) {
    const char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      buf[n] = 0;
      if (n) command(buf);
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
  // Before the watchdog is on: the first start creates the file system (seconds [EST]).
  ring.begin(/*format_if_needed=*/true);
  Serial.printf("# flash buffer: %s%s%s\n", ring.statusText(), ring.detail()[0] ? ", " : "",
                ring.detail());
  reportBoot();
  loadSavedMap();
  mapProbes();
  reportRomScan();
  if (HAS_LOAD) {
    if (probes.count) probes.read();  // a first heatsink temperature before the load may start
    if (!load.begin()) Serial.println("# load: LEDC attach FAILED: both branches are held off");
    Serial.println("# load: active. Commands: GAMMA <minutes> | LOAD OFF | LOAD ON");
  }
  if (ENABLE_PUMP) {
    if (hub.ok()) pump_ina.begin();
    if (!pump.begin()) Serial.println("# pump: LEDC attach FAILED: the pump is held off");
    Serial.printf("# pump: enabled, off. INA226 pump = %s. Commands: PUMP <percent> | PUMP OFF | "
                  "PUMP RESET | PUMP\n", pump_ina.statusText());
  }
  esp_task_wdt_config_t wdt = {LOOP_WDT_MS, 0, true};  // time-out, no idle task, reset
  if (esp_task_wdt_reconfigure(&wdt) != ESP_OK) esp_task_wdt_init(&wdt);
  enableLoopWDT();  // the loop task joins the watchdog; fed after every loop() and by feedLoopWDT()
  Serial.println(HEADER);
}

void loop() {
  static uint32_t next = millis();
  static uint32_t n = 0;
  if (mark_ms) logMarker();
  readCommands();
  if (ENABLE_PUMP) pumpLoop();
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
