// Bits of the `status` column of every 10 s CSV row (decimal number).
// 0 = nothing to report. Several bits can be set in one row (values add up).
// The ONLY definition on the firmware side. The analysis keeps the same table in
// data/schema.py (STATUS_BITS); tests/test_logger_format.py fails if the two differ.
// Documented in docs/paper/07-load-firmware-spec.md, section 7.
// Never renumber a bit once data has been logged with it: add new bits at the end.
#pragma once

#define ST_RTC_INVALID  (1u << 0)  // no valid RTC time: date and time are empty, use uptime_ms
#define ST_TEMP_FAIL    (1u << 1)  // at least one of T1..T5 gave no valid reading (field empty)
#define ST_AMBIENT_FAIL (1u << 2)  // BME280: no ambient temperature
#define ST_IRRAD_FAIL   (1u << 3)  // PYR20: no irradiance
#define ST_PANEL_FAIL   (1u << 4)  // panel INA226 (or its PaHub channel): no V, I, P
#define ST_TROM_UNMAPPED (1u << 5) // a T1..T5 row of T_ROM is not filled in: that column is empty (files before CSV v2: T1..T5 followed the bus-scan order)
#define ST_DET_SKIPPED  (1u << 6)  // row not given to the detector (an input or the time is missing)
#define ST_DET_RESET    (1u << 7)  // detector state cleared at this row (too many skipped in a row)
#define ST_SD_RETRY     (1u << 8)  // SD: this row did not reach the card at the first attempt, or the row(s) before it did not reach the card
#define ST_SD_BUFFERED  (1u << 9)  // SD: this row waited in the LittleFS ring buffer and was copied to the card later

// Sensor numbers (CSV v2). Bit n of the `sens_fail` column = sensor n failed at this row;
// the fail-counter columns f_T1 ... f_sd follow the same order. Same table in
// data/schema.py (SENSOR_BITS), compared by the same test. Add new sensors at the end.
#define PV_SENSOR_T1   0   // DS18B20 behind column T1 ... T7 (ROM from T_ROM)
#define PV_SENSOR_T2   1
#define PV_SENSOR_T3   2
#define PV_SENSOR_T4   3
#define PV_SENSOR_T5   4
#define PV_SENSOR_T6   5
#define PV_SENSOR_T7   6
#define PV_SENSOR_ENV  7   // BME280 (or BMP280)
#define PV_SENSOR_RTC  8   // DS3231: no answer, or time not set, or a time jump not accepted
#define PV_SENSOR_HUB  9   // PaHub: no answer (the INA226 behind it is then not tried)
#define PV_SENSOR_INA  10  // panel INA226 itself (the PaHub answered)
#define PV_SENSOR_PYR  11  // PYR20 over RS485 Modbus
#define PV_SENSOR_SD   12  // microSD: the row did not reach the card at once
#define PV_SENSOR_COUNT 13
