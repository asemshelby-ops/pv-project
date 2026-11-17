#include "pv_spray.h"

#if PHASE_B_ENABLED

// Flash (NVS) names. The counters must survive a restart: an event number is used once.
static const char *const NVS_SPACE = "phaseb";
static const char *const KEY_WET = "n_wet";
static const char *const KEY_DRY = "n_dry";
static const char *const KEY_ARMED = "armed";

spray_cfg_t pvSprayConfig() {
  spray_cfg_t c = {};
  c.seed = PHASE_B_SEED;
  c.block = PHASE_B_BLOCK_SIZE;
  c.dry_run = PHASE_B_DRY_RUN;
  c.row_period_ms = SAMPLE_PERIOD_MS;
  c.window_ms = PHASE_B_EVENT_WINDOW_MS;
  c.lockout_ms = PHASE_B_LOCKOUT_MS;
  c.pump_duty = PUMP_DUTY;
  c.spray_max_ms = SPRAY_MAX_MS;
  c.stop_drop_c = SPRAY_STOP_DROP_C;
  c.min_dt_water_c = SPRAY_MIN_DT_WATER_C;
  c.max_dt_water_c = SPRAY_MAX_DT_WATER_C;
  c.min_g = SPRAY_MIN_G_WM2;
  c.need_t_water = SPRAY_NEED_T_WATER;
  c.i_max_a = PUMP_I_MAX_A;
  c.no_flow_ms = PUMP_NO_FLOW_MS;
  c.pump_sample_ms = PUMP_SAMPLE_MS;
  c.pump_fail_limit = INA_FAIL_LIMIT;
  c.pulses_per_l = FLOW_PULSES_PER_L;
  return c;
}

// Runs in the esp_timer task when a spray has lasted SPRAY_MAX_MS + SPRAY_HARD_MARGIN_MS:
// the loop should have stopped the pump long before. Switch the PWM off here and tell the
// loop, which then records the fault (it latches: no event until SPRAY RESET).
void PvSpray::hardStop(void *self) {
  PvSpray *p = static_cast<PvSpray *>(self);
  if (p->pwm_ok_) ledcWrite(pins::PUMP_PWM, 0);
  p->hard_fired_ = true;
}

bool PvSpray::beginFlow() {
  pcnt_unit_config_t unit = {};
  unit.low_limit = -1;
  unit.high_limit = FLOW_COUNT_LIMIT;
  if (pcnt_new_unit(&unit, &pcnt_) != ESP_OK) return false;
  pcnt_glitch_filter_config_t filter = {};
  filter.max_glitch_ns = FLOW_GLITCH_NS;
  pcnt_unit_set_glitch_filter(pcnt_, &filter);
  pcnt_chan_config_t chan = {};
  chan.edge_gpio_num = FLOW_PIN;
  chan.level_gpio_num = -1;
  pcnt_channel_handle_t ch = nullptr;
  if (pcnt_new_channel(pcnt_, &chan, &ch) != ESP_OK) return false;
  // one count per rising edge of the sensor's pulse
  pcnt_channel_set_edge_action(ch, PCNT_CHANNEL_EDGE_ACTION_INCREASE,
                               PCNT_CHANNEL_EDGE_ACTION_HOLD);
  return pcnt_unit_enable(pcnt_) == ESP_OK && pcnt_unit_clear_count(pcnt_) == ESP_OK &&
         pcnt_unit_start(pcnt_) == ESP_OK;
}

bool PvSpray::begin() {
  pinMode(pins::PUMP_PWM, OUTPUT);
  digitalWrite(pins::PUMP_PWM, LOW);
  counts_ = 0;
  reading_ = false;
  hard_fired_ = false;

  prefs_ok_ = prefs_.begin(NVS_SPACE, false);
  const uint32_t n_wet = prefs_ok_ ? prefs_.getUInt(KEY_WET, 0) : 0;
  const uint32_t n_dry = prefs_ok_ ? prefs_.getUInt(KEY_DRY, 0) : 0;
  const bool armed = prefs_ok_ ? prefs_.getBool(KEY_ARMED, true) : false;

#if PHASE_B_DRY_RUN
  pwm_ok_ = false;   // never attached: the pin stays LOW for the whole run
  const bool pump_ready = true;
#else
  pwm_ok_ = ledcAttachChannel(pins::PUMP_PWM, PWM_FREQ_HZ, PWM_RES_BITS, PUMP_LEDC_CHANNEL);
  if (pwm_ok_) {
    ledcWrite(pins::PUMP_PWM, 0);
  } else {  // no PWM: hold the gate low for good
    ledcDetach(pins::PUMP_PWM);
    pinMode(pins::PUMP_PWM, OUTPUT);
    digitalWrite(pins::PUMP_PWM, LOW);
  }
  esp_timer_create_args_t args = {};
  args.callback = &PvSpray::hardStop;
  args.arg = this;
  args.name = "spray_cut";
  const bool timer_ok = esp_timer_create(&args, &timer_) == ESP_OK;
  const bool pump_ready = pwm_ok_ && timer_ok;
#endif
  flow_ok_ = beginFlow();
  if (flow_ok_) pcnt_unit_get_count(pcnt_, &flow_last_);

  init_ok_ = prefs_ok_ && pump_ready && flow_ok_;
  snprintf(detail_, sizeof(detail_), "%s%s%s", prefs_ok_ ? "" : "event counter store (NVS) failed; ",
           pump_ready ? "" : "pump PWM or cut-off timer failed; ",
           flow_ok_ ? "" : "flow counter failed; ");
  const spray_cfg_t cfg = pvSprayConfig();
  spray_init(&S, &cfg, n_wet, n_dry, armed && init_ok_, millis());
  return init_ok_;
}

void PvSpray::write() {
  if (!pwm_ok_) return;   // dry run, or no PWM: the pin is LOW
  uint32_t counts = 0;
  const uint32_t top = (1u << PWM_RES_BITS) - 1u;
  if (S.duty >= 1.0f) counts = top;
  else if (S.duty > 0.0f) counts = (uint32_t)(S.duty * (float)top + 0.5f);
  if (counts == counts_) return;
  ledcWrite(pins::PUMP_PWM, counts);
  if (timer_) {
    if (counts && !counts_) {
      esp_timer_stop(timer_);
      esp_timer_start_once(timer_, (uint64_t)(SPRAY_MAX_MS + SPRAY_HARD_MARGIN_MS) * 1000ULL);
    } else if (!counts) {
      esp_timer_stop(timer_);
    }
  }
  counts_ = counts;
}

// One 64-average reading of the pump rail, without blocking: trigger, look again when the
// conversion can be over, give the result (or the failure) to the logic.
void PvSpray::pollPump() {
  const uint32_t now = millis();
  if (reading_) {
    if (now - t_trig_ms_ < PUMP_CONV_MS) return;
    if (ina_.ready()) {
      const bool ok = ina_.fetch();
      reading_ = false;
      spray_pump_reading(&S, millis(), ina_.busV, ina_.currentA, ok ? 1 : 0);
    } else if (now - t_trig_ms_ > PUMP_READ_TIMEOUT_MS) {
      reading_ = false;
      spray_pump_reading(&S, now, NAN, NAN, 0);
    }
    return;
  }
  if (!spray_pump_due(&S, now)) return;
  if (ina_.trigger(INA226_64_SAMPLES)) {
    reading_ = true;
    t_trig_ms_ = now;
  } else {  // not found, or lost on the bus: the logger retries begin() every minute
    spray_pump_reading(&S, now, NAN, NAN, 0);
  }
}

void PvSpray::service() {
  const uint32_t now = millis();
  if (hard_fired_) {  // the independent timer cut the pump: record it
    hard_fired_ = false;
    counts_ = 0;
    spray_hard_stop(&S, now);
  }
  int count = 0;
  if (flow_ok_ && pcnt_unit_get_count(pcnt_, &count) == ESP_OK) {
    int pulses = count - flow_last_;
    if (pulses < 0) pulses += FLOW_COUNT_LIMIT;  // the counter started again at 0
    flow_last_ = count;
    spray_flow(&S, now, (uint32_t)pulses);
  }
  spray_service(&S, now);
  write();
  pollPump();
  write();  // a pump reading may have ended the spray (over-current)
}

void PvSpray::row(const spray_row_in_t &in) {
  spray_row(&S, millis(), &in);
  if (S.commit_pending) {  // the number is stored BEFORE the event opens and the pump starts
    const bool stored =
        prefs_ok_ && prefs_.putUInt(S.pend_dry ? KEY_DRY : KEY_WET, S.pend_count) == sizeof(uint32_t);
    spray_commit(&S, millis(), stored ? 1 : 0);
  }
  write();
}

void PvSpray::arm(bool on) {
  spray_arm(&S, millis(), on && init_ok_ ? 1 : 0);
  write();
  if (prefs_ok_) prefs_.putBool(KEY_ARMED, on);
}

#endif  // PHASE_B_ENABLED
