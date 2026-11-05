// BME280 test: finds the sensor at 0x76/0x77, reads the chip ID (register 0xD0) and
// reports whether the board carries a real BME280 (0x60) or a BMP280 (0x58, no humidity).
// Then prints temperature, humidity and pressure every 2 s.
#include <PvDaq.h>

PvBme280 env;

void setup() {
  pvOutputsOff();
  Serial.begin(115200);
  delay(300);
  Wire.begin(pins::I2C_SDA, pins::I2C_SCL, I2C_CLOCK_HZ);
  env.begin();
  Serial.printf("\nBME280 test. begin: %s %s\n", env.statusText(), env.detail());
  if (env.address) {
    Serial.printf("address 0x%02X, chip ID 0x%02X -> %s%s\n", env.address, env.chipId,
                  env.model(), env.hasHumidity() ? "" : "  (NO humidity sensor on this board)");
  }
}

void loop() {
  delay(2000);
  if (!env.ok() && !env.begin()) {
    Serial.printf("%s (%s)\n", env.statusText(), env.detail());
    return;
  }
  env.read();
  Serial.printf("%s  T %.2f C  RH %.1f %%  P %.2f hPa  [%s]\n", env.model(), env.temperatureC,
                env.humidityPct, env.pressureHpa, env.statusText());
}
