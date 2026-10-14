# 서버 웹캠 기반 앱 적용

이 저장소의 실제 감지 경로는 서버 컴퓨터에 직결된 웹캠이다.
Flutter 앱은 카메라 영상을 업로드하지 않고, 서버의 감지 결과를 받아 표시한다.
Flutter 소스는 이 저장소에 없으므로 아래는 백엔드 연결 계약이다.

## 기본 실행

검증한 후보 `20261013_195253_465521`의 모델·스케일러를
`backend/ai_models/released/personal-ear-v1/`에 배포용으로 복사했다.
후보 폴더는 학습 실험용이며 실행·배포 시 함께 가져올 필요가 없다.
배포 모델은 Git 추적 대상에 포함되도록 예외 처리했다.

별도의 모델 환경변수 없이 프로젝트 루트에서:

```powershell
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

IDE에서 `backend/app/main.py`를 직접 실행해도 서버가 시작된다.
프로젝트 루트에서는 `python backend/app/main.py`로 실행할 수 있다.
직접 실행은 개발용 자동 재시작(`reload=True`)을 사용한다.

`backend/.env` 또는 프로세스 환경에 `DROWSINESS_MODEL_DIR`를 지정했다면
그 값이 기본 배포 모델보다 우선한다. 기본 모델을 사용하려면 해당 설정을 제거한다.
상대 경로는 현재 터미널 위치와 관계없이 `backend/` 기준이다.
웹캠 번호는 `DROWSINESS_CAMERA_INDEX=0`으로 설정할 수 있다.

`GET /health`에서 다음을 확인한다.

```json
{
  "models_ready": true,
  "model_release_id": "personal-ear-v1",
  "model_normalization": "personal_baseline",
  "model_threshold": 0.02,
  "detection_code_version": "event-risk-v7",
  "model_entry_seconds": 1.25,
  "eye_entry_seconds": 1.25
}
```

설정되지 않은 다른 후보를 자동으로 선택하거나 모델 묶음이 잘못됐을 때
기존 모델로 조용히 대체하지 않는다. 모델 준비 실패는 `/health`와
`/detection/start`의 503 응답으로 확인한다. 배포 모델·스케일러의 SHA-256도 검증한다.

## 앱 연결 순서

1. `POST /auth/login`: `username`, `password`, 앱 기기의 `device_id`를 전송해 토큰을 받는다.
2. `GET /sessions/current`로 현재 세션을 확인하거나 `POST /sessions/start`로 생성한다.
   생성 성공은 HTTP 201이며 ID 필드는 `session_id`다. 현재 세션 조회는 `id`를 사용한다.
3. `POST /detection/start`: Bearer 인증과 `{"session_id": ...}`를 전송한다.
4. `WS /detection/ws/{session_id}`에 `Authorization: Bearer <token>` 헤더로 연결한다.
5. 앱에서 종료할 때 `/detection/stop`, 이어서 `/sessions/end`를 호출한다.

HTTP 200 시작 응답은 백그라운드 루프 시작을 의미하며, 실제 카메라 준비와
보정 완료는 WebSocket 상태로 판단한다. 시작 응답에는 보정 안내 메시지,
`calibration_required_seconds`, `model_release_id`, `detection_code_version`도 포함된다.

휴대폰 앱의 서버 주소에는 PC의 LAN/Tailscale 주소를 사용한다.
휴대폰의 `localhost`는 서버 PC를 의미하지 않는다.
현재 구조는 카메라 1대에 감지 세션 1개를 사용한다.

## 화면과 경고 처리

기존 `pred_score`, `ear_value`, `drowsy_level`, `face_detected`, `message` 필드를 유지했다.
앱의 경고 기준은 원시 점수가 아니라 최종 `drowsy_level`이다.
개인 보정·추론·위험 단계 판정과 회복은 서버가 수행한다.
경고음 중지는 사용자가 앱 버튼으로 직접 수행한다. 단계 하락이나 눈 재개방은 경고음 중지 명령이 아니다.

| 상태 | 앱 표시와 처리 |
|---|---|
| `CALIBRATING` | 눈을 뜨고 정면을 보라는 안내와 `calibration_progress` 표시 |
| `WARMING_UP` | 모델 입력 준비 중 표시 |
| `DROWSY_PENDING` | 눈 상태를 확인 중이라고 표시; 경고 단계 0 |
| `AWAKE` | 위험 단계 0 표시. 경고음은 자동 중지하지 않음 |
| `DROWSY` | 최종 단계 표시. `recovery_pending`이면 회복 대기, `risk_latched`이면 3단계 위험 기록 유지 |
| `NO_FACE`, `INVALID_EAR` | 측정 불가 안내. 단계 0만 보고 정상으로 표시하지 않음 |
| `ERROR` 또는 `error` 필드 | 카메라/추론 오류 표시. 감지 재시작 안내 |

관련 추가 필드:

- `observation_valid`: 유효한 눈 관측과 개인 보정이 완료됐는지
- `alert_active`: 현재 유효한 관측에서 최종 경고가 활성 상태인지
- `alert_event`: 서버에서 경고 단계가 새로 상승한 프레임인지
- `calibrated`, `calibration_progress`, `eye_closed_seconds`
- `new_drowsiness_event`, `recent_event_count`: 새 사건 여부와 최근 30초 사건 수
- `emergency_condition`: 현재 양쪽 눈 감김이 3초 이상 지속되는지
- `recovery_pending`, `recovery_seconds`: 정상 관측에서 위험 단계 회복 대기 여부와 시간
- `risk_latched`: 3단계 위험 기록이 세션에 유지되는지
- `ear_closure_ratio`, `observed_window_seconds`: 최근 60초의 유효 관측에서 EAR 기반 눈 감김 비율과 관측 시간. 표준 PERCLOS와 같지 않음
- `transition_reason`: 마지막 단계 변경 이유
- `alarm_acknowledged`, `alarm_ack_required`: 앱 경보 중지 확인 여부와 중지 확인이 필요한지

경고음을 매 프레임 반복하지 않는다. 앱에서도 마지막 수신 단계와 현재 단계를
비교해 단계가 상승할 때 소리·진동을 실행하는 것이 좋다. `alert_event`는 편의 신호이며,
연결 지연·재연결 시 단일 발생 프레임을 놓칠 수 있으므로 현재 `alert_active`와 단계도 확인한다.
`ping` 메시지는 감지 결과가 아니므로 화면·경고 판단에서 제외한다.

사용자의 중지 버튼은 앱의 오디오 재생 상태만 바꾼다. 서버 단계를 0으로 초기화하거나
관측 값을 수정하지 않는다. 중지 후 같은 단계 메시지가 계속 수신돼도 매 프레임 소리를 다시 시작하지 않는다.
`alert_active=false`, 정상 관측, 단계 하락, 측정 불가도 자동 음소거의 근거로 사용하지 않는다.
앱은 소리를 중지한 뒤 아래 API로 중지 사실을 서버에 알린다. 서버가 앱 소리를 직접 끄지는 않는다.
Flutter 소스가 없으므로 버튼에서 이 API를 호출하는 코드는 앱 저장소에서 연결해야 한다.

```http
POST /detection/alarm/acknowledge
Authorization: Bearer <JWT>
Content-Type: application/json

{"session_id": 11}
```

현재 실행 중인 감지의 소유자만 호출할 수 있다. 성공 응답은 HTTP 202 (`status: accepted`)이며
카메라 스레드에서 적용 후 WebSocket의 `alarm_acknowledged=true`로 확인한다.
2·3단계가 아니면 409, 실행 중인 세션이 없으면 404, 다른 사용자 세션이면 403이다.
중복 확인 요청은 이미 시작된 회복 시간을 초기화하지 않는다.
테스트 클라이언트의 `m` 키로 동일 API를 호출할 수 있다. `0` 키는 정답 라벨이며 중지 확인과 별개다.

## v7 단계 전환과 회복

아래 수치는 개발용 정책이며 실제 도로 안전성 검증을 완료한 기준은 아니다.

- 양쪽 EAR가 개인 기준의 72% 이하로 1.25초 지속되면 사건 1회와 1단계가 발생한다.
- 모델 점수가 선택 모델의 임계값 이상이고 양쪽 EAR가 개인 기준의 80% 이하로 1.25초 지속돼도 같은 사건으로 인정한다. 모델 점수 크기로 2·3단계를 결정하지 않는다.
- 재개방(양쪽 EAR 82% 이상)이 0.5초 확인되기 전에는 같은 사건을 다시 세지 않는다.
- 최근 30초에 사건이 2회 발생하면 2단계, 2단계 이후 새 사건이 1회 더 발생하면 3단계다.
- 양쪽 눈 감김이 3초 지속되면 반복 횟수와 무관하게 즉시 3단계로 전환한다.
- 1단계는 정상 눈 상태 10초 연속 관측 후 0단계로 회복한다.
- 2·3단계는 앱의 경보 중지 확인을 받은 뒤 양쪽 EAR가 개인 기준 82% 이상인 정상 눈 상태를 10초 연속 관측하면 바로 0단계로 내려간다. 중지 확인 전 정상 시간은 포함하지 않는다.
- 회복 중 0.5초 미만의 짧은 깜빡임(닫힘과 경계 상태 포함)은 타이머를 초기화하지 않는다. 깜빡이는 동안 회복 시간 표시는 보류하며, 다시 양쪽 눈이 열렸을 때 짧은 깜빡임 구간을 포함해 계산한다. 눈을 감은 상태에서는 10초가 지나도 0단계로 내려가지 않는다.
- 눈 처짐이나 눈 감김이 0.5초 이상 지속되면 타이머를 다시 시작한다. 이 허용 시간은 `/health`의 `recovery_blink_tolerance_seconds`로 확인한다. 새로운 사건이나 긴급 조건 발생 시 기존 중지 확인은 무효이며 다시 중지 확인해야 한다.
- 0단계 회복 시 사건 이력을 비워 다음 독립 사건은 1단계부터 시작한다. 개인 보정과 모델 입력은 유지하며 기존 DB 로그는 삭제하지 않는다.
- 이전 정책의 30초 대기·눈 감김 비율 10% 조건·3단계 영구 유지는 제거했다. EAR 눈 감김 비율은 진단 지표로만 제공한다.
- 미검출·유효하지 않은 EAR·긴 프레임 공백은 지속 시간과 회복 시간을 끊는다. 위험 단계나 사건 이력을 정상으로 초기화하지 않는다.

`DROWSY`는 현재 감긴 눈뿐 아니라 위험 기록·회복 대기를 포함한다.
따라서 눈 뜸/감김 정답만으로 계산한 최종 단계 프레임 정확도를 v4와 그대로 비교하지 않는다.
개별 사건 감지, 반복 단계 전환, 회복 시간, 긴급 전환을 따로 검증한다.

WebSocket은 세션 소유자만 연결할 수 있다. 카메라·추론 오류는 `ERROR` JSON과
종료 코드 1011로 전달하며, 일반 감지 종료는 1000으로 닫는다.
서버에서는 단계 상승 시 DB 로그를 기록한다. 앱의 실제 경고음·진동·SMS UI는
Flutter 측 기능이며, 이 저장소에서는 직접 수정하지 않았다.

## 독립 실행 도구

```powershell
python model_training/realtime_detect.py
```

기본 모델 선택과 `CameraDetector`·`DrowsinessProcessor`를 서버와 공유한다.
영상 창의 `q`는 종료, `r`은 개인 보정과 경고 상태 초기화다.
`m`은 앱 중지 버튼을 대신하는 경보 중지 확인이다. 2·3단계에서 정상 상태 10초 회복을 시험할 수 있다.
`--camera 1`로 카메라를 바꾸거나 `--no-sound`로 화면만 확인할 수 있다.
경고음은 별도 스레드에서 실행해 프레임 처리를 멈추지 않는다.
서버 감지와 독립 도구를 동시에 실행해 같은 웹캠을 점유하지 않는다.

## 기존 모델 복구

`backend/.env`에 `DROWSINESS_MODEL_DIR=ai_models`를 지정하고 서버를 재시작한다.
기존 원시 EAR 모델·스케일러는 루트 `ai_models/`에 그대로 있다.
새 배포 모델 폴더의 모델·스케일러·메타데이터 중 하나만 바꾸면 안 된다.

## 검증 범위

자동 테스트 40개가 통과했다. 기본 배포 모델의 실제 로드·추론도 확인했다.
v5 단계 정책 변경 후에는 전환·회복·긴급 조건 테스트를 포함한 총 55개 테스트가 통과했다.
v6 중지 확인 API·2·3단계 직접 회복·중복 요청·새 위험 발생·관측 중단 테스트를 포함한 총 64개 테스트가 통과했다.
서버 직결 웹캠을 열어 5개 프레임을 처리한 뒤 자원을 정상 해제했다.
이 카메라 확인에서는 얼굴이 검출되지 않았으므로 얼굴·졸음 판정의 추가 촬영 검증을 의미하지는 않는다.

기본 모델 실제 로드·추론, 공통 처리기의 보정·경고 사건, 카메라 오류 시 자원 해제,
WebSocket JSON·오류·소유자 검사를 검증한다.
실제 Flutter 화면·휴대폰 소리·진동의 통합 실행은 앱 소스와 기기가 없어 검증하지 않았다.
모델의 여러 사용자·환경 일반화 여부도 별도의 검증 범위다.
