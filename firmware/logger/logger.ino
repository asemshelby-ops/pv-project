// Phase 0 data logger. Every 10 s (millis schedule, no drift): read every sensor that
// is present, skip the missing ones, and write one CSV row to the microSD card and to
// Serial. Columns: the schema of CLAUDE.md + wind (data/schema.py COLUMNS), then the
// NASIM-GBT (no wind, double precision) outputs p and alarm, then five columns:
// D, load_mode, sweep_id (no load hardware yet: NaN, none, 0), uptime_ms, status.
//
//   date,time,T1,T2,T3,T4,T5,T_ambient,humidity,irradiance,voltage,current,power,wind,p,alarm,
//   D,load_mode,sweep_id,uptime_ms,status
//
// Timestamps are UTC (the DS3231 holds UTC). Missing value = empty field. Lines starting
// with '#' on Serial are status messages, not data. Files: /YYYYMMDD.csv per day (UTC
// date), /markers.csv for button presses, /rom_scan.txt for the DS18B20 ROMs found at boot.
// A row is never dropped: without a valid RTC time it is written with empty date and time,
// its uptime_ms and the ST_RTC_INVALID status bit (bits: PvDaq/src/pv_status.h), into the
// last day file used since boot, or /nodate.csv if there was none.
// Build: python firmware/build_all.py logger   (adds the include paths to export/c).
//
// SAFETY: pump and load MOSFET outputs are held LOW. The alarm is only logged;
// NASIM is trained on SYNTHETIC data and must be retrained before it drives anything.
#include <PvDaq.h>
#include <pv_status.h>

extern "C" {  // export/c is C, compiled here through nasim_core.c / nasim_model.c
#include "nasim.h"
void nasim_score_nowind(real *input, real *output);
}

// ---- configuration -------------------------------------------------------------------
static const real NASIM_P_THRESHOLD = 0.98;   // out/paper_demo/seed5/results/tuned.json
static const int NASIM_PERSIST = 2;           // config.GBT_PERSIST
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

// ---- devices -----------------------------------------------------------------------------
PvDs3231 rtc;
PvBme280 env;
PvPaHub hub;
PvIna226 panel("INA226 panel", &hub, PAHUB_CH_PANEL);
PvDs18b20 probes(pins::ONEWIRE);
PvPyr20Modbus pyr(Serial2, pins::RS485_RX, pins::RS485_TX);
PvSdCard sd;
PvDevice *const DEVICES[] = {&rtc, &env, &hub, &panel, &probes, &pyr, &sd};

gbt_detector_t det;
int det_doy = -1;                 // day the detector state belongs to
uint8_t det_fails = 0;            // rows skipped by the detector in a row

uint8_t t_map[5][8] = {{0}};      // ROM behind each of T1..T5 in use (all zero = none yet)
bool t_rom_unmapped = true;       // true while t_map comes from the scan order, not T_ROM
char day_path[16] = "/nodate.csv";  // file of the last row that had a valid RTC time

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
  if (readable(panel)) panel.read();
  if (probes.count) probes.read();

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

  // NASIM: same input as the replay test; needs time, T1..T5, ambient and irradiance.
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
      gbt_reset(&det);
      det_doy = doy;
    }
    sample_t s;
    for (int k = 0; k < N_BACK; k++) s.t_back[k] = T[k];
    s.ta = ta;
    s.g = g;
    s.wind = NAN;
    s.doy = doy;
    s.minute = minute + lt.second() / 60.0;
    alarm = gbt_update(&det, &s);
    p = det.p;  // NAN while the 10-min buffers fill
  } else if (have_time && !in_window) {  // planned: the detector rests outside the window
    if (det_doy != -1) gbt_reset(&det);
    det_doy = -1;
    det_fails = 0;
  } else {  // invalid row (input or time missing): skip it, keep the detector state
    status |= ST_DET_SKIPPED;
    if (det_fails < 255) det_fails++;
    if (det_fails == DET_RESET_AFTER_FAILS && det_doy != -1) {
      gbt_reset(&det);  // too long a hole in the 10 s sequence the features assume
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
  row += ",NaN,none,0,";  // D, load_mode, sweep_id: no load hardware yet
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
    if (!rtc.read()) return;
    const uint32_t s = rtc.time().unixtime();
    if (s != s0) {
      anchor_ms = millis();
      anchor_s = s;
      return;
    }
  }
}

// One row per press: UTC date, UTC time with milliseconds, and millis() at the press.
void logMarker() {
  uint32_t ms = mark_ms;
  mark_ms = 0;
  char line[64];
  if (anchor_s) {
    const int64_t utc_ms = (int64_t)anchor_s * 1000 + (int32_t)(ms - anchor_ms);
    const DateTime t((uint32_t)(utc_ms / 1000));
    snprintf(line, sizeof(line), "%04u-%02u-%02u,%02u:%02u:%02u.%03u,%lu", t.year(), t.month(),
             t.day(), t.hour(), t.minute(), t.second(), (unsigned)(utc_ms % 1000),
             (unsigned long)ms);
  } else {
    snprintf(line, sizeof(line), ",,%lu", (unsigned long)ms);
  }
  Serial.printf("# marker: %s\n", line);
  if (sd.ok()) sd.appendLine("/markers.csv", line, "date,time,millis");
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
  Serial.println("# PV Phase 0 logger. NASIM-GBT (no wind, double) trained on SYNTHETIC data.");
  Serial.println("# timestamps are UTC (DS3231)");
  for (PvDevice *d : DEVICES) {
    if (d != &panel) d->begin();
  }
  if (hub.ok()) panel.begin();  // the INA226 needs the PaHub
  mapProbes();
  reportRomScan();
  Serial.println(HEADER);
}

void loop() {
  static uint32_t next = millis();
  static uint32_t n = 0;
  if (mark_ms) logMarker();
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
