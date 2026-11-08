// DrowsyGuard 아두이노 경보기 (Arduino Uno)
// MH-Z19E(D10,D11) + 청색 LED(D7) + 부저(D8)
// 배선과 동작 규칙은 arduino/README.md 참고.
//
// 1) 졸음 경보: 서버(USB 시리얼, 9600bps)의 명령으로만 켜고 끈다.
//    "A1" = 경보 켜기 (LED 깜빡임 + 부저), "A0" = 경보 끄기.
//    정확히 A0·A1만 받으며 처리한 명령마다 "ACK:A0"/"ACK:A1"로 응답한다.
//    그 밖의 줄은 상태를 바꾸지 않고 "ERR:CMD"로 응답한다.
//    서버 명령이 끊겨도 마지막 상태를 유지한다. 경보는 서버의 A0
//    (앱 중지 확인, 감지 종료)나 재부팅으로만 꺼진다.
// 2) 서보 제어는 사용하지 않는다.
// 3) 켜질 때 "BOOT", CO2는 "CO2:850" 형식으로 서버에 보낸다.

#include <SoftwareSerial.h>

// ----- 핀 -----
const int LED_PIN    = 7;
const int BUZZER_PIN = 8;
const int CO2_RX_PIN = 10;  // 센서 TX 연결
const int CO2_TX_PIN = 11;  // 센서 RX 연결

// ----- CO2 -----
const int CO2_MIN_VALID = 300;     // 이 범위를 벗어난 값은 오류로 버린다
const int CO2_MAX_VALID = 10000;
const uint32_t CO2_REQUEST_MS = 2000;
const uint32_t CO2_STALE_MS   = 10000;

// ----- 경보 -----
const int BUZZER_HZ = 400;          // 낮을수록 작게 들림
const uint32_t BLINK_MS = 300;

SoftwareSerial co2Serial(CO2_RX_PIN, CO2_TX_PIN);

const byte READ_CMD[9] = {0xFF, 0x01, 0x86, 0x00, 0x00, 0x00, 0x00, 0x00, 0x79};

int  co2ppm = 0;            // 0 = 유효한 측정값 없음
uint32_t lastValidCo2 = 0;
uint32_t lastRequest  = 0;

bool alarmOn  = false;
bool ledState = false;
uint32_t lastBlink = 0;

char cmdBuf[8];
byte cmdLen = 0;
bool cmdOverflow = false;

void setAlarm(bool on) {
  if (on == alarmOn) return;
  alarmOn = on;
  if (on) {
    tone(BUZZER_PIN, BUZZER_HZ);
    ledState = true;
    digitalWrite(LED_PIN, HIGH);
    lastBlink = millis();
  } else {
    noTone(BUZZER_PIN);
    ledState = false;
    digitalWrite(LED_PIN, LOW);
  }
}

void handleCommand(const char *cmd) {
  if (strcmp(cmd, "A1") == 0) {
    setAlarm(true);
    Serial.println("ACK:A1");
  } else if (strcmp(cmd, "A0") == 0) {
    setAlarm(false);
    Serial.println("ACK:A0");
  } else {
    Serial.println("ERR:CMD");  // 상태는 바꾸지 않는다
  }
}

// 서버 명령 읽기. 한 줄 = 한 명령 ("A1\n"). 빈 줄은 무시.
void readCommands() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (cmdOverflow) {
        Serial.println("ERR:CMD");
      } else if (cmdLen > 0) {
        cmdBuf[cmdLen] = '\0';
        handleCommand(cmdBuf);
      }
      cmdLen = 0;
      cmdOverflow = false;
    } else if (cmdLen < sizeof(cmdBuf) - 1) {
      cmdBuf[cmdLen++] = c;
    } else {
      cmdOverflow = true;  // 너무 긴 줄은 줄 끝까지 버리고 오류로 응답
    }
  }
}

void requestCO2() {
  while (co2Serial.available()) co2Serial.read();
  co2Serial.write(READ_CMD, 9);
}

void readCO2() {
  if (co2Serial.available() < 9) return;

  byte r[9];
  for (int i = 0; i < 9; i++) r[i] = co2Serial.read();
  if (r[0] != 0xFF || r[1] != 0x86) return;

  byte sum = 0;
  for (int i = 1; i < 8; i++) sum += r[i];
  if ((byte)(0xFF - sum + 1) != r[8]) return;

  int value = r[2] * 256 + r[3];
  if (value < CO2_MIN_VALID || value > CO2_MAX_VALID) return;  // 오류값은 유효기간을 늘리지 않음

  co2ppm = value;
  lastValidCo2 = millis();
  Serial.print("CO2:");
  Serial.println(co2ppm);
}

void expireCO2() {
  if (co2ppm > 0 && millis() - lastValidCo2 >= CO2_STALE_MS) {
    co2ppm = 0;
    Serial.println("CO2:NA");
  }
}

void setup() {
  Serial.begin(9600);
  co2Serial.begin(9600);

  pinMode(LED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  Serial.println("BOOT");
}

void loop() {
  readCommands();

  // 시간 비교는 항상 그 순간의 millis()로 한다 (부호 없는 뺄셈은 오버플로에도 안전)
  if (millis() - lastRequest >= CO2_REQUEST_MS) {
    lastRequest = millis();
    requestCO2();
  }
  readCO2();
  expireCO2();
  if (alarmOn && millis() - lastBlink >= BLINK_MS) {
    lastBlink = millis();
    ledState = !ledState;
    digitalWrite(LED_PIN, ledState);
  }
}
