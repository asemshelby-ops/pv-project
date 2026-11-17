// HOST STUB, not the Arduino core. Just enough declarations of the ESP32 Arduino core 3.x
// API for g++ -fsyntax-only to type-check the project's own firmware sources on a PC
// (tests/test_firmware_host.py). The signatures are written from the core's documentation
// as understood on 2026-11-17; nothing here is executed. The arduino-cli build
// (firmware/build_all.py) is the authority; this only catches typos and type errors on a
// machine without the ESP32 toolchain.
#pragma once
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

#define IRAM_ATTR
#define OUTPUT 0x03
#define INPUT_PULLUP 0x05
#define LOW 0
#define HIGH 1
#define FALLING 2
#define SERIAL_8N1 0x800001c
#define constrain(amt, low, high) ((amt) < (low) ? (low) : ((amt) > (high) ? (high) : (amt)))
using std::isnan;

uint32_t millis();
uint32_t micros();
void delay(uint32_t ms);
void delayMicroseconds(uint32_t us);
void pinMode(uint8_t pin, uint8_t mode);
void digitalWrite(uint8_t pin, uint8_t value);
int digitalPinToInterrupt(int pin);
void attachInterrupt(uint8_t pin, void (*isr)(), int mode);
bool ledcAttachChannel(uint8_t pin, uint32_t freq, uint8_t resolution, uint8_t channel);
bool ledcWrite(uint8_t pin, uint32_t duty);
bool ledcDetach(uint8_t pin);
void enableLoopWDT();
unsigned uxTaskGetStackHighWaterMark(void *task);

class String {
 public:
  String(const char *s = "");
  String(const String &s);
  String(char c);
  String(int v, unsigned char base = 10);
  String(unsigned int v, unsigned char base = 10);
  String(long v, unsigned char base = 10);
  String(unsigned long v, unsigned char base = 10);
  String(float v, unsigned int decimals = 2);
  String(double v, unsigned int decimals = 2);
  String &operator=(const String &s);
  String &operator+=(const String &s);
  String &operator+=(const char *s);
  String &operator+=(char c);
  String &operator+=(int v);
  String &operator+=(unsigned int v);
  String &operator+=(long v);
  String &operator+=(unsigned long v);
  bool operator==(const String &s) const;
  bool operator!=(const String &s) const;
  const char *c_str() const;
  unsigned length() const;
  bool reserve(unsigned n);
  void trim();
};
String operator+(const String &a, const String &b);
String operator+(const String &a, const char *b);

class Print {
 public:
  size_t printf(const char *format, ...) __attribute__((format(printf, 2, 3)));
  size_t print(const char *s);
  size_t print(const String &s);
  size_t print(char c);
  size_t print(int v);
  size_t println(const char *s);
  size_t println(const String &s);
  size_t println(unsigned long v);
  size_t println();
};

class Stream : public Print {
 public:
  int available();
  int read();
  void flush();
  String readStringUntil(char terminator);
};

class HardwareSerial : public Stream {
 public:
  void begin(unsigned long baud, uint32_t config = SERIAL_8N1, int8_t rx = -1, int8_t tx = -1);
  size_t setRxBufferSize(size_t n);
};
extern HardwareSerial Serial;
extern HardwareSerial Serial2;

class EspClass {
 public:
  uint32_t getFreeHeap();
  uint32_t getMinFreeHeap();
};
extern EspClass ESP;
