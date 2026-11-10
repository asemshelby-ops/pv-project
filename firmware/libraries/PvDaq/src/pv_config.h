// Pins and device constants for the Phase 0 logger. One place for all of them;
// keep firmware/PINMAP.md in step with this file. Every value has its source.
#pragma once
#include <stdint.h>

// ---- Build switches (decisions 2026-11-10) --------------------------------------------
#define ENABLE_PUMP 0      // cooling and pump deferred: pump code compiled out
                           // (pvOutputsOff still holds PUMP_PWM LOW: safety, not pump code)
#define HAS_ANEMOMETER 0   // no anemometer in this phase: wind logged as NaN

// ---- GPIO (firmware/PINMAP.md) ----------------------------------------------
namespace pins {
constexpr int I2C_SDA = 21;
constexpr int I2C_SCL = 22;
constexpr int ONEWIRE = 4;
constexpr int SD_SCK = 18;
constexpr int SD_MISO = 19;
constexpr int SD_MOSI = 23;
constexpr int SD_CS = 13;
constexpr int RS485_TX = 25;      // ESP32 TX -> MAX3485 DI ("TXD" on most modules)
constexpr int RS485_RX = 26;      // ESP32 RX <- MAX3485 RO ("RXD")
constexpr int PYR20_ANALOG = 34;  // ADC1_CH6
constexpr int PUMP_PWM = 32;
constexpr int LOAD_PWM = 33;
constexpr int MARKER_BUTTON = 27;  // to GND, internal pull-up
}  // namespace pins

// ---- I2C ------------------------------------------------------------------------
constexpr uint32_t I2C_CLOCK_HZ = 100000;      // long leads, several pull-ups
constexpr uint8_t PAHUB_ADDR = 0x70;           // M5Stack PaHub V2.1 default (M5 docs)
// PCA9548A channel numbers (bit n of the command byte). Bench check 2026-11-05, one unit
// moved between ports: the port printed "n" is chip channel n ("0" -> 0, "1" -> 1).
// The chip has 8 channels (TI/NXP datasheet); M5 brings out ports 0-5. Scans cover all 8.
constexpr uint8_t PAHUB_CHANNELS = 8;          // all chip channels, for scans and range checks
constexpr uint8_t PAHUB_CH_PANEL = 0;          // panel unit in port "0" (verified)
#if ENABLE_PUMP
constexpr uint8_t PAHUB_CH_PUMP = 1;           // pump unit in port "1" (verified)
#endif
constexpr uint8_t INA226_ADDR = 0x41;          // M5Stack Unit INA226-10A, fixed (M5 docs)
constexpr float INA226_10A_SHUNT_OHM = 0.005f; // 5 mOhm (M5 docs; also their ESPHome config)
constexpr float INA226_10A_MAX_A = 10.0f;      // +/-10 A rating (M5 docs)
constexpr uint8_t DS3231_ADDR = 0x68;          // used by RTClib
constexpr uint8_t BME280_ADDR_A = 0x76;        // SDO low
constexpr uint8_t BME280_ADDR_B = 0x77;        // SDO high

// ---- PYR20 (INFWIN UM-PYR20 manual) ---------------------------------------------
constexpr uint8_t PYR20_SLAVE = 1;             // default address
constexpr uint32_t PYR20_BAUD = 9600;          // default, 8N1
constexpr uint16_t PYR20_REG_SR = 0x0000;      // solar radiation, UINT16, 0-2000 = 0-2000 W/m2
constexpr uint8_t PYR20_FC = 0x04;             // manual: FC 3 or 4; its read example uses 4

// ---- PWM (LEDC) -------------------------------------------------------------------
constexpr uint32_t PWM_FREQ_HZ = 20000;        // load: ~20 kHz (CLAUDE.md); pump: above hearing
constexpr uint8_t PWM_RES_BITS = 10;

// ---- Measurement load (option C) -----------------------------------------------------
constexpr float R_LOAD = 0.75f;                // ohm, resistor bank (decision 2026-11-10)

// ---- Logging -------------------------------------------------------------------------
constexpr uint32_t SAMPLE_PERIOD_MS = 10000;   // one CSV row every 10 s (CLAUDE.md)
