# DrowsyGuard 아두이노 경보기

서버 PC(웹캠이 연결된 컴퓨터)에 USB로 연결한다. 서버가 2·3단계 졸음 경보를 켜고 끄며,
아두이노는 CO₂ 측정값을 서버로 전송한다. 서보 제어는 사용하지 않는다.

- 스케치: `drowsyguard_alarm/drowsyguard_alarm.ino`
- 보드: Arduino Uno (ATmega328P, 5V). `SoftwareSerial`은 AVR 코어에 포함된다.
- 펌웨어 업로드에는 Arduino IDE 또는 Arduino CLI가 필요하다. 서버에는 Python의 `pyserial`과 USB 포트 접근 권한이 필요하다
  (장치 관리자에 COM 포트가 안 보이면 호환 보드의 CH340 드라이버 설치).

## 이 노트북에서 사용하기 (Arch Linux)

프로젝트 루트에서 실행한다. Arduino CLI와 보드 패키지는 Git에서 제외한
`.local/arduino/`에 설치되어 있으며, `cli.sh`가 해당 경로를 사용한다.
GUI IDE 없이 컴파일과 업로드가 가능하다.

1. 최초 한 번 일반 터미널에서 USB 시리얼 접근 그룹을 추가하고 로그아웃 후 다시 로그인한다.
   관리자 암호가 필요하다.

   ```bash
   sudo usermod -aG uucp david
   ```

2. USB 데이터 케이블로 Uno를 연결하고 포트를 확인한다.

   ```bash
   bash arduino/cli.sh board list
   ls -l /dev/serial/by-id/
   ```

   호환 보드는 보드 이름이 Unknown이어도 포트가 표시될 수 있다.
   가능하면 재연결 시에도 유지되는 `/dev/serial/by-id/...` 경로를 사용한다.

3. 서버와 시리얼 모니터를 종료하고 펌웨어를 업로드한다.
   아래 `/dev/ttyACM0`은 실제 연결된 보드 포트로 바꾼다.

   ```bash
   bash arduino/cli.sh compile --fqbn arduino:avr:uno --build-path "$PWD/.local/arduino/build" arduino/drowsyguard_alarm
   bash arduino/cli.sh upload --fqbn arduino:avr:uno --port /dev/ttyACM0 --input-dir "$PWD/.local/arduino/build" arduino/drowsyguard_alarm
   ```

4. `backend/.env`에 실제 포트를 설정하고 백엔드를 다시 시작한다.
   파일이 없으면 생성하고, 이미 있으면 기존 설정을 유지하며 아래 항목을 수정한다.

   ```dotenv
   ARDUINO_PORT=/dev/ttyACM0
   ARDUINO_BAUD=9600
   ```

5. `/health` 또는 `/devices/telemetry`에서 `arduino_connected=true`와
   `arduino_responding=true`를 확인한다. 실제 부저·센서 검증은 아래 확인 방법을 따른다.

도구를 다시 설치할 때는 공식 Linux x86-64 Arduino CLI 실행 파일을
`.local/arduino/bin/arduino-cli`에 둔 뒤 다음 명령을 실행한다.

```bash
bash arduino/cli.sh core update-index
bash arduino/cli.sh core install arduino:avr@1.8.8
```

## 배선

| 부품 | 부품 핀 | 아두이노 | 비고 |
|---|---|---|---|
| 청색 LED | 긴 다리(+) | D7 | 짧은 다리(−) → 220Ω 저항 → GND |
| 부저 | + | D8 | − → GND |
| CO₂ 센서 MH-Z19E | TX | D10 | 아두이노 수신 핀 (교차 연결) |
| | RX | D11 | 아두이노 송신 핀 |
| | Vin | 5V | |
| | GND | GND | |

전원과 접지
- 모든 부품의 GND는 아두이노 GND와 같은 줄(공통 접지)에 연결한다.

## 동작 규칙

| 상황 | 동작 |
|---|---|
| 서버가 `A1` 전송 (2·3단계, 앱 중지 전) | LED 0.3초 깜빡임 + 부저 400Hz |
| 서버가 `A0` 전송 (0·1단계, 앱 경보 중지, 감지 종료, 서버 종료) | LED·부저 끔 |
| 카메라·추론이 멈춤 (서버는 동작) | 마지막 상태 유지. 서버가 1초마다 같은 명령을 다시 보냄 |
| 서버 프로세스·USB 통신 단절 | 아두이노가 마지막 상태 유지. 경보 중이면 계속 울림 |
| 아두이노 재부팅 | 경보 꺼진 상태로 시작, `BOOT` 전송 → 서버가 현재 상태를 바로 다시 보냄 |
| CO₂ 응답 없음·오류값 10초 이상 | `CO2:NA` 전송, 측정값 없음으로 표시 |

감지 정지 중에도 경보를 유지하는 이유: 졸음 경보 도중 카메라가 멈췄다고 경보가 꺼지면 안 된다.
경보는 앱의 중지 버튼(`POST /detection/alarm/acknowledge`)이나 감지 종료(`POST /detection/stop`)로 끈다.
감지 루프가 오류로 멈춘 경우 중지 확인 API는 404이므로 앱은 `/detection/stop`을 호출해야 한다.
중지 확인이 접수되면 카메라·추론이 지연 중이어도 서버 통신 워커에 A0 상태를 즉시 전달한다.
늦게 도착한 이전 위험의 결과가 경보를 다시 켜지 않도록 처리하며, 새 위험이 발생하면 다시 경보한다.
감지 종료 후 이전 카메라 자원이 아직 정리 중이면 새 감지 시작은 409이므로 잠시 뒤 재시도한다.

## 시리얼 규칙 (9600bps, 줄 단위)

| 방향 | 메시지 | 의미 |
|---|---|---|
| 서버 → 아두이노 | `A1` / `A0` | 경보 켜기 / 끄기. 이 두 개만 유효 |
| 아두이노 → 서버 | `ACK:A1` / `ACK:A0` | 명령 처리 완료 |
| | `ERR:CMD` | 잘못된 명령. 상태는 그대로 |
| | `BOOT` | 재부팅됨 |
| | `CO2:850` | 유효 측정값 (300~10000ppm) |
| | `CO2:NA` | 10초 넘게 유효 측정값 없음 |

ACK는 명령을 받아 처리했다는 뜻이다. 부저가 실제로 소리를 냈는지까지 확인하지는 않는다.

## 확인 방법

1. IDE 시리얼 모니터(9600)를 열면 `BOOT`, 2초마다 `CO2:...`가 보인다.
2. 입력창에 `A1`을 보내면 `ACK:A1`과 함께 경보가 켜지고, `A0`으로 꺼진다. `A2`는 `ERR:CMD`.
3. 서버와 함께 쓸 때는 시리얼 모니터를 닫는다. 같은 포트를 동시에 쓸 수 없다.
4. 서버 `/health`에서 `arduino_connected`(포트 열림), `arduino_responding`(3초 안에 ACK 수신),
   `arduino_alarm_on`, `co2_ppm`을 확인한다. `status=ok`는 카메라·모델 상태만 뜻한다.
   BOOT를 받으면 이전 ACK는 무효이며, 새 명령의 ACK를 받은 뒤 다시 응답 중으로 표시한다.
5. 앱 홈 화면은 로그인 후 `GET /devices/telemetry`로 실제 CO₂ 값을 조회한다.
   카메라·졸음 감지를 시작하지 않아도 센서 값은 표시된다. 유효 값이 없거나 서버 수신 후 10초가 지나면
   숫자 대신 ‘측정값 없음’을 표시한다. `co2_age_seconds`는 서버 수신 후 경과 시간이다.

저주파 마사지기는 현재 펌웨어에 제어 코드가 없다. 2026-10-17 사용자 요청으로 관련 연동·단계 저장은 보류했다.

## 테스트

- 호스트 테스트(보드 불필요): `bash arduino/test/run.sh`
  명령 처리, 경보 유지, 잘못된 명령 거부, CO₂ 유효성·만료, millis 랩어라운드를 검사한다.
- 실제 보드에서 따로 확인할 것: USB 단절·재연결, 센서 단절 시 `CO2:NA`.
