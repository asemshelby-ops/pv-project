// DS3231 test: prints the RTC time and die temperature every second.
// Set the clock from the PC (UTC):
//     .venv/Scripts/python firmware/tools/set_rtc.py COM3
// or type in the serial monitor:  SET 2026-11-05 14:30:00
#include <PvDaq.h>

PvDs3231 rtc;

void setup() {
  pvOutputsOff();
  Serial.begin(115200);
  delay(300);
  Wire.begin(pins::I2C_SDA, pins::I2C_SCL, I2C_CLOCK_HZ);
  Serial.println("\nDS3231 test. I2C scan (expect 0x68 RTC, 0x57 its EEPROM):");
  pvScanI2C(Serial, "main bus");
  rtc.begin();
  Serial.printf("begin: %s %s\n", rtc.statusText(), rtc.detail());
}

void handleCommand(const String &cmd) {
  int y, mo, d, h, mi, s;
  if (sscanf(cmd.c_str(), "SET %d-%d-%d %d:%d:%d", &y, &mo, &d, &h, &mi, &s) == 6) {
    if (!rtc.ok() && !rtc.begin()) {
      Serial.println("SET failed: RTC not found");
      return;
    }
    rtc.set(DateTime(y, mo, d, h, mi, s));
    Serial.printf("SET ok: %04d-%02d-%02d %02d:%02d:%02d\n", y, mo, d, h, mi, s);
  } else if (cmd == "NOW") {
    // Answers at the moment the RTC second changes, so the PC can time the reply and
    // measure the RTC's offset to well below one second (set_rtc.py logs the drift).
    if (!rtc.ok() || !rtc.read() || !rtc.timeValid) {
      Serial.println("NOW failed: no valid RTC time");
      return;
    }
    const uint32_t s0 = rtc.time().unixtime(), start = millis();
    while (millis() - start < 1100) {
      if (!rtc.read()) break;
      const DateTime t = rtc.time();
      if (t.unixtime() != s0) {
        Serial.printf("NOW %04u-%02u-%02u %02u:%02u:%02u\n", t.year(), t.month(), t.day(),
                      t.hour(), t.minute(), t.second());
        return;
      }
    }
    Serial.println("NOW failed: the RTC second did not change");
  } else {
    Serial.println("unknown command; use: SET YYYY-MM-DD HH:MM:SS  or  NOW");
  }
}

void loop() {
  static uint32_t next = millis();
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.length()) handleCommand(cmd);
  }
  if ((int32_t)(millis() - next) < 0) return;
  next += 1000;

  if (!rtc.ok() && !rtc.begin()) {
    Serial.printf("RTC: %s (%s)\n", rtc.statusText(), rtc.detail());
    return;
  }
  if (!rtc.read()) {  // e.g. never set: the registers hold no valid date
    Serial.printf("no valid time  die %.2f C  [%s: %s]\n", rtc.temperatureC, rtc.statusText(),
                  rtc.detail());
    return;
  }
  const DateTime t = rtc.time();
  Serial.printf("%04u-%02u-%02u %02u:%02u:%02u  die %.2f C  [%s%s%s]\n", t.year(), t.month(),
                t.day(), t.hour(), t.minute(), t.second(), rtc.temperatureC, rtc.statusText(),
                rtc.detail()[0] ? ": " : "", rtc.detail());
}
