// PYR20 Modbus RTU test (MAX3485 on UART2, 9600 8N1, address 1).
// 1. CRC self-test against the example frames printed in the INFWIN PYR20 manual.
// 2. Reads the sensor's settings registers 0x0200-0x0207 once (FC 3).
// 3. Every 2 s reads irradiance register 0x0000 with FC 4 and with FC 3, printing
//    the raw request and reply frames and the CRC check.
#include <PvDaq.h>

PvPyr20Modbus pyr(Serial2, pins::RS485_RX, pins::RS485_TX);

// Example frames from the manual, without their last two bytes (the CRC).
struct CrcCase {
  uint8_t frame[8];
  uint8_t len;
  uint8_t crc_lo, crc_hi;
};
const CrcCase CASES[] = {
    {{0x01, 0x04, 0x00, 0x00, 0x00, 0x01}, 6, 0x31, 0xCA},  // FC4 request
    {{0x01, 0x04, 0x02, 0x00, 0x10}, 5, 0xB8, 0xFC},        // FC4 reply (SR = 16)
    {{0x01, 0x03, 0x02, 0x00, 0x00, 0x02}, 6, 0xC5, 0xB3},  // FC3 request
};

void crcSelfTest() {
  Serial.println("CRC self-test (manual example frames):");
  for (const CrcCase &c : CASES) {
    uint16_t crc = PvPyr20Modbus::crc16(c.frame, c.len);
    bool pass = (crc & 0xFF) == c.crc_lo && (crc >> 8) == c.crc_hi;
    Serial.print("  ");
    pvPrintHex(Serial, c.frame, c.len);
    Serial.printf("  -> %02X %02X  expected %02X %02X  %s\n", crc & 0xFF, crc >> 8, c.crc_lo,
                  c.crc_hi, pass ? "PASS" : "FAIL");
  }
}

void printFrames() {
  Serial.print("    TX: ");
  pvPrintHex(Serial, pyr.tx, pyr.txLen);
  Serial.print("\n    RX: ");
  if (pyr.rxLen) {
    pvPrintHex(Serial, pyr.rx, pyr.rxLen);
    if (pyr.rxLen >= 2) {
      uint16_t calc = PvPyr20Modbus::crc16(pyr.rx, pyr.rxLen - 2);
      Serial.printf("   (CRC received %02X %02X, computed %02X %02X)", pyr.rx[pyr.rxLen - 2],
                    pyr.rx[pyr.rxLen - 1], calc & 0xFF, calc >> 8);
    }
    if (pyr.echoRemoved) Serial.print("  [echo of the request removed]");
  } else {
    Serial.print("(nothing)");
  }
  Serial.println();
}

void setup() {
  pvOutputsOff();
  Serial.begin(115200);
  delay(300);
  Serial.println("\nPYR20 Modbus test");
  crcSelfTest();
  pyr.begin();
  Serial.printf("begin: %s %s\n", pyr.statusText(), pyr.detail());
  printFrames();

  uint16_t cfg[8];
  if (pyr.readRegisters(0x03, 0x0200, 8, cfg)) {
    Serial.printf("settings: address %u, baud code %u (3 = 9600), protocol %u, parity %u, "
                  "data bits code %u, stop bits code %u, response delay %u x 10 ms, "
                  "active output %u s\n",
                  cfg[0], cfg[1], cfg[2], cfg[3], cfg[4], cfg[5], cfg[6], cfg[7]);
  } else {
    Serial.printf("settings read: %s (%s)\n", pyr.statusText(), pyr.detail());
  }
  printFrames();
}

void readWith(uint8_t fc) {
  uint16_t v = 0;
  bool good = pyr.readRegisters(fc, PYR20_REG_SR, 1, &v);
  if (good) {
    Serial.printf("  FC %u: SR raw %u -> %u W/m2  [ok]\n", fc, v, v);
  } else {
    Serial.printf("  FC %u: %s (%s)\n", fc, pyr.statusText(), pyr.detail());
  }
  printFrames();
}

void loop() {
  delay(2000);
  Serial.printf("t = %lu ms\n", (unsigned long)millis());
  readWith(0x04);
  readWith(0x03);
}
