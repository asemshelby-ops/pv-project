// nasim_replay -- supervisor task 2, part 1: NASIM-GBT (no wind) on the ESP32 by replay.
// No sensors: the PC streams recorded samples over serial; the board runs the exact
// detector of export/c (features, thermal model, CUSUM core, trees, alarm logic) and
// answers every sample with its probability, alarm flag and the time gbt_update took.
//
// Build, flash and run ONLY through the PC script, which sets the include paths to
// export/c and chooses double or float (-DNASIM_FLOAT):
//     python -m export.replay --port COM3 --precision double
//
// Protocol, 921600 baud, one line per message:
//   board -> PC  READY precision=<double|float> sizeof_real=<n> sizeof_detector=<n>
//   PC -> board  new_day,T1,T2,T3,T4,T5,ta,g,wind,doy,minute   (rows of test_input.csv)
//   board -> PC  p,alarm,us          p = nan while the buffers fill (warm-up)
//   PC -> board  STATS
//   board -> PC  STATS stack_free_min=<B> heap_free=<B> heap_free_min=<B>

#include <Arduino.h>
#include <stdlib.h>
extern "C" {                 // nasim.c is C; this sketch file is C++
#include "nasim.h"
void nasim_score_nowind(real *input, real *output);
}

static const real P_THRESHOLD = 0.995;  // tuned on validation days (out/results/hard/seed5/tuned.json)
static const int PERSIST = 2;           // config.GBT_PERSIST

static gbt_detector_t det;              // the whole detector state, statically allocated
static char line[256];
static size_t len = 0;

static void handle_sample(char *p) {
  // Parsed exactly like export/c/parity_main.c: strtod (double), then cast to real.
  char *end;
  int new_day = (int)strtol(p, &end, 10); p = end + 1;
  double v[9];
  for (int i = 0; i < 8; i++) { v[i] = strtod(p, &end); p = end + 1; }
  int doy = (int)strtol(p, &end, 10); p = end + 1;
  v[8] = strtod(p, &end);

  sample_t s;
  for (int i = 0; i < N_BACK; i++) s.t_back[i] = (real)v[i];
  s.ta = (real)v[5]; s.g = (real)v[6]; s.wind = (real)v[7];
  s.doy = doy; s.minute = (real)v[8];

  if (new_day) gbt_reset(&det);
  uint32_t t0 = micros();
  int alarm = gbt_update(&det, &s);
  uint32_t dt = micros() - t0;
  Serial.printf("%.17g,%d,%lu\n", (double)det.p, alarm, (unsigned long)dt);
}

void setup() {
  Serial.setRxBufferSize(8192);  // the PC keeps at most 64 lines (~4.5 KB) in flight
  Serial.begin(921600);
  delay(200);
  gbt_init(&det, nasim_score_nowind, /*use_wind=*/0, /*hybrid=*/1, P_THRESHOLD, PERSIST);
  Serial.printf("READY precision=%s sizeof_real=%u sizeof_detector=%u\n",
                sizeof(real) == 8 ? "double" : "float",
                (unsigned)sizeof(real), (unsigned)sizeof(gbt_detector_t));
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c != '\n') {
      if (len < sizeof(line) - 1) line[len++] = c;
      continue;
    }
    line[len] = '\0';
    len = 0;
    if (strcmp(line, "STATS") == 0) {
      Serial.printf("STATS stack_free_min=%u heap_free=%u heap_free_min=%u\n",
                    (unsigned)uxTaskGetStackHighWaterMark(NULL),
                    (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMinFreeHeap());
    } else if (line[0] == '0' || line[0] == '1') {
      handle_sample(line);
    }
  }
}
