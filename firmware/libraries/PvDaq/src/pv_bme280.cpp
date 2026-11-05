#include "pv_bme280.h"

bool PvBme280::readChipId(uint8_t addr, uint8_t &id) {
  wire_.beginTransmission(addr);
  wire_.write(0xD0);                       // "id" register (Bosch datasheets)
  if (wire_.endTransmission(false) != 0) return false;
  if (wire_.requestFrom(addr, (uint8_t)1) != 1) return false;
  id = wire_.read();
  return true;
}

const char *PvBme280::model() const {
  switch (chipId) {
    case 0x60: return "BME280";
    case 0x58: return "BMP280";
    case 0x56:
    case 0x57: return "BMP280 (sample)";
    default: return "unknown";
  }
}

bool PvBme280::begin() {
  address = 0;
  chipId = 0;
  for (uint8_t a : {BME280_ADDR_A, BME280_ADDR_B}) {
    if (readChipId(a, chipId)) {
      address = a;
      break;
    }
  }
  if (!address) return setStatus(PvStatus::NotFound, "no ACK at 0x76 or 0x77");

  if (chipId == 0x60) {
    if (!bme_.begin(address, &wire_)) return setStatus(PvStatus::ReadError, "BME280 init failed");
    bme_.setSampling(Adafruit_BME280::MODE_FORCED, Adafruit_BME280::SAMPLING_X1,
                     Adafruit_BME280::SAMPLING_X1, Adafruit_BME280::SAMPLING_X1,
                     Adafruit_BME280::FILTER_OFF);
    return setStatus(PvStatus::Ok);
  }
  if (chipId == 0x58 || chipId == 0x56 || chipId == 0x57) {
    if (!bmp_.begin(address, chipId)) return setStatus(PvStatus::ReadError, "BMP280 init failed");
    bmp_.setSampling(Adafruit_BMP280::MODE_FORCED, Adafruit_BMP280::SAMPLING_X1,
                     Adafruit_BMP280::SAMPLING_X1, Adafruit_BMP280::FILTER_OFF);
    return setStatus(PvStatus::Ok, "BMP280: no humidity");
  }
  char d[48];
  snprintf(d, sizeof(d), "unknown chip ID 0x%02X", chipId);
  return setStatus(PvStatus::ReadError, d);
}

bool PvBme280::read() {
  bool good;
  if (chipId == 0x60) {
    good = bme_.takeForcedMeasurement();
    temperatureC = bme_.readTemperature();
    humidityPct = bme_.readHumidity();
    pressureHpa = bme_.readPressure() / 100.0f;
  } else {
    good = bmp_.takeForcedMeasurement();
    temperatureC = bmp_.readTemperature();
    humidityPct = NAN;
    pressureHpa = bmp_.readPressure() / 100.0f;
  }
  if (!good || isnan(temperatureC)) {
    temperatureC = humidityPct = pressureHpa = NAN;
    return setStatus(PvStatus::ReadError, "measurement failed");
  }
  return setStatus(PvStatus::Ok, hasHumidity() ? "" : "BMP280: no humidity");
}
