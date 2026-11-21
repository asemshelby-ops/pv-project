// Bits of the `status` column, the last field of every 10 s CSV row (decimal number).
// 0 = nothing to report. Several bits can be set in one row (values add up).
// The ONLY definition on the firmware side. The analysis keeps the same table in
// data/schema.py (STATUS_BITS); tests/test_status.py fails if the two differ.
// Documented in docs/paper/07-load-firmware-spec.md, section 7.
// Never renumber a bit once data has been logged with it: add new bits at the end.
#pragma once

#define ST_RTC_INVALID  (1u << 0)  // no valid RTC time: date and time are empty, use uptime_ms
#define ST_TEMP_FAIL    (1u << 1)  // at least one of T1..T5 gave no valid reading (field empty)
#define ST_AMBIENT_FAIL (1u << 2)  // BME280: no ambient temperature
#define ST_IRRAD_FAIL   (1u << 3)  // PYR20: no irradiance
#define ST_PANEL_FAIL   (1u << 4)  // panel INA226 (or its PaHub channel): no V, I, P
#define ST_TROM_UNMAPPED (1u << 5) // T_ROM not filled in: T1..T5 follow the bus-scan order
#define ST_DET_SKIPPED  (1u << 6)  // row not given to the detector (an input or the time is missing)
#define ST_DET_RESET    (1u << 7)  // detector state cleared at this row (too many skipped in a row)
#define ST_SD_RETRY     (1u << 8)  // SD: this row needed a second write attempt, or the row(s)
                                   // before it did not reach the card
#define ST_BUFFERED     (1u << 9)  // SD: this row went to the ESP32's flash first (the card did
                                   // not take it, or older rows were still waiting there) and
                                   // was copied to the card later, in order
