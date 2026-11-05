// PYR20 0-2 V analog test (GPIO34, ADC1). Prints the averaged, eFuse-calibrated
// voltage and the irradiance SR = 1000 * V (INFWIN PYR20 manual) every second.
// The ESP32 ADC only reads 150-2450 mV reliably (ESP-IDF docs), so below ~150 W/m2
// the analog value is not trustworthy; compare with the Modbus reading.
#include <PvDaq.h>

PvPyr20Analog pyr(pins::PYR20_ANALOG);

void setup() {
  pvOutputsOff();
  Serial.begin(115200);
  delay(300);
  pyr.begin();
  Serial.printf("\nPYR20 analog test on GPIO%d. %s\n", pins::PYR20_ANALOG, pyr.detail());
}

void loop() {
  delay(1000);
  pyr.read();
  Serial.printf("%5lu mV  ->  %6.0f W/m2  %s\n", (unsigned long)pyr.millivolts, pyr.irradiance,
                pyr.inRange ? "" : "(outside the 150-2450 mV ADC range)");
}
