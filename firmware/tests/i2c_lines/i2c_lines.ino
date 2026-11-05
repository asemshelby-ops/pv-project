// I2C line check (no I2C traffic): tells from the ESP32 side whether SDA (GPIO21) and
// SCL (GPIO22) have external pull-ups, float, or are held low, and whether they are
// shorted together. Repeats every 2 s, so wires can be wiggled while watching.
//
// Method, per line:
//   internal pull-DOWN (~45 kOhm) on -> reads HIGH only if an external pull-up (a few kOhm,
//                                       normally on the module) is connected and powered
//   internal pull-UP on               -> reads LOW only if something holds the line low
//                                       (short to GND, or pull-ups to an unpowered VCC)
// The pins are never driven HIGH; SDA is driven LOW briefly for the short test, which is
// what every I2C device does anyway.
#include <PvDaq.h>

int readStable(int pin, int mode) {
  pinMode(pin, mode);
  delay(5);  // let the line settle through the internal resistor
  int highs = 0;
  for (int i = 0; i < 20; i++) highs += digitalRead(pin);
  return highs == 20 ? HIGH : highs == 0 ? LOW : -1;  // -1 = unstable
}

const char *levelText(int v) { return v == HIGH ? "HIGH" : v == LOW ? "LOW " : "UNSTABLE"; }

void checkLine(const char *label, int pin) {
  int with_down = readStable(pin, INPUT_PULLDOWN);
  int with_up = readStable(pin, INPUT_PULLUP);
  const char *verdict;
  if (with_down == HIGH && with_up == HIGH) {
    verdict = "external pull-up present -> line reaches a powered module";
  } else if (with_down == LOW && with_up == HIGH) {
    verdict = "FLOATING -> nothing connected to this pin (wrong pin, loose wire), or no pull-up";
  } else if (with_down == LOW && with_up == LOW) {
    verdict = "HELD LOW -> short to GND, or module pull-ups with its VCC unpowered";
  } else {
    verdict = "unclear (unstable reading: loose contact or noise)";
  }
  Serial.printf("  %s GPIO%-2d  pull-down: %s  pull-up: %s  => %s\n", label, pin,
                levelText(with_down), levelText(with_up), verdict);
}

void checkShort() {
  pinMode(pins::I2C_SCL, INPUT_PULLUP);
  pinMode(pins::I2C_SDA, OUTPUT);
  digitalWrite(pins::I2C_SDA, LOW);
  delay(5);
  int scl = digitalRead(pins::I2C_SCL);
  pinMode(pins::I2C_SDA, INPUT);
  Serial.printf("  SDA driven LOW -> SCL reads %s  => %s\n", scl ? "HIGH" : "LOW ",
                scl ? "no short between SDA and SCL" : "SDA and SCL SHORTED together (or SCL held low)");
}

void setup() {
  pvOutputsOff();
  Serial.begin(115200);
  delay(300);
  Serial.println("\nI2C line check (SDA = GPIO21, SCL = GPIO22), every 2 s");
}

void loop() {
  Serial.printf("t = %lu ms\n", (unsigned long)millis());
  checkLine("SDA", pins::I2C_SDA);
  checkLine("SCL", pins::I2C_SCL);
  checkShort();
  delay(2000);
}
