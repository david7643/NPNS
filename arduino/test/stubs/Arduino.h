// 호스트 테스트용 최소 Arduino API. 시간·시리얼·핀 상태를 테스트가 직접 조작한다.
#pragma once
#include <stdint.h>
#include <string.h>
#include <string>
#include <deque>
#include <vector>

typedef uint8_t byte;
#define OUTPUT 1
#define LOW 0
#define HIGH 1

namespace sim {
extern uint32_t now_ms;
extern uint32_t tick_per_call;   // millis()가 불릴 때마다 흐르는 시간 (경계 넘김 재현용)
extern int pins[20];
extern int tone_hz;
}

uint32_t millis();
void pinMode(int, int);
void digitalWrite(int pin, int value);
void tone(int pin, unsigned int hz);
void noTone(int pin);

class HardwareSerialSim {
 public:
  std::deque<char> rx;
  std::string tx;
  void begin(long) {}
  int available() { return (int)rx.size(); }
  int read() { int c = rx.front(); rx.pop_front(); return c; }
  void print(const char *s) { tx += s; }
  void println(const char *s) { tx += s; tx += "\n"; }
  void println(int v) { tx += std::to_string(v) + "\n"; }
  void feed(const std::string &s) { for (char c : s) rx.push_back(c); }
};
extern HardwareSerialSim Serial;
