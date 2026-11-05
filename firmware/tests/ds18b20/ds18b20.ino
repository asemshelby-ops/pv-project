// DS18B20 test: scans the 1-Wire bus, prints every ROM with its CRC check and the
// authenticity screen from CLAUDE.md (28-xx-xx-xx-xx-00-00-xx), then prints all
// temperatures every second (one 12-bit conversion takes 750 ms). Use it for the water-bath cross-calibration: put all
// sensors in one stirred bath and log this output.
#include <PvDaq.h>

PvDs18b20 probes(pins::ONEWIRE);

void scan() {
  probes.begin();
  Serial.printf("\nDS18B20 scan: %s %s, %u sensor(s)\n", probes.statusText(), probes.detail(),
                probes.count);
  uint8_t flagged = 0;
  for (uint8_t i = 0; i < probes.count; i++) {
    char txt[24];
    PvDs18b20::romText(probes.rom[i], txt);
    bool crc = PvDs18b20::romCrcOk(probes.rom[i]);
    bool genuine = PvDs18b20::looksGenuine(probes.rom[i]);
    if (!genuine) flagged++;
    Serial.printf("  #%u  %s  CRC %s  %s\n", i, txt, crc ? "ok" : "BAD",
                  genuine ? "pattern ok" : "LIKELY CLONE (bytes 5-6 not 00-00)");
  }
  if (probes.count) Serial.printf("  %u of %u flagged as likely clones\n", flagged, probes.count);
}

void setup() {
  pvOutputsOff();
  Serial.begin(115200);
  delay(300);
  scan();
  Serial.println("Type 'scan' to rescan after plugging sensors in.");
}

void loop() {
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd == "scan") scan();
  }
  static uint32_t next = millis();
  if ((int32_t)(millis() - next) < 0) return;
  next += 1000;
  if (!probes.ok() && probes.count == 0) {
    Serial.println("no sensors (type 'scan')");
    return;
  }
  probes.read();
  Serial.printf("%10lu ms", (unsigned long)millis());
  for (uint8_t i = 0; i < probes.count; i++) Serial.printf("  #%u %8.4f", i, probes.tempC[i]);
  Serial.printf("  [%s %s]\n", probes.statusText(), probes.detail());
}
