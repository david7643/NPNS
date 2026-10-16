// drowsyguard_alarm.ino 호스트 테스트 (보드 없이 실행). 실행: bash arduino/test/run.sh
#include "Arduino.h"

namespace sim {
uint32_t now_ms = 0;
uint32_t tick_per_call = 0;
int pins[20] = {0};
int tone_hz = 0;
}
uint32_t millis() { uint32_t t = sim::now_ms; sim::now_ms += sim::tick_per_call; return t; }
void pinMode(int, int) {}
void digitalWrite(int pin, int value) { sim::pins[pin] = value; }
void tone(int, unsigned int hz) { sim::tone_hz = (int)hz; }
void noTone(int) { sim::tone_hz = 0; }
HardwareSerialSim Serial;

#include "../drowsyguard_alarm/drowsyguard_alarm.ino"

#include <cstdio>

static int failures = 0;
#define CHECK(cond) do { if (!(cond)) { std::printf("  FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); failures++; } } while (0)

static bool has(const std::string &line) { return Serial.tx.find(line + "\n") != std::string::npos; }
static int count(const std::string &line) {
  int n = 0; size_t pos = 0; std::string needle = line + "\n";
  while ((pos = Serial.tx.find(needle, pos)) != std::string::npos) { n++; pos += needle.size(); }
  return n;
}

// 스케치 전역 상태를 처음 전원을 켠 것처럼 되돌리고 setup()을 실행한다.
static void boot(uint32_t start_ms = 0) {
  sim::now_ms = start_ms; sim::tick_per_call = 0; sim::tone_hz = 0;
  for (int &p : sim::pins) p = 0;
  Serial.rx.clear(); Serial.tx.clear();
  co2Serial.rx.clear(); co2Serial.requests = 0;
  windowServo = Servo();
  co2ppm = 0; co2Fresh = false; lastValidCo2 = start_ms; lastRequest = start_ms;
  alarmOn = false; ledState = false; lastBlink = start_ms;
  windowOpen = false; servoMoving = false; servoMovedAt = start_ms;
  cmdLen = 0; cmdOverflow = false;
  setup();
}

static void run_for(uint32_t ms, uint32_t step = 10) {
  uint32_t end = sim::now_ms + ms;
  while ((int32_t)(end - sim::now_ms) > 0) { loop(); sim::now_ms += step; }
}

// ms 동안 실행하며 LED가 바뀐 횟수를 센다.
static int led_changes_during(uint32_t ms, uint32_t step = 10) {
  int changes = 0, last = sim::pins[LED_PIN];
  uint32_t end = sim::now_ms + ms;
  while ((int32_t)(end - sim::now_ms) > 0) {
    loop();
    if (sim::pins[LED_PIN] != last) { changes++; last = sim::pins[LED_PIN]; }
    sim::now_ms += step;
  }
  return changes;
}

static void sensor_reply(int ppm) {
  byte r[9] = {0xFF, 0x86, (byte)(ppm >> 8), (byte)(ppm & 0xFF), 0, 0, 0, 0, 0};
  byte sum = 0;
  for (int i = 1; i < 8; i++) sum += r[i];
  r[8] = (byte)(0xFF - sum + 1);
  for (byte b : r) co2Serial.rx.push_back(b);
}

static void test_boot_message() {
  std::puts("boot prints BOOT and closes window");
  boot();
  CHECK(has("BOOT"));
  CHECK(windowServo.angle == WINDOW_CLOSED_ANGLE);
}

static void test_a1_stays_on_when_clock_ticks_during_command() {
  std::puts("A1 is not cancelled when millis() moves while the command is read (review P1)");
  boot(1000);
  sim::tick_per_call = 1;           // millis()를 부를 때마다 1ms씩 흐름
  Serial.feed("A1\n");
  for (int i = 0; i < 200; i++) loop();
  CHECK(alarmOn);
  CHECK(sim::tone_hz == BUZZER_HZ);
  CHECK(count("ACK:A1") == 1);
}

static void test_alarm_kept_without_further_commands() {
  std::puts("alarm stays on with no further commands (keep policy)");
  boot();
  Serial.feed("A1\n");
  run_for(10000);
  CHECK(alarmOn);
  Serial.feed("A0\n");
  run_for(50);
  CHECK(!alarmOn);
  CHECK(sim::tone_hz == 0);
  CHECK(sim::pins[LED_PIN] == LOW);
  CHECK(has("ACK:A0"));
}

static void test_led_blinks_while_alarm_on() {
  std::puts("LED toggles while alarm is on");
  boot();
  Serial.feed("A1\n");
  run_for(10);
  int changes = led_changes_during(1000);
  CHECK(changes >= 3 && changes <= 4);   // 300ms 간격
}

static void test_invalid_commands_rejected_without_state_change() {
  std::puts("only exact A0/A1 accepted; others answer ERR:CMD and change nothing");
  boot();
  Serial.feed("A1\n");
  run_for(20);
  Serial.feed("A2\nAA\nA1x\nA0 \nA1234567890\n");
  run_for(20);
  CHECK(alarmOn);
  CHECK(count("ERR:CMD") == 5);
  CHECK(count("ACK:A1") == 1);
  CHECK(!has("ACK:A0"));
}

static void test_crlf_and_blank_lines() {
  std::puts("CRLF line endings and blank lines are handled");
  boot();
  Serial.feed("\r\n\nA1\r\n");
  run_for(20);
  CHECK(alarmOn);
  CHECK(count("ACK:A1") == 1);
  CHECK(!has("ERR:CMD"));
}

static void test_valid_co2_opens_window_and_reports() {
  std::puts("valid CO2 above threshold opens window, servo detaches after move");
  boot();
  run_for(CO2_REQUEST_MS + 10);
  CHECK(co2Serial.requests >= 1);
  sensor_reply(2500);
  run_for(20);
  CHECK(has("CO2:2500"));
  CHECK(windowOpen);
  CHECK(windowServo.angle == WINDOW_OPEN_ANGLE);
  CHECK(windowServo.attached);
  run_for(SERVO_MOVE_MS + 20);
  CHECK(!windowServo.attached);
}

static void test_out_of_range_co2_ignored() {
  std::puts("out-of-range CO2 and bad checksum are ignored");
  boot();
  sensor_reply(100);
  run_for(20);
  sensor_reply(20000);
  run_for(20);
  for (byte b : {0xFF, 0x86, 0x09, 0xC4, 0, 0, 0, 0, 0x00}) co2Serial.rx.push_back(b);  // 체크섬 오류
  run_for(20);
  CHECK(co2ppm == 0);
  CHECK(!windowOpen);
  CHECK(Serial.tx.find("CO2:") == std::string::npos);
}

static void test_stale_co2_reports_na_and_holds_window() {
  std::puts("CO2 expires after 10 s: reports CO2:NA and window holds position");
  boot();
  sensor_reply(2500);
  run_for(20);
  CHECK(windowOpen);
  int writes = windowServo.writes;
  run_for(CO2_STALE_MS + 100);
  CHECK(has("CO2:NA"));
  CHECK(co2ppm == 0);
  CHECK(windowOpen);
  CHECK(windowServo.writes == writes);
}

static void test_window_closes_below_threshold() {
  std::puts("window closes only when a fresh reading is below the close threshold");
  boot();
  sensor_reply(2500);
  run_for(20);
  sensor_reply(1500);
  run_for(20);
  CHECK(windowOpen);
  sensor_reply(900);
  run_for(20);
  CHECK(!windowOpen);
  CHECK(windowServo.angle == WINDOW_CLOSED_ANGLE);
}

static void test_millis_wraparound() {
  std::puts("timers keep working across millis() wraparound");
  boot(0xFFFFFFFFu - 500);
  Serial.feed("A1\n");
  run_for(600);                      // 0을 지나간 시점
  CHECK(sim::now_ms < 1000);
  CHECK(alarmOn);
  int changes = led_changes_during(1000);
  CHECK(changes >= 3 && changes <= 4);
  run_for(CO2_REQUEST_MS);           // 랩어라운드 뒤에도 2초마다 센서 요청
  CHECK(co2Serial.requests >= 1);
  sensor_reply(2500);
  run_for(20);
  CHECK(windowOpen);
  run_for(SERVO_MOVE_MS + 20);
  CHECK(!windowServo.attached);
}

int main() {
  test_boot_message();
  test_a1_stays_on_when_clock_ticks_during_command();
  test_alarm_kept_without_further_commands();
  test_led_blinks_while_alarm_on();
  test_invalid_commands_rejected_without_state_change();
  test_crlf_and_blank_lines();
  test_valid_co2_opens_window_and_reports();
  test_out_of_range_co2_ignored();
  test_stale_co2_reports_na_and_holds_window();
  test_window_closes_below_threshold();
  test_millis_wraparound();
  if (failures) { std::printf("%d check(s) failed\n", failures); return 1; }
  std::puts("ALL FIRMWARE TESTS PASSED");
  return 0;
}
