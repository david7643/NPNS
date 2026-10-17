#pragma once
#include "Arduino.h"

// CO2 센서 대역. requests는 받은 요청 수, rx는 센서 응답 바이트.
class SoftwareSerial {
 public:
  SoftwareSerial(int, int) {}
  void begin(long) {}
  int available() { return (int)rx.size(); }
  int read() { int c = rx.front(); rx.pop_front(); return c; }
  size_t write(const byte *, size_t n) { requests++; return n; }
  std::deque<byte> rx;
  int requests = 0;
};
