// Phase B: randomized spray trial -- the logic (docs/phase-b/01-randomized-spray.md).
// NOT ADOPTED: prepared 2026-11-17 before any pump, flow sensor or tank exists. Nothing in
// the logger calls this file unless it is built with PHASE_B_ENABLED 1 (pv_config.h).
//
// What it does. On a NASIM alarm it opens a "spike event", numbers it and draws its arm
// (spray / no-spray) from a seeded sequence of permuted blocks, so the two arms stay
// balanced even with few events. In the spray arm it runs the pump at a fixed duty until a
// stop condition; in both arms the event stays open for the same window. It also keeps
// the three metered quantities (pump duty, water flow, pump power) as means over each 10 s
// row, so the rows add up to the litres and the watt-hours of an event.
//
// Plain C with no Arduino calls, like pv_load_logic.c: it compiles on the PC and is tested
// there (tests/test_spray_logic.py). The logic never touches hardware. The caller
// (pv_spray.cpp on the board, the test on the PC) does this:
//   every loop pass   spray_flow(pulses counted since the last pass); spray_service();
//                     write S.duty to the pump PWM; if spray_pump_due(): one INA226 reading
//                     of the pump rail -> spray_pump_reading()
//   every 10 s row    spray_row(inputs of the row); if S.commit_pending: store the event
//                     counter where it survives a restart, then spray_commit();
//                     spray_row_fields() gives the six columns and the status bits
//
// SAFE STATE. spray_init() and every stop leave S.duty = 0 (pump off). The pump can run
// only in SPRAY_ACTIVE, never longer than spray_max_ms, never in a dry run. Every time is
// millis(); differences are taken with wrap-around arithmetic.
#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SPRAY_MAX_BLOCK 16  // largest block of the randomization (it must be even)

enum { SPRAY_IDLE = 0, SPRAY_ACTIVE = 1, SPRAY_WINDOW = 2, SPRAY_LOCKOUT = 3 };
// IDLE     no event open; an alarm may open one
// ACTIVE   event open, spray arm, pump on (in a dry run: the time it would be on)
// WINDOW   event open, pump off (the whole window in the no-spray arm)
// LOCKOUT  the window has closed; alarms do not open an event yet

// Why an alarm did not open an event. Decided BEFORE the arm is drawn and never from the
// arm, so the two arms hold the same kind of events.
enum {
  SPRAY_OK = 0,
  SPRAY_NO_EVENT_OPEN = 1,     // an event is open: the alarm belongs to it
  SPRAY_NO_DISARMED = 2,       // switched off by the SPRAY OFF command
  SPRAY_NO_LOCKOUT = 3,        // lock-out after the last window
  SPRAY_NO_FAULT_LATCHED = 4,  // a pump fault ended the last spray: needs SPRAY RESET
  SPRAY_NO_SENSOR = 5,         // a sensor of the row gave no reading
  SPRAY_NO_STORAGE = 6,        // the row or the event counter could not be stored
  SPRAY_NO_LATE = 7,           // the window would run past the logged day
  SPRAY_NO_LOW_LIGHT = 8,      // irradiance below min_g, or unknown
  SPRAY_NO_LOAD = 9,           // a load is fitted and is not holding: no power to compare
  SPRAY_NO_PUMP_SENSOR = 10,   // pump INA226: no valid reading
  SPRAY_NO_TANK_SENSOR = 11,   // no valid tank temperature
  SPRAY_NO_WATER_WARM = 12,    // T_module - T_water below min_dt_water_c
  SPRAY_NO_THERMAL_SHOCK = 13, // T_module - T_water above max_dt_water_c
  SPRAY_NO_BAD_CONFIG = 14,    // the configuration cannot be used (block, times, duty)
};

// Why a spray ended. 1-3 are normal ends; 4-9 are faults (the row after carries
// SPRAY_ST_SPRAY_ABORT); 5, 6 and 9 also latch: no event until spray_reset_fault().
enum {
  SPRAY_STOP_NONE = 0,
  SPRAY_STOP_MAX_TIME = 1,     // spray_max_ms reached
  SPRAY_STOP_TARGET = 2,       // cooled by stop_drop_c since the alarm
  SPRAY_STOP_WATER_WARM = 3,   // T_module - T_water fell below min_dt_water_c
  SPRAY_STOP_SENSOR = 4,       // a sensor of the row (or the tank sensor) gave no reading
  SPRAY_STOP_NO_FLOW = 5,      // pump on, no flow pulse for no_flow_ms
  SPRAY_STOP_OVERCURRENT = 6,  // pump current above i_max_a
  SPRAY_STOP_PUMP_SENSOR = 7,  // pump INA226 failed pump_fail_limit times in a row
  SPRAY_STOP_COMMAND = 8,      // SPRAY OFF typed during the spray
  SPRAY_STOP_HARD_TIMER = 9,   // the independent timer cut the pump: the logic was late
};

// What happened since spray_take_notes() was last called (for the log file).
#define SPRAY_NOTE_OPENED  (1u << 0)  // an event was opened: S.spike_id, S.assign
#define SPRAY_NOTE_REFUSED (1u << 1)  // an alarm did not open one: S.last_reason
#define SPRAY_NOTE_STOPPED (1u << 2)  // a spray ended: S.done
#define SPRAY_NOTE_CLOSED  (1u << 3)  // an event's window closed: S.done

// More bits of the row's `status` column, continuing PvDaq/src/pv_status.h (bits 0-8).
// The analysis keeps the same table in data/schema.py (PHASE_B_STATUS_BITS).
#define SPRAY_ST_PUMP_FAIL     (1u << 9)   // no pump power for this row (P_pump_W empty)
#define SPRAY_ST_SPRAY_BLOCKED (1u << 10)  // an alarm at this row would not open an event
#define SPRAY_ST_SPRAY_ABORT   (1u << 11)  // a spray was stopped by a fault since the last row

typedef struct {
  uint32_t seed;              // of the allocation sequence
  uint32_t block;             // block size: even, 2 .. SPRAY_MAX_BLOCK
  uint32_t dry_run;           // 1 = events and arms as usual, the pump never runs
  uint32_t row_period_ms;     // time between two rows (10 000)
  uint32_t window_ms;         // length of an event window, from the alarm row
  uint32_t lockout_ms;        // after a window: no new event for this long
  float pump_duty;            // PWM duty while spraying, 0-1
  uint32_t spray_max_ms;      // longest spray; must be shorter than the window
  float stop_drop_c;          // stop after this much cooling since the alarm; 0 = not used
  float min_dt_water_c;       // spray only while T_module - T_water >= this
  float max_dt_water_c;       // no event when T_module - T_water > this (thermal shock)
  float min_g;                // W/m2: no event below
  uint32_t need_t_water;      // 1 = the two water limits apply and need a tank reading
  float i_max_a;              // pump over-current stop
  uint32_t no_flow_ms;        // pump on this long without a flow pulse -> stop
  uint32_t pump_sample_ms;    // period of the pump-power readings
  uint32_t pump_fail_limit;   // failed pump readings in a row that stop a spray
  float pulses_per_l;         // flow sensor
} spray_cfg_t;

typedef struct {  // the inputs of one 10 s row
  float t_module;             // mean of T1..T5, degC (NaN if not all five are valid)
  float t_water;              // tank, degC (NaN = no reading)
  float g;                    // irradiance, W/m2 (NaN = no reading)
  uint32_t alarm;             // 1 = NASIM alarmed at this row
  uint32_t row_fault;         // 1 = a sensor of the row gave no reading (RTC, T1..T5,
                              //     ambient, irradiance, panel)
  uint32_t load_ok;           // 1 = no load fitted, or the load holds the module
  uint32_t storage_ok;        // 1 = the row before reached the card and the counter store works
  uint32_t window_fits;       // 1 = a window opened now ends inside the logged day
} spray_row_in_t;

typedef struct {  // the last spray / event that ended (kept until the next one ends)
  int32_t spike_id, assign;
  uint32_t sprayed;           // 1 = the pump ran (dry run: would have run)
  uint32_t stop_reason;
  uint32_t spray_ms;          // pump-on time
  uint32_t pulses;            // flow pulses from the alarm to now
  float energy_j;             // pump energy from the alarm to now
} spray_done_t;

typedef struct {
  spray_cfg_t cfg;
  // ---- output: what the pump PWM must carry now ----
  float duty;
  // ---- state the caller may read ----
  uint32_t mode;              // SPRAY_IDLE ..
  int32_t spike_id;           // open event, 0 = none. Wet run: 1, 2, 3 ...; dry run: -1, -2 ...
  int32_t assign;             // -1 = no event open, 0 = no-spray arm, 1 = spray arm
  uint32_t armed;             // 0 after SPRAY OFF
  uint32_t fault_latched;     // SPRAY_STOP_* that latched, 0 = none
  uint32_t n_wet, n_dry;      // events drawn so far in wet and in dry runs (the caller
                              // stores them where they survive a restart)
  uint32_t commit_pending;    // 1 = store pend_count under pend_dry, then call spray_commit()
  uint32_t pend_count, pend_dry;
  uint32_t last_reason;       // SPRAY_NO_* of the last alarm that opened nothing
  uint32_t blocked;           // SPRAY_NO_* that would stop an alarm now (0 = none, or an
                              // event is open)
  uint32_t pump_ok;           // 1 = the last pump reading was valid
  spray_done_t done;
  // ---- internal ----
  uint32_t notes, abort_flag, sprayed, stop_reason, spray_ms;
  uint32_t t_event_ms, t_row_alarm_ms, t_spray_ms, t_lock_ms, t_last_pulse_ms;
  float t_module_alarm;
  uint32_t t_meter_ms;        // start of the present row interval
  uint32_t t_duty_ms;         // since when `duty` has its value
  float duty_ms;              // integral of duty over the row interval, ms
  uint32_t pulses_total, pulses_row, pulses_event;
  uint32_t pump_pending, pump_fails, pump_have_prev;
  uint32_t t_pump_trig_ms, t_pump_prev_ms;
  float p_prev_w, e_row_j, e_event_j;
  uint32_t t_valid_ms;        // part of the row interval covered by valid pump readings
} spray_t;

// Arm (1 = spray) of event number n (0, 1, 2 ...) of the sequence `seed`. A pure function:
// the same on the board and in analysis/phase_b.py (allocation), so every logged arm can be
// checked afterwards from the seed and the event number alone.
int spray_alloc(uint32_t seed, uint32_t block, uint32_t n);

// Starts in SPRAY_IDLE with the pump off. n_wet, n_dry: the stored event counters.
void spray_init(spray_t *S, const spray_cfg_t *cfg, uint32_t n_wet, uint32_t n_dry, int armed,
                uint32_t now_ms);

// Every loop pass: the time-based stops (longest spray, no flow).
void spray_service(spray_t *S, uint32_t now_ms);
// Flow pulses counted since the last call (0 is fine).
void spray_flow(spray_t *S, uint32_t now_ms, uint32_t pulses);
// 1 when a pump-rail reading must be started now; the reading goes to spray_pump_reading().
int spray_pump_due(spray_t *S, uint32_t now_ms);
// One reading of the pump rail (64-average V and I). ok = 0: it failed.
void spray_pump_reading(spray_t *S, uint32_t now_ms, float v, float i, int ok);

// Every 10 s row, after the detectors: window and lock-out, the stops that depend on the
// row's sensors, and the alarm. May set commit_pending.
void spray_row(spray_t *S, uint32_t now_ms, const spray_row_in_t *in);
// After the caller stored pend_count: stored = 1 opens the event (and starts the pump in
// the spray arm); stored = 0 drops it (SPRAY_NO_STORAGE) and the number is not used.
void spray_commit(spray_t *S, uint32_t now_ms, int stored);
// Closes the row interval. Writes ",spike_id,spray_assign,pump_duty,flow_Lpm,water_L_cum,
// P_pump_W" (leading comma; P_pump_W empty when it could not be measured) and returns the
// SPRAY_ST_* bits of this row. Call exactly once per row, after spray_row / spray_commit.
uint32_t spray_row_fields(spray_t *S, uint32_t now_ms, char *buf, uint32_t size);
// The six column names with a leading comma, as they follow `status` in the header line.
const char *spray_row_header(void);

void spray_arm(spray_t *S, uint32_t now_ms, int on);      // SPRAY ON / SPRAY OFF
void spray_reset_fault(spray_t *S);                       // SPRAY RESET
void spray_hard_stop(spray_t *S, uint32_t now_ms);        // the independent timer fired
uint32_t spray_take_notes(spray_t *S);                    // SPRAY_NOTE_* since the last call

const char *spray_mode_text(uint32_t mode);       // "idle", "spray", "window", "lockout"
const char *spray_reason_text(uint32_t reason);   // SPRAY_NO_*
const char *spray_stop_text(uint32_t reason);     // SPRAY_STOP_*

// For the PC tests (ctypes): sizes and field access without mirroring spray_t.
uint32_t spray_sizeof(void);
uint32_t spray_cfg_sizeof(void);
uint32_t spray_row_in_sizeof(void);
double spray_get(const spray_t *S, const char *field);

#ifdef __cplusplus
}
#endif
