// HOST STUB (see Arduino.h in this folder).
#pragma once
#include <Arduino.h>
#include <SPI.h>
#define FILE_READ "r"
#define FILE_APPEND "a"
enum { CARD_NONE, CARD_MMC, CARD_SD, CARD_SDHC };
class File : public Stream {
 public:
  explicit operator bool() const;
  void close();
};
class SDFS {
 public:
  bool begin(uint8_t cs, SPIClass &spi, uint32_t hz, const char *mount, uint8_t max_files,
             bool format_if_empty);
  void end();
  bool exists(const char *path);
  File open(const char *path, const char *mode = FILE_READ);
  uint8_t cardType();
  uint64_t cardSize();
};
extern SDFS SD;
