// Common interface of every Phase 0 driver:
//   begin()  detect and configure the device; false if it is not there
//   read()   refresh the readings; false if the read failed
//   status() Ok / NotFound / ReadError (+ detail() text for the log)
// A driver never blocks forever and never crashes when its device is missing:
// the logger simply skips devices whose status is not Ok and retries begin() later.
#pragma once
#include <Arduino.h>

enum class PvStatus : uint8_t { NotStarted, Ok, NotFound, ReadError };

class PvDevice {
 public:
  virtual ~PvDevice() = default;
  virtual bool begin() = 0;
  virtual bool read() = 0;
  virtual const char *name() const = 0;

  PvStatus status() const { return status_; }
  bool ok() const { return status_ == PvStatus::Ok; }
  const char *detail() const { return detail_; }
  const char *statusText() const {
    switch (status_) {
      case PvStatus::Ok: return "ok";
      case PvStatus::NotFound: return "not found";
      case PvStatus::ReadError: return "read error";
      default: return "not started";
    }
  }

 protected:
  bool setStatus(PvStatus s, const char *detail = "") {
    status_ = s;
    snprintf(detail_, sizeof(detail_), "%s", detail);
    return s == PvStatus::Ok;
  }

 private:
  PvStatus status_ = PvStatus::NotStarted;
  char detail_[64] = "";
};
