# DrowsyGuard (NPNS)

서버 PC에 연결한 USB 웹캠으로 운전자의 눈 상태를 분석하고, 졸음 위험 단계와 운전 기록을 Android 앱에 전달하는 졸음운전 방지 프로젝트입니다. FastAPI 백엔드, EAR 시계열 LSTM 감지, 데이터 수집·재학습 도구, 선택 연결하는 Arduino 경보기를 포함합니다.

Android Kotlin 앱은 별도 `AndroidStudioProjects/NSND` 프로젝트에서 관리합니다. 이 저장소에는 앱 소스가 없으며, 앱은 카메라 영상을 업로드하지 않고 서버의 감지 결과를 수신합니다. 현재 서버는 **프로세스 1개·카메라 1대·활성 감지 세션 1개**를 기준으로 동작합니다.

## 주요 기능

- JWT 회원가입·로그인·로그아웃·회원 탈퇴, 계정별 단일 활성 로그인과 기기 확인
- 운전 세션 시작·종료·조회, 앱에서 측정한 누적 주행 거리 동기화
- MediaPipe 눈 랜드마크와 개인별 EAR 보정, LSTM 추론, `event-risk-v7` 위험 단계 판정
- WebSocket 실시간 결과 전송과 서버 PC의 영상·판정 지표 미리보기
- 위험 ID 기반 경보 중지 확인, 독립 눈 감김 사건과 단계 상승 기록 구분
- 디스크 일지 기반 기록 저장 재시도, 커서 기반 누락 사건 조회, 사건별 앱 수신 위치 동기화
- 세션·날짜별 리포트, 안전 점수, 관측 가능 시간·위험 시간·독립 사건 통계
- 긴급 연락처 CRUD와 3단계 자동 SMS 정책 조회·검증
- 선택 연결한 Arduino의 LED·부저 경보, CO₂ 측정값 조회, 센서에 따른 서보 창문 제어

SMS 발송과 결과 이력 저장, 휴대폰 소리·진동, GPS 측정은 별도 Android 앱이 담당합니다. 백엔드에는 SMS 발송 API나 알리고 발송 모듈이 없습니다. 연락처가 등록되면 자동 SMS 정책이 활성화되며, `/contacts/sms-settings`는 비활성화 요청을 거부합니다.

## 프로젝트 구조

```text
NPNS/
├── backend/
│   ├── .env.example                # 서버 설정 템플릿
│   ├── requirements.txt            # Python 의존성
│   ├── APPLICATION_INTEGRATION.md  # Android 연동 계약과 상세 동작
│   ├── app/
│   │   ├── main.py                 # 서버 진입점·시작/종료·health
│   │   ├── config.py               # 환경변수·경로 설정
│   │   ├── database.py             # 비동기 DB·기존 스키마 보완
│   │   ├── models.py / schemas.py  # DB 모델·API 요청/응답
│   │   ├── auth.py / dependencies.py
│   │   ├── detection_service.py    # 보정·추론·위험 판정·감지 세션
│   │   ├── ear_sequence.py         # 공통 EAR 시계열 입력 처리
│   │   ├── usb_camera.py           # USB 웹캠 선택·프레임 확인
│   │   ├── detection_preview.py / preview_window.py
│   │   ├── record_journal.py       # 디스크 일지·DB 재전송
│   │   ├── observation_recorder.py / report_observations.py
│   │   ├── arduino_bridge.py       # USB 시리얼 통신
│   │   └── routers/                # auth·sessions·detection·report·contacts·devices
│   ├── ai_models/
│   │   ├── face_landmarker.task
│   │   ├── released/personal-ear-v1/ # 기본 모델·스케일러·메타데이터
│   │   └── candidates/             # 로컬 재학습 결과 (Git 제외)
│   ├── data/                      # 기록 일지 (실행 시 생성, Git 제외)
│   ├── diagnostics/               # 진단 CSV·요약 (Git 제외)
│   ├── tests/                     # unittest 테스트
│   └── test_client.py             # API·실시간 감지 진단 클라이언트
├── model_training/                # 수집·학습·평가·규칙 비교·독립 웹캠 실행
├── arduino/                       # Uno 펌웨어·배선 안내·호스트 테스트
└── .github/workflows/backend-arduino-checks.yml
```

## 기술 구성

| 구성 | 사용 기술 |
|---|---|
| API·실시간 통신 | FastAPI 0.115.12, Uvicorn 0.34.3, WebSocket |
| 데이터 저장 | SQLAlchemy 2.0.41, aiosqlite, SQLite |
| 인증·설정 | python-jose, passlib/bcrypt, Pydantic, pydantic-settings |
| 영상·추론 | OpenCV, MediaPipe Tasks, TensorFlow/Keras, NumPy, scikit-learn, joblib |
| 장치 연동 | pyserial 3.5, Arduino Uno, Servo, SoftwareSerial |
| 검증 | Python unittest, Ruff, C++ 펌웨어 호스트 테스트, Arduino CLI |

전체 의존성은 [backend/requirements.txt](backend/requirements.txt)에 있습니다. CI는 Python 3.12를 사용하며, 일부 AI 패키지는 버전이 고정되어 있지 않습니다.

## 설치와 실행

아래 명령은 Windows PowerShell의 프로젝트 루트를 기준으로 합니다. 가상환경은 루트의 `.venv` 하나를 사용합니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt

# 최초 설정 시에만 복사합니다. 기존 .env가 있으면 내용을 확인해 필요한 항목만 추가하세요.
if (-not (Test-Path backend/.env)) { Copy-Item backend/.env.example backend/.env }
```

`backend/.env`의 `SECRET_KEY`를 고유한 값으로 변경하고 카메라·장치 설정을 확인합니다. `.env`는 Git 추적에서 제외됩니다.

```powershell
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

IDE에서 `backend/app/main.py`를 직접 실행하거나, 루트에서 `python backend/app/main.py`를 실행해도 됩니다. 직접 실행은 개발용 자동 재시작을 사용합니다. 감지와 기록 일지가 프로세스 내 상태를 사용하므로 서버는 단일 worker로 실행합니다.

- Swagger UI: <http://localhost:8000/docs>
- 서버·모델·카메라·장치 상태: <http://localhost:8000/health>
- 휴대폰 앱의 서버 주소: 서버 PC의 LAN/Tailscale 주소와 포트 `8000`

휴대폰의 `localhost`는 서버 PC 주소가 아닙니다. 실제 앱 연결 순서와 응답 예시는 [앱 적용 안내](backend/APPLICATION_INTEGRATION.md)에 있습니다.

### 환경변수

| 항목 | 기본값·동작 |
|---|---|
| `APP_ENV` | `development`; DB SQL 로그 활성화 |
| `SECRET_KEY` | 개발용 기본값이 있으므로 `.env`에서 변경 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 코드 기본값 `1440`(24시간), `.env.example`은 `525600`(1년) |
| `DATABASE_URL` | `sqlite+aiosqlite:///./drowsyguard.db`; 상대 SQLite 경로는 `backend/` 기준 |
| `DROWSINESS_MODEL_DIR` | 미설정 시 `backend/ai_models/released/personal-ear-v1/`; 상대 경로는 `backend/` 기준 |
| `DROWSINESS_CAMERA_INDEX` | `auto`; Linux는 프레임을 읽을 수 있는 USB 영상 장치 선택, Windows는 번호 `0` 사용 |
| `DROWSINESS_PREVIEW_ENABLED` | `true`; 화면 없는 서버는 `false`로 설정 |
| `ARDUINO_PORT` | 미설정 시 장치 연동 비활성화; 예: `COM3`, `/dev/ttyACM0` |
| `ARDUINO_BAUD` | `9600` |

웹캠이 여러 대라면 Windows에서는 OpenCV 번호 `0`·`1` 등을 지정합니다. Linux에서는 `/dev/video32`에 해당하는 `32`처럼 지정하며, USB 장치인지와 실제 프레임 수신 여부를 확인합니다. 설정을 바꾸면 서버를 재시작합니다.

### 모델·카메라 준비 확인

기본 감지에는 저장소에 포함된 다음 자산을 사용합니다.

- `backend/ai_models/face_landmarker.task`
- `backend/ai_models/released/personal-ear-v1/best_drowsy_model.keras`
- 같은 폴더의 `ear_scaler.pkl`, `metadata.json`

모델·스케일러·메타데이터는 같은 묶음으로 사용합니다. 기본 배포 메타데이터는 `personal_baseline` 보정, 추론 임계값 `0.02`, 30개 입력과 약 `0.97초` 윈도우를 정의합니다. 배포 모델·스케일러는 메타데이터의 SHA-256과 비교합니다. `DROWSINESS_MODEL_DIR` 설정은 기본 모델보다 우선합니다.

`GET /health`에서 `models_ready`, `camera_available`, `model_release_id=personal-ear-v1`, `detection_code_version=event-risk-v7`를 확인합니다. `status=ok`는 모델과 카메라가 준비됐다는 뜻이며 Arduino 연결 성공까지 뜻하지는 않습니다. 모델이나 카메라가 준비되지 않으면 `status=not_ready`입니다. 모델 미준비 상태에서 감지 시작은 HTTP 503을 반환하고, 비동기 카메라·추론 오류는 WebSocket의 오류 결과로 전달합니다.

앱에서 감지를 시작하면 서버 PC에 실시간 미리보기가 열립니다. 눈 윤곽, 보정 진행률, 좌우 EAR, LSTM 점수, 눈 감김 시간, 위험 단계와 FPS를 표시합니다. Q·Esc·창 닫기는 미리보기만 닫으며 감지 종료와 경보 중지는 앱에서 수행합니다. 그래픽 데스크톱에서 실행해야 창이 표시됩니다.

## 앱 연결과 API

회원가입·로그인·`/health`를 제외한 아래 API는 Bearer JWT 인증을 사용합니다. WebSocket도 `Authorization: Bearer <token>` 헤더가 필요합니다. 로그인 요청은 `username`, `password`, `device_id`를 사용합니다.

1. `/auth/login`으로 토큰을 받고 `/sessions/current`를 조회하거나 `/sessions/start`로 운전 세션을 만듭니다.
2. `/detection/start`에 `{"session_id": ...}`를 전송합니다.
3. `/detection/ws/{session_id}`로 연결해 보정 상태와 감지 결과를 받습니다.
4. 2·3단계 경보를 사용자가 중지하면 `/detection/alarm/acknowledge`에 `session_id`와 현재 `risk_id`를 전송합니다.
5. 종료 시 `/detection/stop`, `/sessions/end` 순서로 호출합니다.

감지 시작 성공 응답은 백그라운드 루프가 시작됐다는 뜻입니다. 실제 카메라 준비와 보정 완료는 WebSocket 상태로 확인합니다. `ping` 메시지는 감지 결과에서 제외합니다.

| 메서드 | 경로 | 기능 |
|---|---|---|
| GET | `/health` | 모델·카메라·Arduino·활성 감지·기록 오류 상태 |
| POST | `/auth/register`, `/auth/login` | 회원가입, 로그인 |
| GET / DELETE | `/auth/me` | 내 정보 조회, 회원 탈퇴 |
| DELETE | `/auth/logout` | 로그아웃 |
| PUT | `/auth/contacts` | 기존 단일 비상 연락처 필드 수정 |
| POST | `/sessions/start`, `/sessions/end` | 운전 시작, 종료 |
| GET | `/sessions/current`, `/sessions/history` | 현재 세션, 세션 목록 |
| PUT | `/sessions/{session_id}/distance` | 누적 거리 저장; 기존 값보다 감소하지 않음 |
| POST | `/detection/start`, `/detection/stop` | 서버 웹캠 감지 시작, 종료 |
| GET | `/detection/status/{session_id}` | 감지 실행 여부 |
| WS | `/detection/ws/{session_id}` | 실시간 감지 결과 |
| POST | `/detection/alarm/acknowledge` | 현재 위험의 경보 중지 확인 접수 (202) |
| GET | `/detection/events` | `after`·`limit` 커서로 저장된 누락 사건 조회 |
| PUT | `/detection/events/{event_id}/location` | 사건의 최초 앱 수신 위치·품질 저장 |
| GET | `/report/summary`, `/report/history` | 최근 종료 세션 요약, 종료 세션 목록 |
| GET | `/report/detail/{session_id}`, `/report/by-date` | 세션 상세, 날짜별 집계 |
| GET / POST | `/contacts` | 긴급 연락처 목록, 등록 |
| PUT / DELETE | `/contacts/{contact_id}` | 긴급 연락처 수정, 삭제 |
| GET / PUT | `/contacts/sms-settings` | 자동 SMS 정책 조회, 설정 검증 |
| GET | `/devices/telemetry` | 감지 실행과 독립적인 Arduino·CO₂ 상태 |

리포트 요약·상세·날짜별 조회는 `utc_offset_minutes`를 받습니다. 기본값은 UTC(`0`)이며 한국 날짜·시간 집계에는 `540`을 사용합니다. 날짜별 조회는 `date=YYYY-MM-DD`도 필요합니다. 요청·응답의 전체 스키마는 Swagger UI를 참고하세요.

## 졸음 판정과 경보 회복

기본 모델은 `personal-ear-v1`, 단계 정책은 `event-risk-v7`입니다. 최소 3초 동안 눈을 뜨고 정면을 보며 좌우 EAR 기준을 보정합니다. 보정 후 다음 규칙을 적용합니다.

| 조건 | 결과 |
|---|---|
| 양쪽 EAR가 개인 기준의 72% 이하로 1.25초 유지 | 독립 사건 1회, 1단계 진입 |
| 모델 점수가 임계값 이상이고 양쪽 EAR가 기준의 80% 이하로 1.25초 유지 | 같은 사건 진입 조건 |
| 최근 30초의 사건 2회 | 2단계 |
| 2단계 이후 새 사건 | 3단계 |
| 양쪽 눈 감김 3초 지속 | 즉시 3단계 |
| 양쪽 EAR가 기준의 82% 이상으로 0.5초 유지 | 다음 독립 사건을 셀 수 있도록 재개방 확인 |
| 1단계에서 정상 관측 10초 | 0단계 회복 |
| 2·3단계에서 현재 위험 중지 확인 후 정상 관측 10초 | 0단계 회복·사건 이력 초기화 |

모델 점수 크기로 2·3단계를 결정하지 않습니다. 회복 중 0.5초 미만의 짧은 깜빡임은 허용하지만, 눈 처짐·감김이 0.5초 이상이거나 관측이 끊기면 회복 시간을 다시 계산합니다. 0단계 복귀는 양쪽 눈이 열린 상태에서 완료합니다. 새 위험이 생기면 이전 중지 확인은 무효가 됩니다.

재개방·단계 하락만으로 앱 경고음이 자동 종료되지는 않습니다. 사용자의 중지 버튼이 앱 소리·진동을 멈추고 서버에 중지 확인을 보냅니다. `event_id`는 단계 상승 기록, `episode_id`는 독립 눈 감김 사건, `risk_id`는 중지 확인 대상을 식별합니다. 세부 상태·오류·재연결 계약은 [앱 적용 안내](backend/APPLICATION_INTEGRATION.md)를 참고하세요.

## 기록 저장과 리포트

| DB 테이블 | 저장 내용 |
|---|---|
| `users`, `login_sessions` | 사용자, 계정별 활성 로그인 |
| `contacts` | 긴급 연락처와 발송 메시지 |
| `driving_sessions` | 운전 시작·종료·누적 거리 |
| `detection_logs` | 단계 상승, EAR·점수, 사건 ID와 앱 수신 위치 |
| `drowsiness_episodes` | 독립 눈 감김 사건과 최고 단계 |
| `observation_segments` | 유효·미관측 구간, 단계별 관측 시간 |

서버 시작 시 테이블을 생성하고 기존 DB에 필요한 기록 메타데이터 열을 추가합니다. 기본 DB는 `backend/drowsyguard.db`이며, 루트의 동명 파일과 구분해야 합니다. 기록은 `backend/data/record-journal-*.sqlite3`의 별도 디스크 일지를 거쳐 DB에 반영하며, 남은 일지는 다음 시작에 재처리합니다. 일지 자체의 디스크 오류는 `storage_error` 등으로 전달합니다.

리포트의 기존 `total_drowsy_count`는 단계 상승 로그 수입니다. 독립 사건 수는 `independent_event_count`로 별도 제공합니다. 유효 관측 시간·미관측 시간·위험 관측 시간·관측 커버리지도 함께 확인해야 합니다. 과거 기록에 독립 사건·관측 자료가 없으면 이를 추정해서 채우지 않습니다.

안전 점수는 단계 상승 로그별 1단계 2점·2단계 5점·3단계 15점을 100점에서 차감하고 최저 0점으로 계산합니다. 등급은 80점 이상 `safe`, 50점 이상 `caution`, 그 미만 `danger`입니다. 관측 불가 구간을 정상 운전으로 해석해서는 안 됩니다.

## Arduino 경보기 (선택)

Arduino Uno를 서버 PC에 USB로 연결하고 `ARDUINO_PORT`를 지정합니다. 2·3단계에서 LED·부저를 켜고, 사용자 경보 중지·감지 종료에 따라 끕니다. CO₂ 센서와 서보는 펌웨어가 별도로 처리하며, 2000ppm 이상에서 창문을 열고 1000ppm 미만에서 닫습니다. `GET /devices/telemetry`는 카메라 감지를 시작하지 않아도 조회할 수 있습니다.

배선, `A1`·`A0`/ACK 프로토콜, 센서 만료와 통신 단절 시 동작은 [Arduino 안내](arduino/README.md)에 있습니다. 저주파 마사지기 제어는 현재 구현되어 있지 않습니다.

## 진단·학습·평가

활성화된 가상환경으로 프로젝트 루트에서 실행합니다. 진단 클라이언트에는 실행 중인 백엔드가 필요합니다.

```powershell
python backend/test_client.py
```

클라이언트는 테스트 계정으로 로그인하고 감지를 시작합니다. 보정 후 `0`은 정상 정답, `1`은 눈 감김 정답, `u`는 라벨 해제, `m`은 경보 중지 확인, `q`는 종료입니다. `backend/diagnostics/`에 CSV를 저장하고 종료 시 터미널에 요약을 출력합니다. 원시 모델과 최종 위험 단계 지표를 구분하며, 키 입력부터 결과 수신까지의 지연을 측정합니다.

서버 없이 같은 처리기로 웹캠을 확인하려면 다음을 사용합니다. 서버 감지와 동시에 같은 카메라를 사용하지 마세요.

```powershell
python model_training/realtime_detect.py
```

새 EAR 데이터 수집과 학습 CLI 확인:

```powershell
python model_training/collect_data.py
python model_training/train_model.py --help
```

수집 결과는 `model_training/ear_sessions.csv`에 촬영 세션별로 저장됩니다. 두 번째 촬영 단계는 라벨 키 입력 전까지 저장하지 않습니다. `1`·`0`·`u`로 실제 눈 상태를 지정합니다. 학습에는 타임스탬프와 정답 라벨이 필요하며, 기존 2열 `ear_data.csv`는 사용하지 않습니다.

아래 학습 예시의 CSV 경로는 직접 수집한 **서로 다른 촬영 파일**로 바꿉니다. 일반 수집 CSV에는 개인별 기준 열이 없으므로 `--normalization raw`를 지정합니다. 개인 기준 열을 포함한 진단 CSV는 기본값 `personal_baseline`을 사용할 수 있습니다.

```powershell
python model_training/train_model.py --train path/to/train.csv --validation path/to/validation.csv --test path/to/test.csv --normalization raw --epochs 30
```

학습·검증·테스트의 파일 또는 촬영 세션 중복은 거부합니다. 스케일러는 학습 데이터로 맞추고 임계값은 검증 세션으로 선택하며, 테스트 세션은 두 과정에서 제외합니다. 결과는 `backend/ai_models/candidates/<실행시간>/`에 저장하고 기본 배포 모델을 자동 교체하지 않습니다. 후보를 실행하려면 `DROWSINESS_MODEL_DIR=ai_models/candidates/<생성된 폴더>`를 설정하고 서버를 재시작합니다. 설정을 제거하면 기본 배포 모델로 돌아갑니다.

저장된 진단 CSV의 판정 규칙 비교는 `python model_training/compare_decisions.py <진단.csv>`로 실행합니다. 이는 기록된 EAR·점수를 재사용하며 모델 재추론이나 시간 윈도우 변경 효과를 검증하지 않습니다.

배포 모델의 촬영 검증 기록은 v4 정책 기준이며 v7 실사용 성능을 뜻하지 않습니다. 다른 사용자·새 촬영과 실제 도로 안전성에 대한 검증은 완료되지 않았습니다. 기존 모델의 학습 FPS 메타데이터가 없어 30 FPS 가정을 사용하고, 낮은 카메라 FPS의 빠른 깜빡임은 보간으로 복원할 수 없습니다. 비교 결과와 개발 이력은 [모델 비교](model_training/model_comparison.md), [개선 진행 기록](model_training/improvement_progress.md)에 있습니다.

## 테스트

프로젝트 루트의 활성화된 가상환경에서 백엔드 테스트를 실행합니다. 개별 테스트는 하드웨어·모델을 대체해 검사하며, 실제 웹캠·시리얼·추론 시연은 별도로 확인해야 합니다.

```powershell
cd backend
python -m unittest discover -s tests -v
```

`test_usb_camera.py`의 Linux 장치 탐색 테스트는 심볼릭 링크로 장치를 재현합니다. Windows에서 링크 생성 권한이 없으면 `WinError 1314`로 실패하므로 해당 검증은 링크 생성이 가능한 환경이나 Linux CI에서 실행합니다.

C++ 컴파일러와 Bash가 있는 환경의 프로젝트 루트에서는 `bash arduino/test/run.sh`로 보드 없이 펌웨어를 검사합니다. GitHub Actions는 관련 백엔드·Arduino 파일 변경 시 백엔드 테스트와 일부 Ruff 검사, 펌웨어 호스트 테스트, Uno 컴파일을 수행합니다.

## 관련 문서

- [Android 앱 연동·상태·오류 계약](backend/APPLICATION_INTEGRATION.md)
- [Arduino 배선·프로토콜·실물 확인](arduino/README.md)
- [전체 기능 분석 (2026-10-16)](NSND_전체기능_분석_2026-10-16.md)
- [기록 복구·경보·GPS·관측 통계 개선](NSND_1-6_개선결과_2026-10-16.md)
- [사건 위치·거리 영속화 등 소프트웨어 구현](NSND_소프트웨어_구현결과_2026-10-16.md)
- [자동 SMS 정책 변경 (2026-10-17)](NSND_SMS_정책변경_2026-10-17.md)
