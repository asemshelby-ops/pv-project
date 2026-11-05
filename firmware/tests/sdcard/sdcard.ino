// microSD test: mounts the card, prints its type and size, writes a test file,
// reads it back and compares, then appends a line every 5 s (pull the card to see
// the driver report the failure and recover when it is reinserted).
// A card with no valid FAT volume can be formatted from here: type FORMAT in the
// serial monitor. That ERASES the card, and only works if it has no readable FAT
// volume (a readable card is never touched). Used once on 2026-11-05 because the PC
// has no SD slot.
#include <PvDaq.h>

PvSdCard sd;
const char *TEST_FILE = "/sdtest.txt";

void writeReadTest() {
  char line[64];
  snprintf(line, sizeof(line), "PV logger SD test, millis=%lu", (unsigned long)millis());
  File f = SD.open(TEST_FILE, FILE_WRITE);  // truncates
  if (!f) {
    Serial.println("write test: open failed -> FAIL");
    return;
  }
  f.println(line);
  f.close();
  f = SD.open(TEST_FILE, FILE_READ);
  String back = f ? f.readStringUntil('\n') : String();
  if (f) f.close();
  back.trim();
  Serial.printf("wrote: %s\nread : %s\nwrite/read test: %s\n", line, back.c_str(),
                back == line ? "PASS" : "FAIL");
}

void listRoot() {
  File root = SD.open("/");
  Serial.println("files in /:");
  for (File e = root.openNextFile(); e; e = root.openNextFile()) {
    Serial.printf("  %-24s %8lu B\n", e.name(), (unsigned long)e.size());
  }
}

void reportMounted() {
  Serial.printf("card %s, %llu MB (mounted on attempt %u)\n", sd.typeText(), sd.sizeMB(),
                sd.mountAttempts);
  writeReadTest();
  listRoot();
}

void setup() {
  pvOutputsOff();
  Serial.begin(115200);
  delay(300);
  sd.begin();
  Serial.printf("\nmicroSD test. begin: %s %s\n", sd.statusText(), sd.detail());
  if (!sd.ok()) {
    Serial.println("If the card has no valid FAT volume, type FORMAT to create one (ERASES the card).");
    return;
  }
  reportMounted();
}

void loop() {
  static uint32_t next = millis() + 5000;
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd == "FORMAT") {
      Serial.println("formatting (only if no valid FAT volume) ...");
      bool good = sd.formatIfNoFilesystem();
      Serial.printf("format/mount: %s %s\n", sd.statusText(), sd.detail());
      if (good) reportMounted();
    }
  }
  if ((int32_t)(millis() - next) < 0) return;
  next += 5000;
  if (!sd.ok() && !sd.begin()) {
    Serial.printf("SD: %s (%s)\n", sd.statusText(), sd.detail());
    return;
  }
  char line[48];
  snprintf(line, sizeof(line), "%lu", (unsigned long)millis());
  bool good = sd.appendLine("/append_test.csv", line, "millis");
  Serial.printf("append %s: %s %s\n", line, good ? "ok" : "FAILED", sd.detail());
}
