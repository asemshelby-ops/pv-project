#include "pv_flow.h"

bool PvFlow::begin() {
  if (chan_) pcnt_del_channel(chan_);
  if (unit_) {
    pcnt_unit_stop(unit_);
    pcnt_unit_disable(unit_);
    pcnt_del_unit(unit_);
  }
  unit_ = nullptr;
  chan_ = nullptr;
  pinMode(pin_, INPUT);
  pcnt_unit_config_t ucfg = {};
  ucfg.low_limit = -1;  // never reached: the counter only counts up
  ucfg.high_limit = LIMIT;
  if (pcnt_new_unit(&ucfg, &unit_) != ESP_OK) {
    unit_ = nullptr;
    return setStatus(PvStatus::NotFound, "no free PCNT unit");
  }
  // Pulses shorter than this are noise, not the sensor (its pulses are milliseconds long).
  pcnt_glitch_filter_config_t filter = {};
  filter.max_glitch_ns = FLOW_GLITCH_NS;
  const bool filtered = pcnt_unit_set_glitch_filter(unit_, &filter) == ESP_OK;
  pcnt_chan_config_t ccfg = {};
  ccfg.edge_gpio_num = pin_;
  ccfg.level_gpio_num = -1;
  if (pcnt_new_channel(unit_, &ccfg, &chan_) != ESP_OK ||
      pcnt_channel_set_edge_action(chan_, PCNT_CHANNEL_EDGE_ACTION_INCREASE,
                                   PCNT_CHANNEL_EDGE_ACTION_HOLD) != ESP_OK ||
      pcnt_unit_enable(unit_) != ESP_OK || pcnt_unit_clear_count(unit_) != ESP_OK ||
      pcnt_unit_start(unit_) != ESP_OK) {
    return setStatus(PvStatus::ReadError, "PCNT set-up failed");
  }
  last_count_ = 0;
  last_ms_ = millis();
  pulses = totalPulses = intervalMs = 0;
  litresPerMin = NAN;
  return setStatus(PvStatus::Ok, filtered ? "" : "no glitch filter");
}

bool PvFlow::read() {
  int count = 0;
  if (!unit_ || pcnt_unit_get_count(unit_, &count) != ESP_OK) {
    pulses = 0;
    litresPerMin = NAN;
    return setStatus(PvStatus::ReadError, "PCNT not readable");
  }
  const uint32_t now = millis();
  pulses = flow_pulses_since(count, last_count_, LIMIT);
  intervalMs = now - last_ms_;
  totalPulses += pulses;
  litresPerMin = flow_l_per_min(pulses, intervalMs, FLOW_PULSES_PER_L);
  last_count_ = count;
  last_ms_ = now;
  return setStatus(PvStatus::Ok);
}
