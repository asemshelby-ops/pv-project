// Bench test of the measurement load (two 1.5 ohm branches, GPIO33 and GPIO14).
// Procedure, step by step: BENCH.md in this folder. Pass criteria: docs/paper/06.
// Type a command and press Enter (115200 baud, line ending "Newline"):
//
//   OFF            both branches off, logic back to its start. ALWAYS SAFE.
//   D 0.25         manual: branch A at this duty, branch B off        (0 .. 1)
//   AB 0.25        manual: branches A and B at this duty
//   STEPS          manual: A alone 0, 0.05 .. 1.00, 2 s per step, one line per step
//   STEPS AB       the same with both branches
//   SETTLE 0.2 0.4       step branch A from the first duty to the second and print the
//   SETTLE 0.2 0.4 AB    voltage for 50 ms at the fastest INA226 setting (B4)
//   AUTO           the firmware logic as in the logger: OFF -> HOLD, a sweep every 60 s
//   HOLD 18.0      the logic holds this voltage; a sweep only on SWEEP
//   SWEEP          one I-V sweep now (needs AUTO or HOLD); prints every point
//   GAMMA 5        sweeps every 15 s for 5 min (needs AUTO or HOLD)
//   R              one 64-average reading
//
// The logic needs irradiance and a heatsink temperature. On the bench there is no
// pyranometer: the sketch gives the logic G = 1000 W/m2. The heatsink sensor is the FIRST
// DS18B20 found on the 1-Wire bus (GPIO4): connect only that one. Without it the manual
// commands still work (with a warning each second), and AUTO / HOLD trip at once, which
// is what the logger will do too.
//
// Safety in manual mode (the logic has its own): current above I_TRIP_A or heatsink at
// T_TRIP_C switches both branches off.
#include <PvDaq.h>
#include <pv_load.h>

PvPaHub hub;
PvIna226 panel("INA226 panel", &hub, PAHUB_CH_PANEL);
PvDs18b20 probes(pins::ONEWIRE);
PvLoad load(panel);

static const float BENCH_G = 1000.0f;  // stands in for the pyranometer
bool logic_on = false;                 // AUTO or HOLD
float hold_v = 0;                      // > 0: HOLD <v> (the setpoint is forced, no timed sweeps)
float t_heatsink = NAN;
bool conv_running = false;
uint32_t conv_ms = 0;
float settle_to_a = 0, settle_to_b = 0;

float heatsink() { return probes.count ? t_heatsink : NAN; }

void allOff(const char *why) {
  load.off();
  logic_on = false;
  hold_v = 0;
  Serial.printf("# OFF (%s): both branches off\n", why);
}

// One 64-average reading, with the manual-mode safety check.
bool readNow() {
  if (!panel.ok() && hub.ok()) panel.begin();
  const bool ok = load.rowReading();
  if (ok && load.isManual() && panel.currentA > I_TRIP_A) allOff("current above I_TRIP_A");
  return ok;
}

void printReading() {
  if (!readNow()) {
    Serial.printf("# INA226: %s (%s)\n", panel.statusText(), panel.detail());
    return;
  }
  Serial.printf("V %.4f  I %.4f  P %.3f\n", panel.busV, panel.currentA, panel.powerW);
}

void printSweep() {
  const uint32_t flags = load_sweep_close(&load.L, BENCH_G);
  const load_sweep_t &s = load.L.sweep;
  Serial.println("sweep_id,k,D,V,I");
  for (uint32_t k = 0; k < s.n; k++)
    Serial.printf("%lu,%lu,%.5f,%.4f,%.4f\n", (unsigned long)s.id, (unsigned long)k, s.d[k], s.v[k],
                  s.i[k]);
  // G_UNSTABLE cannot be judged here (no pyranometer): it is masked out of the line below.
  Serial.printf("# sweep %lu: %lu points in %lu ms, V_oc %.4f, best k=%lu D %.5f V %.4f I %.4f "
                "P %.3f, flags %lu\n",
                (unsigned long)s.id, (unsigned long)s.n, (unsigned long)(s.t_end_ms - s.t_start_ms),
                s.v_oc, (unsigned long)s.best, s.n ? s.d[s.best] : NAN, s.n ? s.v[s.best] : NAN,
                s.n ? s.i[s.best] : NAN, s.n ? s.v[s.best] * s.i[s.best] : NAN,
                (unsigned long)(flags & ~LOAD_FLAG_G_UNSTABLE));
}

void steps(bool both) {
  Serial.println("D,V,I,R_from_reading   (R = V * D / I: 1.5 ohm for A alone, 0.75 for A and B)");
  for (int k = 0; k <= 20 && (k == 0 || load.isManual()); k++) {
    const float d = k * 0.05f;
    load.manual(d, both ? d : 0.0f);
    delay(2000);
    if (!readNow()) break;
    Serial.printf("%.2f,%.4f,%.4f,%.4f\n", d, panel.busV, panel.currentA,
                  panel.currentA > 0.01f ? panel.busV * d / panel.currentA : NAN);
  }
  allOff("STEPS finished");
}

void settle(float d1, float d2, bool both) {
  static float v[110];
  static uint32_t t[110];
  load.manual(d1, both ? d1 : 0.0f);
  delay(500);
  settle_to_a = d2;
  settle_to_b = both ? d2 : 0.0f;
  const uint16_t n = panel.fastBusV(v, t, 110, [] { load.manual(settle_to_a, settle_to_b); });
  delay(300);
  panel.begin();  // back to the normal INA226 settings
  Serial.println("t_ms,V   (t = 0: the duty step)");
  for (uint16_t k = 0; k < n; k++) Serial.printf("%.2f,%.4f\n", (t[k] - t[0]) / 1000.0f, v[k]);
  if (n) readNow();
  Serial.printf("# end value (64-average, 0.3 s later): %.4f V. Stays at D = %.2f; type OFF to end.\n",
                panel.busV, d2);
}

void command(char *c) {
  float a = 0, b = 0;
  char word[8] = "";
  if (!strcmp(c, "OFF")) {
    allOff("command");
  } else if (sscanf(c, "D %f", &a) == 1 || sscanf(c, "AB %f", &a) == 1) {
    logic_on = false;
    a = constrain(a, 0.0f, 1.0f);
    load.manual(a, c[0] == 'A' ? a : 0.0f);
    Serial.printf("# manual: A %.4f  B %.4f\n", a, c[0] == 'A' ? a : 0.0f);
  } else if (!strncmp(c, "STEPS", 5)) {
    logic_on = false;
    steps(strstr(c, "AB") != nullptr);
  } else if (sscanf(c, "SETTLE %f %f %7s", &a, &b, word) >= 2) {
    logic_on = false;
    settle(constrain(a, 0.0f, 1.0f), constrain(b, 0.0f, 1.0f), !strcmp(word, "AB"));
  } else if (!strcmp(c, "AUTO") || sscanf(c, "HOLD %f", &a) == 1) {
    load.off();
    hold_v = c[0] == 'H' ? a : 0;
    if (hold_v > 0) load.L.cfg.sweep_period_ms = 0;  // sweeps only on command
    else load.L.cfg.sweep_period_ms = SWEEP_PERIOD_S * 1000;
    logic_on = true;
    Serial.println(hold_v > 0 ? "# logic on: constant-voltage hold" : "# logic on: as in the logger");
  } else if (!strcmp(c, "SWEEP")) {
    if (logic_on) load_sweep_request(&load.L, millis());
    else Serial.println("# SWEEP needs AUTO or HOLD <volts> first");
  } else if (sscanf(c, "GAMMA %f", &a) == 1) {
    if (logic_on) load_gamma(&load.L, millis(), (uint32_t)constrain(a, 0.0f, 120.0f));
    else Serial.println("# GAMMA needs AUTO or HOLD <volts> first");
  } else if (!strcmp(c, "R")) {
    printReading();
  } else {
    Serial.println("# commands: OFF | D x | AB x | STEPS [AB] | SETTLE d1 d2 [AB] | AUTO | "
                   "HOLD volts | SWEEP | GAMMA minutes | R");
  }
}

void readCommands() {
  static char buf[32];
  static uint8_t n = 0;
  while (Serial.available()) {
    const char ch = (char)Serial.read();
    if (ch == '\n' || ch == '\r') {
      buf[n] = 0;
      if (n) command(buf);
      n = 0;
    } else if (n < sizeof(buf) - 1) {
      buf[n++] = (char)toupper(ch);
    }
  }
}

// Heatsink temperature every 2 s, without blocking the hold loop.
void serviceHeatsink() {
  if (!probes.count) return;
  if (!conv_running) {
    conv_running = probes.startConversion();
    conv_ms = millis();
  } else if (millis() - conv_ms >= 2000) {
    probes.fetch();
    t_heatsink = probes.tempC[0];
    conv_running = false;
    if (load.isManual() && !(t_heatsink < T_TRIP_C)) allOff("heatsink at T_TRIP_C or no reading");
  }
}

void status() {
  const load_t &L = load.L;
  if (load.isManual()) {
    const bool ok = readNow();
    Serial.printf("manual  V %.4f  I %.4f  P %.3f  heatsink %.1f C%s%s\n", ok ? panel.busV : NAN,
                  ok ? panel.currentA : NAN, ok ? panel.powerW : NAN, heatsink(),
                  ok ? "" : "  INA226 NOT READING",
                  probes.count ? "" : "  WARNING: no heatsink sensor, watch the temperature");
  } else if (logic_on) {
    Serial.printf("%-5s  D_A %.4f  D_B %.4f  V %.4f  I %.4f  P %.3f  V_set %.3f  heatsink %.1f C"
                  "%s%s%s%s\n",
                  load_mode_text(L.mode), L.d_a, L.d_b, L.v_last, L.i_last, L.v_last * L.i_last,
                  L.v_set, heatsink(), L.mode == LOAD_TRIP ? "  TRIP: " : "",
                  L.mode == LOAD_TRIP ? load_trip_text(L.trip_reason) : "",
                  L.ina_frozen ? "  INA226 FAILING: duty frozen" : "",
                  L.gamma_active ? "  gamma run" : "");
  }
}

void setup() {
  pvOutputsOff();
  Serial.begin(115200);
  delay(300);
  Wire.begin(pins::I2C_SDA, pins::I2C_SCL, I2C_CLOCK_HZ);
  Serial.println("\n# measurement-load bench test. Type OFF at any time: both branches off.");
  hub.begin();
  if (hub.ok()) panel.begin();
  Serial.printf("# PaHub: %s   INA226 panel: %s %s\n", hub.statusText(), panel.statusText(),
                panel.detail());
  probes.begin();
  if (probes.count) {
    char rom[24];
    PvDs18b20::romText(probes.rom[0], rom);
    probes.read();
    t_heatsink = probes.tempC[0];
    Serial.printf("# heatsink sensor: ROM %s, %.2f C (%u DS18B20 on the bus; connect only one)\n",
                  rom, t_heatsink, probes.count);
    Serial.println("#   -> put this ROM into HEATSINK_ROM in logger/logger.ino");
  } else {
    Serial.println("# NO heatsink sensor: manual commands only; AUTO and HOLD will trip.");
  }
  if (!load.begin()) Serial.println("# LEDC attach FAILED: both branches are held off");
  Serial.printf("# PWM %lu Hz, %u bit; trip at %.0f C or %.1f A. Waiting for a command.\n",
                (unsigned long)PWM_FREQ_HZ, (unsigned)PWM_RES_BITS, T_TRIP_C, I_TRIP_A);
}

void loop() {
  static uint32_t last_status = 0;
  readCommands();
  serviceHeatsink();
  if (logic_on) {
    load.service(BENCH_G, heatsink());
    if (hold_v > 0 && load.L.mode == LOAD_HOLD) load.L.v_set = hold_v;
    if (load.sweepStart(10000, BENCH_G)) {  // no 10 s rows on the bench: always "far away"
      load.finishSweep();
      if (load.L.sweep_done) printSweep();
    }
  }
  if (millis() - last_status >= 1000) {
    last_status = millis();
    status();
  }
}
