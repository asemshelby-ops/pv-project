// HOST STUB (see Arduino.h in this folder). ESP-IDF 5.x task watchdog.
#pragma once
#include <stdint.h>
#include "esp_err.h"
typedef struct {
  uint32_t timeout_ms;
  uint32_t idle_core_mask;
  bool trigger_panic;
} esp_task_wdt_config_t;
esp_err_t esp_task_wdt_init(const esp_task_wdt_config_t *config);
esp_err_t esp_task_wdt_reconfigure(const esp_task_wdt_config_t *config);
