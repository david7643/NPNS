// DrowsyGuard 아두이노 경보기
// MH-Z19E(D10,D11) + 청색 LED(D7) + 부저(D8) + 서보 SG90(D9, 창문)
//
// 1) 졸음 경보: 서버(USB 시리얼)가 보내는 명령으로 동작
//    "A1" = 2·3단계 경보 켜기 (LED 깜빡임 + 부저)
//    "A0" = 경보 끄기
//    3초 동안 명령이 없으면 서버가 꺼진 것으로 보고 경보를 끈다.
// 2) 창문: CO2가 2000ppm 이상이면 서보가 열림 위치로 이동,
//    1000ppm 아래로 내려가면 닫힘 위치로 이동 (서버 없이도 동작)
//    다 움직인 뒤에는 서보 신호를 끊어 떨림·소음을 막는다.
// 3) CO2 값은 "CO2:850" 형식으로 서버에 보낸다.

#include <SoftwareSerial.h>
#include <Servo.h>

// ----- 핀 -----
const int LED_PIN    = 7;
const int BUZZER_PIN = 8;
const int SERVO_PIN  = 9;
const int CO2_RX_PIN = 10;  // 센서 TX(검정) 연결
const int CO2_TX_PIN = 11;  // 센서 RX(흰색) 연결

// ----- 창문 기준 -----
const int WINDOW_OPEN_PPM  = 2000; // 이 이상이면 창문 열기 (졸음 유발 수준)
const int WINDOW_CLOSE_PPM = 1000; // 이 아래로 내려가면 창문 닫기
const int WINDOW_CLOSED_ANGLE = 0;
const int WINDOW_OPEN_ANGLE   = 180;
const unsigned long SERVO_MOVE_MS = 800;  // 끝까지 도는 시간. 다 못 돌면 늘리기

// ----- 경보 -----
const int BUZZER_HZ = 400;                        // 낮을수록 작게 들림
const unsigned long COMMAND_TIMEOUT_MS = 3000;    // 서버 명령이 끊기면 경보 끄기

SoftwareSerial co2Serial(CO2_RX_PIN, CO2_TX_PIN);
Servo windowServo;

const byte READ_CMD[9] = {0xFF, 0x01, 0x86, 0x00, 0x00, 0x00, 0x00, 0x00, 0x79};

int  co2ppm = 0;
bool alarmOn = false;
bool windowOpen = false;
bool ledState = false;

unsigned long lastRequest = 0;
unsigned long lastBlink   = 0;
unsigned long lastCommand = 0;
unsigned long servoDetachAt = 0;
bool servoMoving = false;

char cmdBuf[8];
byte cmdLen = 0;

void setup() {
  Serial.begin(9600);     // 서버(USB)
  co2Serial.begin(9600);  // CO2 센서

  pinMode(LED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);

  moveWindow(WINDOW_CLOSED_ANGLE);
}

// 서보를 잠깐 연결해서 움직이고, SERVO_MOVE_MS 뒤에 신호를 끊는다.
void moveWindow(int angle) {
  windowServo.attach(SERVO_PIN);
  windowServo.write(angle);
  servoMoving = true;
  servoDetachAt = millis() + SERVO_MOVE_MS;
}

void setAlarm(bool on) {
  if (on == alarmOn) return;
  alarmOn = on;
  if (on) {
    tone(BUZZER_PIN, BUZZER_HZ);
  } else {
    noTone(BUZZER_PIN);
    digitalWrite(LED_PIN, LOW);
    ledState = false;
  }
}

// 서버 명령 읽기 ("A1\n" / "A0\n")
void readCommands() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      cmdBuf[cmdLen] = '\0';
      if (cmdLen == 2 && cmdBuf[0] == 'A') {
        lastCommand = millis();
        setAlarm(cmdBuf[1] == '1');
      }
      cmdLen = 0;
    } else if (cmdLen < sizeof(cmdBuf) - 1) {
      cmdBuf[cmdLen++] = c;
    } else {
      cmdLen = 0;  // 너무 긴 줄은 버림
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

  co2ppm = r[2] * 256 + r[3];
  Serial.print("CO2:");
  Serial.println(co2ppm);
}

void updateWindow() {
  if (co2ppm <= 0) return;  // 아직 측정값 없음
  if (!windowOpen && co2ppm >= WINDOW_OPEN_PPM) {
    windowOpen = true;
    moveWindow(WINDOW_OPEN_ANGLE);
  } else if (windowOpen && co2ppm < WINDOW_CLOSE_PPM) {
    windowOpen = false;
    moveWindow(WINDOW_CLOSED_ANGLE);
  }
}

void loop() {
  unsigned long now = millis();

  readCommands();
  if (alarmOn && now - lastCommand >= COMMAND_TIMEOUT_MS) setAlarm(false);

  if (now - lastRequest >= 2000) {
    lastRequest = now;
    requestCO2();
  }
  readCO2();
  updateWindow();

  if (servoMoving && (long)(now - servoDetachAt) >= 0) {
    windowServo.detach();
    servoMoving = false;
  }

  if (alarmOn && now - lastBlink >= 300) {
    lastBlink = now;
    ledState = !ledState;
    digitalWrite(LED_PIN, ledState);
  }
}
