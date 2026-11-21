#include "pv_bme280.h"

// One register, with the read checked. 0xD0 = "id", 0xF3 = "status" (Bosch datasheets).
bool PvBme280::readReg(uint8_t addr, uint8_t reg, uint8_t &value) {
  wire_.beginTransmission(addr);
  wire_.write(reg);
  if (wire_.endTransmission(false) != 0) return false;
  if (wire_.requestFrom(addr, (uint8_t)1) != 1) return false;
  value = wire_.read();
  return true;
}

// Waits until the chip has finished its measurement (status bit 3 = 0). Our own loop,
// because the BMP280 library's wait has no time limit and neither library notices a read
// that failed. False = the chip did not answer, or was still busy after timeout_ms.
bool PvBme280::waitIdle(uint32_t timeout_ms) {
  const uint32_t start = millis();
  delay(2);  // the "measuring" bit is set shortly after the trigger
  for (;;) {
    uint8_t status;
    if (!readReg(address, 0xF3, status)) return false;
    if (!(status & 0x08)) return true;
    if (millis() - start > timeout_ms) return false;
    delay(1);
  }
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
  if (chipId != 0x60 && chipId != 0x58 && chipId != 0x56 && chipId != 0x57) {
    char d[48];
    snprintf(d, sizeof(d), "unknown chip ID 0x%02X", chipId);
    return setStatus(PvStatus::ReadError, d);
  }

  // The library's own begin() runs ONCE per chip (audit 2026-11-21): the BME280 one waits
  // for the chip in a loop without a time limit ("while (isReadingCalibration())"), which
  // never ends if the sensor drops off the bus during it. It is needed once, to read the
  // factory calibration into the library; a later begin() (the logger calls it at the next
  // row after every failed read) only writes the sampling mode again, which is all a chip
  // that was re-powered has lost. The first call itself is covered by the loop watchdog.
  if (initAddr_ != address || initId_ != chipId) {
    initAddr_ = initId_ = 0;
    const bool started = chipId == 0x60 ? bme_.begin(address, &wire_) : bmp_.begin(address, chipId);
    if (!started) return setStatus(PvStatus::ReadError, "library init failed");
    initAddr_ = address;
    initId_ = chipId;
  }
  if (!configure()) return setStatus(PvStatus::ReadError, "lost on the bus");
  return setStatus(PvStatus::Ok, hasHumidity() ? "" : "BMP280: no humidity");
}

// Writes the sampling mode (the library does not report a failed write), then reads the
// chip ID once more: false if the chip did not answer.
bool PvBme280::configure() {
  if (chipId == 0x60) {
    bme_.setSampling(Adafruit_BME280::MODE_FORCED, Adafruit_BME280::SAMPLING_X1,
                     Adafruit_BME280::SAMPLING_X1, Adafruit_BME280::SAMPLING_X1,
                     Adafruit_BME280::FILTER_OFF);
  } else {
    bmp_.setSampling(Adafruit_BMP280::MODE_FORCED, Adafruit_BMP280::SAMPLING_X1,
                     Adafruit_BMP280::SAMPLING_X1, Adafruit_BMP280::FILTER_OFF);
  }
  uint8_t id = 0;
  return readChipId(address, id) && id == chipId;
}

// Audit 2026-11-21: the Adafruit libraries do not report a failed I2C read. With the
// sensor unplugged, takeForcedMeasurement() still returned true and readTemperature()
// computed a temperature from whatever was in its buffer: a wrong value with no error.
// So the chip ID is read before and after the measurement (it proves the chip was there
// the whole time), the wait for the result is our own bounded loop, and the values must
// be inside the sensor's range (Bosch datasheets: -40..85 degC, 0..100 %RH).
bool PvBme280::read() {
  uint8_t id = 0;
  if (!address || !readChipId(address, id) || id != chipId) {
    temperatureC = humidityPct = pressureHpa = NAN;
    return setStatus(PvStatus::ReadError, "lost on the bus");
  }
  bool good;
  if (chipId == 0x60) {
    good = bme_.takeForcedMeasurement();     // triggers and waits, at most 2 s (library)
    good = good && waitIdle(100);
    temperatureC = bme_.readTemperature();
    humidityPct = bme_.readHumidity();
    pressureHpa = bme_.readPressure() / 100.0f;
  } else {
    // Writing "forced" mode starts one measurement (Bosch datasheet). The library's own
    // takeForcedMeasurement() is not used: its wait loop has no time limit.
    bmp_.setSampling(Adafruit_BMP280::MODE_FORCED, Adafruit_BMP280::SAMPLING_X1,
                     Adafruit_BMP280::SAMPLING_X1, Adafruit_BMP280::FILTER_OFF);
    good = waitIdle(100);
    temperatureC = bmp_.readTemperature();
    humidityPct = NAN;
    pressureHpa = bmp_.readPressure() / 100.0f;
  }
  good = good && readChipId(address, id) && id == chipId;  // still there after the reads
  const bool t_ok = temperatureC >= -40.0f && temperatureC <= 85.0f;   // false for NaN too
  const bool rh_ok = !hasHumidity() || (humidityPct >= 0.0f && humidityPct <= 100.0f);
  if (!good || !t_ok || !rh_ok) {
    temperatureC = humidityPct = pressureHpa = NAN;
    return setStatus(PvStatus::ReadError, !good ? "measurement failed" : "value out of range");
  }
  return setStatus(PvStatus::Ok, hasHumidity() ? "" : "BMP280: no humidity");
}
