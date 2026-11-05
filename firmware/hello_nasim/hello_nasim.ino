// hello_nasim — Step A toolchain check for the Freenove ESP32-WROVER.
// Prints "hello NASIM" plus chip / flash / PSRAM info once per second.
// No sensors, no model: this only proves compile -> flash -> serial works.
//
// Build:  arduino-cli compile firmware/hello_nasim
// Flash:  arduino-cli upload -p COMx firmware/hello_nasim
// Watch:  arduino-cli monitor -p COMx -c baudrate=115200

#include <Arduino.h>
#include "esp_flash.h"

uint32_t counter = 0;

void setup() {
  Serial.begin(115200);
  delay(500);  // let the USB-serial bridge settle so the first lines are not lost
}

void loop() {
  uint32_t flash_physical = 0;
  esp_flash_get_physical_size(NULL, &flash_physical);  // NULL = the main flash chip

  Serial.printf("hello NASIM #%lu  (uptime %lu ms)\n", (unsigned long)counter++, (unsigned long)millis());
  Serial.printf("  chip      : %s rev %u, %u cores @ %lu MHz\n",
                ESP.getChipModel(), ESP.getChipRevision(), ESP.getChipCores(),
                (unsigned long)ESP.getCpuFreqMHz());
  uint64_t mac = ESP.getEfuseMac();  // byte 0 of the MAC is the lowest byte
  Serial.printf("  MAC       : %02X:%02X:%02X:%02X:%02X:%02X\n",
                (uint8_t)(mac), (uint8_t)(mac >> 8), (uint8_t)(mac >> 16),
                (uint8_t)(mac >> 24), (uint8_t)(mac >> 32), (uint8_t)(mac >> 40));
  Serial.printf("  flash     : %lu B physical, %lu B in the image header, %lu MHz\n",
                (unsigned long)flash_physical, (unsigned long)ESP.getFlashChipSize(),
                (unsigned long)(ESP.getFlashChipSpeed() / 1000000));
  Serial.printf("  PSRAM     : %s, %lu B total, %lu B free\n",
                psramFound() ? "found" : "NOT found",
                (unsigned long)ESP.getPsramSize(), (unsigned long)ESP.getFreePsram());
  Serial.printf("  heap      : %lu B total, %lu B free\n",
                (unsigned long)ESP.getHeapSize(), (unsigned long)ESP.getFreeHeap());
  Serial.printf("  sketch    : %lu B used, %lu B free for the app\n",
                (unsigned long)ESP.getSketchSize(), (unsigned long)ESP.getFreeSketchSpace());
  Serial.printf("  SDK       : %s, Arduino core %d.%d.%d\n", ESP.getSdkVersion(),
                ESP_ARDUINO_VERSION_MAJOR, ESP_ARDUINO_VERSION_MINOR, ESP_ARDUINO_VERSION_PATCH);
  delay(1000);
}
