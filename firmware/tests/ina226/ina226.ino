// PaHub + 2x M5Stack INA226-10A test.
// 1. Scans the main I2C bus (all PaHub channels off), then each PaHub channel.
// 2. Starts the panel unit (channel PAHUB_CH_PANEL) and the pump unit (PAHUB_CH_PUMP),
//    checks the TI IDs, calibrates for the 5 mOhm shunt / 10 A, and prints
//    bus voltage, shunt voltage, current and power every second.
#include <PvDaq.h>

PvPaHub hub;
PvIna226 panel("INA226 panel", &hub, PAHUB_CH_PANEL);
PvIna226 pump("INA226 pump", &hub, PAHUB_CH_PUMP);

void report(PvIna226 &u) {
  Serial.printf("%s: begin %s %s", u.name(), u.statusText(), u.detail());
  if (u.manufacturerId) Serial.printf("  IDs 0x%04X / 0x%04X", u.manufacturerId, u.dieId);
  if (u.currentLsbA > 0) Serial.printf("  current LSB %.1f uA", u.currentLsbA * 1e6);
  Serial.println();
}

void setup() {
  pvOutputsOff();
  Serial.begin(115200);
  delay(300);
  Wire.begin(pins::I2C_SDA, pins::I2C_SCL, I2C_CLOCK_HZ);
  Serial.println("\nPaHub + INA226 test. I2C scan:");
  hub.begin();
  pvScanI2C(Serial, "main bus (channels off)");
  if (hub.ok()) {
    for (uint8_t ch = 0; ch < PAHUB_CHANNELS; ch++) {
      hub.select(ch);
      char label[32];
      snprintf(label, sizeof(label), "PaHub channel %u", ch);
      pvScanI2C(Serial, label);
    }
    hub.deselect();
  } else {
    Serial.printf("PaHub: %s (%s)\n", hub.statusText(), hub.detail());
  }
  panel.begin();
  pump.begin();
  report(panel);
  report(pump);
}

void printUnit(PvIna226 &u) {
  if (!u.ok() && !u.begin()) {
    Serial.printf("  %-13s %s (%s)\n", u.name(), u.statusText(), u.detail());
    return;
  }
  u.read();
  Serial.printf("  %-13s bus %7.3f V  shunt %8.4f mV  I %8.4f A  P %8.3f W  [%s]\n", u.name(),
                u.busV, u.shuntmV, u.currentA, u.powerW, u.statusText());
}

void loop() {
  delay(1000);
  if (!hub.ok()) hub.begin();
  printUnit(panel);
  printUnit(pump);
}
