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
#if PHASE_B_ENABLED
// PHASE B (randomized spray trial; docs/phase-b/01-randomized-spray.md). NOT ADOPTED and not
// run on hardware: compiled only with PHASE_B_ENABLED 1 (pv_config.h). Each row then ends
// with six more columns, after `status`:
//   ...,status,spike_id,spray_assign,pump_duty,flow_Lpm,water_L_cum,P_pump_W
// and /spray_log.csv records the boot line (seed, counters), every event and every alarm
// that opened none. In THIS build the NASIM alarm does act: it opens a spike event and, in
// the spray arm of a wet run, starts the pump. The pump output is LOW at boot and after
// every stop of pv_spray_logic.c, and it never runs in a dry run (PHASE_B_DRY_RUN 1, the
// default). NASIM must be retrained on field data before a wet run.
#include <pv_spray.h>
#endif
#if MARKER_CODES_ENABLED
#include "phase_b_config.h"  // MARKER_CODE_*: generated from data/schema.py (MARKER_CODES)
#endif
#if NASIM_TIMING_LOG
#include <pv_timing.h>
#endif

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
#if PHASE_B_ENABLED
// Pump-rail INA226. The two M5 units share the fixed address 0x41, so the pump unit sits
// behind its own PaHub channel; PUMP_INA226_PAHUB_CH -1 = a unit wired directly to the bus
// at another address (config.py, [PLACEHOLDER]).
PvIna226 pump_ina("INA226 pump", PUMP_INA226_PAHUB_CH >= 0 ? &hub : nullptr,
                  PUMP_INA226_PAHUB_CH >= 0 ? PUMP_INA226_PAHUB_CH : 0, Wire, PUMP_INA226_ADDR,
                  PUMP_INA226_SHUNT_OHM, PUMP_INA226_MAX_A);
PvSpray spray(pump_ina);
// DS18B20 in the tank (T_water), by ROM: [PLACEHOLDER] in config.py. All zero = not fitted;
// with SPRAY_NEED_T_WATER no event then opens in a wet run.
static const uint8_t TANK_ROM[8] = TANK_ROM_BYTES;
String header_b;            // HEADER + the six Phase B columns (built in setup)
char day_checked[16] = "";  // day file whose first line was checked in this boot
#endif
#if NASIM_TIMING_LOG
static const uint32_t TIMING_PERIOD_MS = 3600000;  // one line per hour in /status_log.csv
pv_timing_t nasim_timing;
#endif

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
#if PHASE_B_ENABLED
    if (memcmp(probes.rom[i], TANK_ROM, 8) == 0) continue;      // nor is the tank sensor
#endif
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

#if PHASE_B_ENABLED
// ---- Phase B (randomized spray trial) -------------------------------------------------------
float tankTemp() {  // T_water; NaN = no valid reading
  if (!readable(probes)) return NAN;
  const int idx = probes.indexOf(TANK_ROM);  // an all-zero ROM matches no sensor
  return idx < 0 ? NAN : probes.tempC[idx];
}

// One line of /spray_log.csv, also printed: UTC date and time, uptime, event id (0 = none),
// what happened, details as key=value pairs. No comma inside `detail`.
void sprayLog(long id, const char *what, const char *detail) {
  char line[288];
  uint32_t unix_s;
  unsigned milli;
  const uint32_t ms = millis();
  if (utcOf(ms, unix_s, milli)) {
    const DateTime t(unix_s);
    snprintf(line, sizeof(line), "%04u-%02u-%02u,%02u:%02u:%02u,%lu,%ld,%s,%s", t.year(),
             t.month(), t.day(), t.hour(), t.minute(), t.second(), (unsigned long)ms, id, what,
             detail);
  } else {
    snprintf(line, sizeof(line), ",,%lu,%ld,%s,%s", (unsigned long)ms, id, what, detail);
  }
  Serial.printf("# spray: %s\n", line);
  if (sd.ok()) sd.appendLine("/spray_log.csv", line, "date,time,uptime_ms,spike_id,event,detail");
}

// Writes what the trial logic did since the last call: alarms that opened no event, events
// opened, sprays ended, windows closed.
void sprayNotes() {
  const uint32_t notes = spray_take_notes(&spray.S);
  if (!notes) return;
  const spray_t &S = spray.S;
  char d[160];
  if (notes & SPRAY_NOTE_REFUSED) {
    snprintf(d, sizeof(d), "reason=%s", spray_reason_text(S.last_reason));
    sprayLog(0, "refused", d);
  }
  if (notes & SPRAY_NOTE_STOPPED) {
    snprintf(d, sizeof(d), "reason=%s on_ms=%lu litres=%.4f energy_j=%.1f",
             spray_stop_text(S.done.stop_reason), (unsigned long)S.done.spray_ms,
             (double)((float)S.done.pulses / FLOW_PULSES_PER_L), (double)S.done.energy_j);
    sprayLog(S.done.spike_id, "stopped", d);
  }
  if (notes & SPRAY_NOTE_CLOSED) {
    snprintf(d, sizeof(d), "assign=%ld sprayed=%lu stop=%s on_ms=%lu litres=%.4f energy_j=%.1f",
             (long)S.done.assign, (unsigned long)S.done.sprayed,
             spray_stop_text(S.done.stop_reason), (unsigned long)S.done.spray_ms,
             (double)((float)S.done.pulses / FLOW_PULSES_PER_L), (double)S.done.energy_j);
    sprayLog(S.done.spike_id, "closed", d);
  }
  if (notes & SPRAY_NOTE_OPENED) {
    snprintf(d, sizeof(d), "assign=%ld t_module=%.2f t_water=%.2f", (long)S.assign,
             (double)S.t_module_alarm, (double)tankTemp());
    sprayLog(S.spike_id, "opened", d);
  }
}

// SPRAY ON | SPRAY OFF | SPRAY RESET | SPRAY STATUS. True if the line was one of them.
bool sprayCommand(const char *c) {
  if (strncmp(c, "SPRAY", 5)) return false;
  if (!strcmp(c, "SPRAY OFF")) {  // also stops a spray that is running
    spray.arm(false);
    sprayNotes();
    sprayLog(0, "command", "SPRAY OFF");
  } else if (!strcmp(c, "SPRAY ON")) {
    spray.arm(true);
    sprayLog(0, "command", spray.S.armed ? "SPRAY ON" : "SPRAY ON refused (not ready)");
    if (!spray.S.armed) Serial.printf("# spray: cannot switch on: %s\n", spray.detail());
  } else if (!strcmp(c, "SPRAY RESET")) {  // after a latched pump fault, once it is repaired
    spray.resetFault();
    sprayLog(0, "command", "SPRAY RESET");
  } else if (!strcmp(c, "SPRAY STATUS")) {
    const spray_t &S = spray.S;
    Serial.printf("# spray: mode=%s id=%ld assign=%ld armed=%lu dry_run=%d n_wet=%lu n_dry=%lu "
                  "fault=%s blocked=%s last_refusal=%s pump_ina=%s\n",
                  spray_mode_text(S.mode), (long)S.spike_id, (long)S.assign,
                  (unsigned long)S.armed, (int)PHASE_B_DRY_RUN, (unsigned long)S.n_wet,
                  (unsigned long)S.n_dry, spray_stop_text(S.fault_latched),
                  spray_reason_text(S.blocked), spray_reason_text(S.last_reason),
                  pump_ina.statusText());
  } else {
    Serial.println("# commands: SPRAY ON | SPRAY OFF | SPRAY RESET | SPRAY STATUS");
  }
  return true;
}

// A day file begun by a build without the Phase B columns has the old header line. Before
// the first row of this boot goes into such a file, the new header line is appended, so a
// reader knows from which line on a row has six more fields (analysis/ingest.py).
void rowHeaderCheck() {
  if (!strcmp(day_path, day_checked) || !sd.ok()) return;
  snprintf(day_checked, sizeof(day_checked), "%s", day_path);
  if (!SD.exists(day_path)) return;  // new file: the header is written with the first row
  File f = SD.open(day_path, FILE_READ);
  if (!f) return;
  String first = f.readStringUntil('\n');
  f.close();
  first.trim();
  if (first != header_b) sd.appendLine(day_path, header_b.c_str());
}

// Called once from setup(): devices of the trial, the boot line of /spray_log.csv.
void sprayBoot() {
  header_b = String(HEADER) + spray_row_header();
  if (PUMP_INA226_PAHUB_CH < 0 || hub.ok()) pump_ina.begin();
  const bool ready = spray.begin();
  const spray_t &S = spray.S;
  char d[224];
  snprintf(d, sizeof(d), "seed=%lu block=%lu n_wet=%lu n_dry=%lu dry_run=%lu armed=%lu "
           "duty=%.3f max_ms=%lu window_ms=%lu lockout_ms=%lu pulses_per_l=%.2f",
           (unsigned long)S.cfg.seed, (unsigned long)S.cfg.block, (unsigned long)S.n_wet,
           (unsigned long)S.n_dry, (unsigned long)S.cfg.dry_run, (unsigned long)S.armed,
           (double)S.cfg.pump_duty, (unsigned long)S.cfg.spray_max_ms,
           (unsigned long)S.cfg.window_ms, (unsigned long)S.cfg.lockout_ms,
           (double)S.cfg.pulses_per_l);
  sprayLog(0, "boot", d);
  if (!ready) {
    Serial.printf("# PHASE B: NOT READY (%s) -> no event will open\n", spray.detail());
    sprayLog(0, "fault", spray.detail());
  }
  Serial.println(PHASE_B_DRY_RUN
                     ? "# PHASE B: DRY RUN. Events are opened and arms drawn; the pump output stays LOW."
                     : "# PHASE B: WET RUN. A NASIM alarm can start the pump.");
  Serial.println("# PHASE B: commands SPRAY ON | SPRAY OFF | SPRAY RESET | SPRAY STATUS");
  if (!HAS_LOAD) {  // with the load the loop watchdog is already on (same time-out)
    esp_task_wdt_config_t wdt = {LOOP_WDT_MS, 0, true};
    if (esp_task_wdt_reconfigure(&wdt) != ESP_OK) esp_task_wdt_init(&wdt);
    enableLoopWDT();
  }
}

#endif  // PHASE_B_ENABLED
#if MARKER_CODES_ENABLED
// `code` column of /markers.csv for a marker name (nullptr = a button press = 0).
int markerCode(const char *what) {
  if (!what) return MARKER_CODE_BUTTON;
  if (!strcmp(what, "mesh on")) return MARKER_CODE_MESH_ON;
  if (!strcmp(what, "mesh off")) return MARKER_CODE_MESH_OFF;
  if (!strcmp(what, "windbreak on")) return MARKER_CODE_WINDBREAK_ON;
  if (!strcmp(what, "windbreak off")) return MARKER_CODE_WINDBREAK_OFF;
  if (!strcmp(what, "load off")) return MARKER_CODE_LOAD_OFF;
  if (!strcmp(what, "load on")) return MARKER_CODE_LOAD_ON;
  return MARKER_CODE_BUTTON;
}

// MARK MESH ON | MARK MESH OFF | MARK WINDBREAK ON | MARK WINDBREAK OFF: a marker row with
// its code, at the millisecond the line arrived. True if the line was a MARK command.
bool markCommand(const char *c) {
  if (strncmp(c, "MARK", 4)) return false;
  const uint32_t ms = millis();
  if (!strcmp(c, "MARK MESH ON")) writeMarker(ms, "mesh on");
  else if (!strcmp(c, "MARK MESH OFF")) writeMarker(ms, "mesh off");
  else if (!strcmp(c, "MARK WINDBREAK ON")) writeMarker(ms, "windbreak on");
  else if (!strcmp(c, "MARK WINDBREAK OFF")) writeMarker(ms, "windbreak off");
  else Serial.println("# commands: MARK MESH ON | MARK MESH OFF | MARK WINDBREAK ON | MARK WINDBREAK OFF");
  return true;
}

#endif  // MARKER_CODES_ENABLED
#if NASIM_TIMING_LOG
// One line of the status file /status_log.csv: the NASIM update times of the last hour
// (PvDaq/src/pv_timing.h) and the memory left. Measured on the board, not estimated.
void timingLog() {
  char stats[96], line[224];
  uint32_t unix_s;
  unsigned milli;
  const uint32_t ms = millis();
  timing_line(&nasim_timing, ms, stats, sizeof(stats));
  char when[24] = ",";
  if (utcOf(ms, unix_s, milli)) {
    const DateTime t(unix_s);
    snprintf(when, sizeof(when), "%04u-%02u-%02u,%02u:%02u:%02u", t.year(), t.month(), t.day(),
             t.hour(), t.minute(), t.second());
  }
  snprintf(line, sizeof(line), "%s,%lu,%s,%lu,%lu,%lu", when, (unsigned long)ms, stats,
           (unsigned long)ESP.getFreeHeap(), (unsigned long)ESP.getMinFreeHeap(),
           (unsigned long)uxTaskGetStackHighWaterMark(NULL));
  Serial.printf("# status: %s\n", line);
  if (sd.ok()) {
    String header = String("date,time,uptime_ms,") + timing_header() +
                    ",heap_free_B,heap_min_B,stack_min_B";
    sd.appendLine("/status_log.csv", line, header.c_str());
  }
}

#endif  // NASIM_TIMING_LOG
// ---- one sample ---------------------------------------------------------------------------
void sample(uint32_t n) {
  uint32_t status = 0;
  if (n % RETRY_EVERY == 0) {
    bool retried = false;
    for (PvDevice *d : DEVICES) {
      if (!d->ok() && d != &panel) retried |= d->begin();
    }
    if (!panel.ok() && hub.ok()) retried |= panel.begin();  // the INA226 needs the PaHub
#if PHASE_B_ENABLED
    if (!pump_ina.ok() && pump_ina.status() != PvStatus::ReadError &&
        (PUMP_INA226_PAHUB_CH < 0 || hub.ok()))
      pump_ina.begin();  // not found yet: try again (a unit lost on the bus is retried by trigger)
#endif
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
#if PHASE_B_ENABLED
    while (millis() - conv_ms < PvDs18b20::CONVERSION_MS + 10) {
      load.service(last_g, heatsinkTemp());
      spray.service();
    }
#else
    while (millis() - conv_ms < PvDs18b20::CONVERSION_MS + 10) load.service(last_g, heatsinkTemp());
#endif
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
#if NASIM_TIMING_LOG
    const uint32_t t_nasim_us = micros();
    if (gbt_update(&det, &s)) alarm |= NASIM_ALARM_NASIM;
    timing_add(&nasim_timing, micros() - t_nasim_us, det.p == det.p);  // p is NaN in warm-up
#else
    if (gbt_update(&det, &s)) alarm |= NASIM_ALARM_NASIM;
#endif
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

#if PHASE_B_ENABLED
  // Phase B: the row's inputs go to the trial logic (which may open an event and start the
  // pump), then its six columns and status bits are taken for this row.
  char spray_fields[96];
  {
    spray_row_in_t in = {};
    for (int k = 0; k < 5; k++) in.t_module += (float)T[k] / 5.0f;  // NaN if one is missing
    in.t_water = tankTemp();
    in.g = (float)g;
    in.alarm = (alarm & NASIM_ALARM_NASIM) ? 1 : 0;
    in.row_fault = (status & (ST_RTC_INVALID | ST_TEMP_FAIL | ST_AMBIENT_FAIL | ST_IRRAD_FAIL |
                              ST_PANEL_FAIL)) ? 1 : 0;
    in.load_ok = HAS_LOAD ? (load.L.mode == LOAD_HOLD ? 1 : 0) : 1;
    in.storage_ok = (sd.ok() && spray.storageOk()) ? 1 : 0;
    in.window_fits = (minute >= 0 && minute + (int)(PHASE_B_EVENT_WINDOW_MS / 60000u) <=
                                         NASIM_END_MIN) ? 1 : 0;
    spray.row(in);
    status |= spray.rowFields(spray_fields, sizeof(spray_fields));
    sprayNotes();
  }

#endif
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
#if PHASE_B_ENABLED
  rowHeaderCheck();
#endif
  for (int a = 0; a < attempts && !written; a++) {
    if (a > 0) status |= ST_SD_RETRY;
#if PHASE_B_ENABLED
    line = row + String(status) + spray_fields;
    written = (a == 0 ? (sd.ok() || sd.begin()) : sd.begin()) &&
              sd.appendLine(day_path, line.c_str(), header_b.c_str());
#else
    line = row + String(status);
    written = (a == 0 ? (sd.ok() || sd.begin()) : sd.begin()) &&
              sd.appendLine(day_path, line.c_str(), HEADER);
#endif
  }
  if (!written && !sd_lost) printStatus();
  sd_lost = !written;
  Serial.println(line);
#if NASIM_TIMING_LOG
  if (timing_due(&nasim_timing, millis())) timingLog();
#endif
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
#if PHASE_B_ENABLED
    spray.service();  // and so do the pump's stops
#endif
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
#if MARKER_CODES_ENABLED
  char coded[80];  // the same row with its code as a fourth field
  snprintf(coded, sizeof(coded), "%s,%d", line, markerCode(what));
  if (what) Serial.printf("# marker (%s): %s\n", what, coded);
  else Serial.printf("# marker: %s\n", coded);
  if (sd.ok()) sd.appendLine("/markers.csv", coded, "date,time,millis,code");
#else
  if (what) Serial.printf("# marker (%s): %s\n", what, line);
  else Serial.printf("# marker: %s\n", line);
  if (sd.ok()) sd.appendLine("/markers.csv", line, "date,time,millis");
#endif
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
#if MARKER_CODES_ENABLED
  if (markCommand(c)) return;
#endif
#if PHASE_B_ENABLED
  if (sprayCommand(c)) return;
  if (!HAS_LOAD) {  // no load fitted: the commands below have nothing to act on
    Serial.println("# no load fitted. Commands: SPRAY ON | SPRAY OFF | SPRAY RESET | SPRAY STATUS");
    return;
  }
#else
#if MARKER_CODES_ENABLED
  if (!HAS_LOAD) {
    Serial.println("# no load fitted. Commands: MARK MESH ON | MARK MESH OFF | MARK WINDBREAK ON | MARK WINDBREAK OFF");
    return;
  }
#endif
#endif
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
#if PHASE_B_ENABLED
  // No sweep while the pump runs: a sweep blocks for ~1.7 s, in which the pump's stops would
  // not be checked. The sweep that was due starts when the spray has ended.
  if (to_row > 0 && !spray.pumpOn() && load_sweep_due(&load.L, millis(), (uint32_t)to_row)) {
#else
  if (to_row > 0 && load_sweep_due(&load.L, millis(), (uint32_t)to_row)) {
#endif
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
#if NASIM_TIMING_LOG
  timing_init(&nasim_timing, TIMING_PERIOD_MS, millis());
  Serial.println("# NASIM update time: one line per hour in /status_log.csv");
#endif
#if PHASE_B_ENABLED
  sprayBoot();
  Serial.println(header_b);
#else
  Serial.println(HEADER);
#endif
}

void loop() {
  static uint32_t next = millis();
  static uint32_t n = 0;
  if (mark_ms) logMarker();
#if PHASE_B_ENABLED
  spray.service();
  sprayNotes();
  if (!HAS_LOAD) readCommands();  // with the load, loadLoop() reads the serial commands
#else
#if MARKER_CODES_ENABLED
  if (!HAS_LOAD) readCommands();
#endif
#endif
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
