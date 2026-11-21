// Pins and device constants for the Phase 0 logger. One place for all of them;
// keep firmware/PINMAP.md in step with this file. Every value has its source.
#pragma once
#include <stdint.h>

// ---- Build switches (decisions 2026-11-10) --------------------------------------------
#ifndef ENABLE_PUMP
#define ENABLE_PUMP 0      // 0 until the bucket test has replaced the assumed thresholds
                           // below (cooling is back in the paper since 2026-11-14, but the
                           // pump is not bought or characterised): the pump is never driven
#endif                     // (pvOutputsOff still holds PUMP_PWM LOW: safety, not pump code).
                           // 1 = the pump driver with its protection (pv_pump.h) runs and the
                           // logger takes the PUMP serial commands (bucket test). Nothing in
                           // the firmware decides by itself to pump. build_all.py compiles
                           // this variant too ("logger_pump").
#define HAS_ANEMOMETER 0   // no anemometer in this phase: wind logged as NaN
#ifndef HAS_FLOW
#define HAS_FLOW 0         // 1 = a pulse-output flow sensor is wired to GPIO35 (pv_flow.h).
#endif                     // Keep 0 while nothing is connected: the pin has no pull-up or
                           // pull-down and would count noise.
#ifndef HAS_LOAD
#define HAS_LOAD 0         // 0 = no load hardware fitted: both load pins stay LOW, rows carry
#endif                     // D = NaN, load_mode = none.
                           // DROPPED 2026-11-21 (Asim): the PWM resistor load is not being
                           // built (charge controller + battery + lamp instead), so this
                           // stays 0. With 0 the compiler removes the load and I-V sweep
                           // code from the logger: tests/test_load_logic.py checks that no
                           // sweep function is in the built firmware. The code is kept,
                           // and still compiled by build_all.py as "logger_load", only so
                           // it does not rot; that build must not be flashed.

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
constexpr int FLOW_PULSE = 35;    // flow sensor pulses -> PCNT; input-only, NO internal pull:
                                  // external pull-up to 3.3 V or a divider (PINMAP.md)
constexpr int LOAD_A_PWM = 33;     // load branch A gate driver (MPP hold)
constexpr int LOAD_B_PWM = 14;     // load branch B gate driver (sweeps only); toggles at boot
                                   // (JTAG MTMS) -> 10 kOhm pull-down on the driver input
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
constexpr uint8_t PAHUB_CH_PUMP = 1;           // pump unit in port "1" (verified); only
                                               // talked to with ENABLE_PUMP 1
constexpr uint8_t PAHUB_CH_BATTERY = 2;        // RESERVED (2026-11-21): third INA226-10A, on
                                               // the battery, in port "2". Not bought; no
                                               // code reads it yet. Same fixed address 0x41
constexpr uint8_t PAHUB_CH_REFCELL = 3;        // RESERVED: fourth unit, reference cell (Stage 2)
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
constexpr uint8_t PWM_RES_BITS = 11;           // 2048 duty steps (doc 07 section 2)
// The LEDC timer counts 2^bits steps per PWM period from the 80 MHz APB clock, so
// frequency x 2^bits must not exceed 80 MHz: 20 kHz x 2048 = 40.96 MHz fits, 12 bit
// (81.92 MHz) would not. The build stops here if either value is changed beyond that.
static_assert((uint64_t)PWM_FREQ_HZ << PWM_RES_BITS <= 80000000ULL,
              "PWM_FREQ_HZ x 2^PWM_RES_BITS exceeds the 80 MHz LEDC clock");

// ---- Measurement load (option C, two branches; decision 2026-11-11) --------------------
// Two identical resistor branches, each with its own MOSFET, in parallel across the module:
//   branch A  switched all day: holds the module at its MPP          -> module sees R_BRANCH / D
//   branch B  switched only during an I-V sweep, with the same duty  -> module sees R_SWEEP / D
// Design: docs/paper/07-load-firmware-spec.md. Gates: pins::LOAD_A_PWM, pins::LOAD_B_PWM.
constexpr uint8_t LOAD_BRANCHES = 2;
constexpr float R_BRANCH = 1.5f;                       // ohm, each branch
constexpr float R_HOLD = R_BRANCH;                     // 1.5 ohm: branch A alone (MPP hold)
constexpr float R_SWEEP = R_BRANCH / LOAD_BRANCHES;    // 0.75 ohm: A and B together (sweep)

// Load firmware parameters (spec section 10 and the sections named). All are starting
// values; the bench step that fixes each one is named. tests/test_load_logic.py reads
// this block, so the PC tests run with exactly these numbers.
constexpr uint32_t SWEEP_PERIOD_S = 60;        // one I-V sweep per minute
constexpr uint32_t GAMMA_SWEEP_PERIOD_S = 15;  // during a gamma run (02-gamma-from-iv.md)
constexpr uint32_t SWEEP_N_COARSE = 24;        // bench B8 (repeatability)
constexpr uint32_t SWEEP_N_FINE = 24;
constexpr float SWEEP_D_MIN = 0.02f;           // first coarse duty; bench B3
constexpr uint32_t T_SETTLE_MS = 20;           // [EST] shortest wait after a duty step, 5 tau
                                               // in full sun; bench B4 (1.5 x worst case)
constexpr uint32_t SWEEP_CONV_MS = 9;          // INA226 1.1 ms x 2 x 4 averages = 8.8 ms (TI)
constexpr float SWEEP_SETTLE_TOL = 0.002f;     // [ASSUMPTION] a point is settled when two
                                               // readings in a row agree within 0.2 % of
                                               // V_oc (the figure of bench B4); B4, B6
constexpr uint32_t SWEEP_BUDGET_MS = 2200;     // [ASSUMPTION] longest sweep: SWEEP_GUARD_MS
                                               // less the 800 ms the next row needs
constexpr uint32_t VOC_SETTLE_MS = 50;         // wait at duty 0 before V_oc is read (sec. 4)
constexpr uint32_t SWEEP_GUARD_MS = 3000;      // [ASSUMPTION] a sweep (~1.7 s) starts only if
                                               // the next 10 s row is at least this far away
constexpr uint32_t HOLD_PERIOD_MS = 250;       // [ASSUMPTION] bench B3, B8
constexpr float HOLD_GAIN = 1.0f;              // [ASSUMPTION] bench B3, B8
constexpr float HOLD_MAX_STEP = 0.10f;         // duty change per hold step limited to +/-10 %
constexpr float HOLD_D_MIN = 0.01f;            // hold duty kept inside [0.01, 1.0]
constexpr float VSET_FRACTION_OF_VOC = 0.78f;  // [ASSUMPTION] before the first sweep
constexpr float LOAD_G_ON_WM2 = 50.0f;         // [ASSUMPTION] OFF -> HOLD at or above
constexpr float LOAD_G_OFF_WM2 = 30.0f;        // [ASSUMPTION] HOLD -> OFF below, for ...
constexpr uint32_t LOAD_G_OFF_MS = 300000;     // ... 5 min
constexpr float LOAD_VOC_ON_V = 10.0f;         // [ASSUMPTION] OFF -> HOLD also needs this V_oc
constexpr float T_TRIP_C = 80.0f;              // [ASSUMPTION] heatsink trip; bench B0, B5
constexpr float I_TRIP_A = 9.5f;               // unit range is +/-10 A (sec. 8)
constexpr uint32_t TRIP_CLEAR_MS = 300000;     // TRIP -> OFF after 5 min without the cause
constexpr uint32_t INA_FAIL_LIMIT = 3;         // failed readings in a row that freeze the duty
constexpr float SWEEP_G_UNSTABLE = 0.02f;      // G_UNSTABLE flag above 2 % (sec. 4)
constexpr float SWEEP_MONO_TOL = 0.005f;       // [ASSUMPTION] NON_MONOTONIC ignores voltage
                                               // rises below 0.5 % of V_oc (reading noise)

// ---- Pump protection (pv_pump_logic.h; used only with ENABLE_PUMP 1) ---------------------------
// NONE of these is a property of our pump: nobody has measured it. The only figures we
// have are seller listings of the R385 (6-12 V, 0.5-0.7 A under load; twin/params.py).
// The currents are what the pump's INA226 reads, i.e. the MEAN supply current, which
// falls with the PWM duty. Set all of them from the bucket test with the real nozzles:
// current at each duty with water, the same while the pump still draws air, the lowest
// duty at which it starts, and the current with the outlet blocked for a moment.
constexpr uint32_t PUMP_RAMP_MS = 1000;        // [ASSUMPTION] soft start, duty 0 -> 1
constexpr uint32_t PUMP_RAMP_MAX_STEP_MS = 20; // a busy loop advances the ramp by 2 % at most
constexpr float PUMP_D_MIN = 0.50f;            // [ASSUMPTION] lowest running duty; the twin's
                                               // placeholder (6 V of 12 V, seller listing)
constexpr float PUMP_I_TRIP_A = 1.5f;          // [ASSUMPTION] about twice the listed 0.7 A
constexpr uint32_t PUMP_I_TRIP_N = 3;          // readings in a row (~35 ms each)
constexpr float PUMP_I_DRY_A = 0.20f;          // [ASSUMPTION] below the listed 0.5 A; the
                                               // dry-running current is NOT known
constexpr uint32_t PUMP_PRIME_MS = 10000;      // [ASSUMPTION] time allowed to draw water
constexpr uint32_t PUMP_DRY_MS = 3000;         // [ASSUMPTION] low current this long = dry
constexpr uint32_t PUMP_INA_FAIL_LIMIT = 3;    // failed readings in a row that stop the pump

// ---- Flow sensor (pv_flow.h; used only with HAS_FLOW 1) ------------------------------------------
constexpr float FLOW_PULSES_PER_L = 0.0f;      // NOT KNOWN: no sensor chosen. 0 = log raw
                                               // pulses only. Fill in from a bucket
                                               // calibration (pulses counted / litres caught)
constexpr uint32_t FLOW_GLITCH_NS = 10000;     // PCNT ignores pulses shorter than 10 us; the
                                               // ESP32 filter cannot go above ~12.7 us
                                               // (1023 cycles of the 80 MHz clock)

// ---- Logging -------------------------------------------------------------------------
constexpr uint32_t SAMPLE_PERIOD_MS = 10000;   // one CSV row every 10 s (CLAUDE.md)

// Flash ring buffer for rows the SD card did not take (pv_flashring.h). The partition is
// 0x160000 B = 1408 kB ("spiffs" in the core's default.csv). [EST] a row is ~130 B, so a
// 60-row segment is ~8 kB and fills three 4 kB LittleFS blocks with its metadata: room
// for roughly 110 segments = 18 h of rows. NOT measured: read the "flash buffer" status
// line on the bench after an hour without the card.
constexpr uint32_t RING_SEG_ROWS = 60;          // rows per segment file = 10 min
constexpr uint32_t RING_RESERVE_BYTES = 65536;  // [ASSUMPTION] kept free: LittleFS needs
                                                // spare blocks to move data when it writes
constexpr uint32_t RING_MAX_SEGMENTS = 400;     // upper bound on files in /rb (boot scan time)
constexpr uint32_t RING_FLUSH_PER_ROW = 3;      // segments copied back to the card per 10 s
                                                // row: 30 min of backlog every 10 s
