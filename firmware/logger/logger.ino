// Phase 0 data logger. Every 10 s (millis schedule, no drift): read every sensor that
// is present, skip the missing ones, and write one CSV row to the microSD card and to
// Serial. Columns: the schema of CLAUDE.md + wind (data/schema.py COLUMNS), then the
// NASIM-GBT (no wind, double precision) outputs p and alarm.
//
//   date,time,T1,T2,T3,T4,T5,T_ambient,humidity,irradiance,voltage,current,power,wind,p,alarm
//
// Timestamps are UTC (the DS3231 holds UTC). Missing value = empty field. Lines starting
// with '#' on Serial are status messages, not data. Files: /YYYYMMDD.csv per day (UTC
// date), /markers.csv for button presses.
// Build: python firmware/build_all.py logger   (adds the include paths to export/c).
//
// SAFETY: pump and load MOSFET outputs are held LOW. The alarm is only logged;
// NASIM is trained on SYNTHETIC data and must be retrained before it drives anything.
#include <PvDaq.h>

extern "C" {  // export/c is C, compiled here through nasim_core.c / nasim_model.c
#include "nasim.h"
void nasim_score_nowind(real *input, real *output);
}

// ---- configuration -------------------------------------------------------------------
static const real NASIM_P_THRESHOLD = 0.995;  // out/results/hard/seed5/tuned.json
static const int NASIM_PERSIST = 2;           // config.GBT_PERSIST
static const int NASIM_START_MIN = 6 * 60;    // detector window = synthetic day window,
static const int NASIM_END_MIN = 18 * 60;     // config.DAY_START / DAY_END (local time)
static const int32_t UTC_OFFSET_S = 3 * 3600; // Makkah = UTC+3: NASIM's time inputs are local
static const uint8_t RETRY_EVERY = 6;         // retry missing devices every 6 samples (60 s)
static const uint8_t RESYNC_EVERY = 6;        // re-find the RTC second edge every 60 s

// Back-sheet sensors T1..T5 by ROM. TODO: fill in after the water-bath cross-calibration
// and mounting (ds18b20 test sketch prints the ROMs). While a row is all zero, T1..T5
// take the sensors in bus-scan order (deterministic, but not tied to a position).
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

volatile uint32_t mark_ms = 0;    // last button press (ISR), 0 = none pending

// Millisecond UTC for marker rows. The DS3231 counts whole seconds, so the logger notes
// millis() at the moment the RTC second changes: UTC second anchor_s began at anchor_ms.
uint32_t anchor_s = 0;            // 0 = no anchor (RTC missing or not set)
uint32_t anchor_ms = 0;
const char *HEADER =
    "date,time,T1,T2,T3,T4,T5,T_ambient,humidity,irradiance,voltage,current,power,wind,p,alarm";

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

double backTemp(int k) {  // T1..T5 -> k = 0..4
  if (!probes.ok() && probes.status() != PvStatus::ReadError) return NAN;
  int idx = romIsZero(T_ROM[k]) ? (k < probes.count ? k : -1) : probes.indexOf(T_ROM[k]);
  return idx < 0 ? NAN : probes.tempC[idx];
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
  if (n % RETRY_EVERY == 0) {
    bool retried = false;
    for (PvDevice *d : DEVICES) {
      if (!d->ok() && d != &panel) retried |= d->begin();
    }
    if (!panel.ok() && hub.ok()) retried |= panel.begin();  // the INA226 needs the PaHub
    if (retried || n == 0) printStatus();
  }

  if (rtc.ok()) rtc.read();
  if (env.ok()) env.read();
  if (panel.ok()) panel.read();
  if (probes.count) probes.read();
  if (pyr.ok() || pyr.status() == PvStatus::ReadError) pyr.read();

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
  if (complete && in_window) {
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
  } else if (det_doy != -1) {
    gbt_reset(&det);  // a gap breaks the 10 s sequence the features assume
    det_doy = -1;
  }

  // CSV row
  String row;
  row.reserve(160);
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

  Serial.println(row);
  if (sd.ok() || sd.begin()) {
    char path[16];
    if (have_time) {
      snprintf(path, sizeof(path), "/%04u%02u%02u.csv", t.year(), t.month(), t.day());
    } else {
      snprintf(path, sizeof(path), "/nodate.csv");
    }
    if (!sd.appendLine(path, row.c_str(), HEADER)) printStatus();
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
