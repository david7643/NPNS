// DrowsyGuard 아두이노 경보기 (Arduino Uno)
// MH-Z19E(D10,D11) + 청색 LED(D7) + 부저(D8) + 서보 SG90(D9, 창문)
// 배선과 동작 규칙은 arduino/README.md 참고.
//
// 1) 졸음 경보: 서버(USB 시리얼, 9600bps)의 명령으로만 켜고 끈다.
//    "A1" = 경보 켜기 (LED 깜빡임 + 부저), "A0" = 경보 끄기.
//    정확히 A0·A1만 받으며 처리한 명령마다 "ACK:A0"/"ACK:A1"로 응답한다.
//    그 밖의 줄은 상태를 바꾸지 않고 "ERR:CMD"로 응답한다.
//    서버 명령이 끊겨도 마지막 상태를 유지한다. 경보는 서버의 A0
//    (앱 중지 확인, 감지 종료)나 재부팅으로만 꺼진다.
// 2) 창문: 새로 받은 유효 CO2가 2000ppm 이상이면 열고, 1000ppm 미만이면 닫는다.
//    센서 값이 10초 넘게 없으면 창문을 그대로 두고 "CO2:NA"를 보낸다.
// 3) 켜질 때 "BOOT", CO2는 "CO2:850" 형식으로 서버에 보낸다.

#include <SoftwareSerial.h>
#include <Servo.h>

// ----- 핀 -----
const int LED_PIN    = 7;
const int BUZZER_PIN = 8;
const int SERVO_PIN  = 9;
const int CO2_RX_PIN = 10;  // 센서 TX 연결
const int CO2_TX_PIN = 11;  // 센서 RX 연결

// ----- CO2 -----
const int CO2_MIN_VALID = 300;     // 이 범위를 벗어난 값은 오류로 버린다
const int CO2_MAX_VALID = 10000;
const uint32_t CO2_REQUEST_MS = 2000;
const uint32_t CO2_STALE_MS   = 10000;

// ----- 창문 -----
const int WINDOW_OPEN_PPM  = 2000;  // 이 이상이면 창문 열기 (졸음 유발 수준)
const int WINDOW_CLOSE_PPM = 1000;  // 이 아래로 내려가면 창문 닫기
const int WINDOW_CLOSED_ANGLE = 0;  // 실제 창문 모형의 허용 범위에 맞게 조정
const int WINDOW_OPEN_ANGLE   = 180;
const uint32_t SERVO_MOVE_MS = 800;  // 끝까지 도는 시간. 다 못 돌면 늘리기

// ----- 경보 -----
const int BUZZER_HZ = 400;          // 낮을수록 작게 들림
const uint32_t BLINK_MS = 300;

SoftwareSerial co2Serial(CO2_RX_PIN, CO2_TX_PIN);
Servo windowServo;

const byte READ_CMD[9] = {0xFF, 0x01, 0x86, 0x00, 0x00, 0x00, 0x00, 0x00, 0x79};

int  co2ppm = 0;            // 0 = 유효한 측정값 없음
bool co2Fresh = false;      // 창문 판단에 아직 쓰지 않은 새 측정값
uint32_t lastValidCo2 = 0;
uint32_t lastRequest  = 0;

bool alarmOn  = false;
bool ledState = false;
uint32_t lastBlink = 0;

bool windowOpen  = false;
bool servoMoving = false;
uint32_t servoMovedAt = 0;

char cmdBuf[8];
byte cmdLen = 0;
bool cmdOverflow = false;

void moveWindow(int angle) {
  windowServo.attach(SERVO_PIN);
  windowServo.write(angle);
  servoMoving = true;
  servoMovedAt = millis();
}

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
  co2Fresh = true;
  lastValidCo2 = millis();
  Serial.print("CO2:");
  Serial.println(co2ppm);
}

void expireCO2() {
  if (co2ppm > 0 && millis() - lastValidCo2 >= CO2_STALE_MS) {
    co2ppm = 0;
    co2Fresh = false;
    Serial.println("CO2:NA");
  }
}

// 새로 받은 유효 측정값으로만 판단한다. 값이 없으면 창문을 그대로 둔다.
void updateWindow() {
  if (!co2Fresh) return;
  co2Fresh = false;
  if (!windowOpen && co2ppm >= WINDOW_OPEN_PPM) {
    windowOpen = true;
    moveWindow(WINDOW_OPEN_ANGLE);
  } else if (windowOpen && co2ppm < WINDOW_CLOSE_PPM) {
    windowOpen = false;
    moveWindow(WINDOW_CLOSED_ANGLE);
  }
}

void setup() {
  Serial.begin(9600);
  co2Serial.begin(9600);

  pinMode(LED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  moveWindow(WINDOW_CLOSED_ANGLE);
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
  updateWindow();

  if (servoMoving && millis() - servoMovedAt >= SERVO_MOVE_MS) {
    windowServo.detach();
    servoMoving = false;
  }

  if (alarmOn && millis() - lastBlink >= BLINK_MS) {
    lastBlink = millis();
    ledState = !ledState;
    digitalWrite(LED_PIN, ledState);
  }
}
