# 🛠️ DrowsyGuard 백엔드 — 환경 구성 완료

## 프로젝트 구조

```
drowsy_detection/backend/
├── .env                    # 환경변수 (gitignored)
├── .env.example            # 환경변수 템플릿
├── .gitignore
├── requirements.txt        # Python 의존성
├── .venv/                  # 가상환경
└── app/
    ├── __init__.py
    ├── main.py             # FastAPI 엔트리포인트
    ├── config.py           # 환경변수 → Settings 클래스
    ├── database.py         # SQLAlchemy Async 엔진 + 세션
    ├── models.py           # DB 모델 (User, DrivingSession, DetectionLog)
    ├── schemas.py          # Pydantic 요청/응답 스키마
    ├── auth.py             # JWT + bcrypt 유틸리티
    ├── dependencies.py     # 인증 의존성 (get_current_user)
    └── routers/
        ├── __init__.py
        └── auth.py         # 인증 API 라우터 (/auth/*)
```

## 기술 스택 (설치 완료)

| 패키지 | 용도 |
|--------|------|
| FastAPI 0.115.12 | 웹 프레임워크 |
| Uvicorn 0.34.3 | ASGI 서버 |
| SQLAlchemy 2.0.41 + aiosqlite | 비동기 ORM + SQLite |
| python-jose 3.4.0 | JWT 토큰 |
| passlib + bcrypt | 비밀번호 해싱 |
| pydantic-settings | 환경변수 관리 |

## 구현된 API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/health` | 헬스체크 |
| `POST` | `/auth/register` | 회원가입 |
| `POST` | `/auth/login` | 로그인 → JWT 발급 |
| `GET` | `/auth/me` | 내 정보 조회 |
| `PUT` | `/auth/contacts` | 비상 연락처 수정 |

## DB 테이블

| 테이블 | 설명 |
|--------|------|
| `users` | 사용자 정보 (이름, 아이디, 비밀번호 해시, 비상연락처) |
| `driving_sessions` | 운전 세션 (시작/종료 시각) |
| `detection_logs` | 졸음 감지 로그 (EAR, pred_score, drowsy_level) |

## 서버 실행 방법

```powershell
#cd drowsy_detection\backend
cd backend
.venv\Scripts\activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

> Swagger UI: `http://localhost:8000/docs`

## 실제 앱의 기본 감지 설정

일반 서버 실행은 `personal-ear-v1` 모델과 v7 단계 로직을 사용합니다. 모델의 촬영 검증 기록은 v4 기준이며 현재 v7의 실사용 성능을 뜻하지 않습니다.
배포용 모델·스케일러·메타데이터는 `backend/ai_models/released/personal-ear-v1/`에 있습니다.
`DROWSINESS_MODEL_DIR`를 설정한 경우 해당 모델을 우선하며, 카메라는 서버에 직결된 웹캠을 사용합니다.
앱의 기존 `/detection/start`와 WebSocket 결과 경로에 개선된 처리가 연결되어 있습니다.
백엔드와 `realtime_detect.py`는 동일한 처리기를 사용합니다.

실행·보정 화면·경고·복구와 Android Kotlin 앱 연결 계약은 [앱 적용 안내](backend/APPLICATION_INTEGRATION.md)를 참고하세요.
추가 보완한 누락 기록 복구·서비스 경보·SMS 정책·GPS 동기화·관측 통계는 [1~6번 개선 결과](NSND_1-6_개선결과_2026-10-16.md)에 정리했습니다.
2026-10-16에 추가한 사건 위치 동기화·거리 영속화·SMS 결과 이력과 수정 사항은 [소프트웨어 구현 결과](NSND_소프트웨어_구현결과_2026-10-16.md)에 정리했습니다.

## 졸음 감지 사용 및 재학습

감지를 시작하면 최소 3초 동안 개인별 좌우 눈 EAR 기준을 보정합니다. 이때 눈을 뜨고 정면을 봐야 합니다. 보정 후에는 눈 감김 지속 시간과 LSTM 점수를 함께 사용하며, 눈을 다시 뜬 상태가 0.5초 유지되면 다음 독립 사건을 셀 수 있습니다. 이 조건만으로 경고 단계나 앱 경고음이 해제되지는 않습니다.

양쪽 눈 감김(개인 EAR 기준 72% 이하) 또는 모델 신호와 양쪽 눈 처짐(80% 이하)이 1.25초 지속되면 사건 1회로 인정합니다. 최근 30초의 사건 2회는 2단계, 2단계 이후 새 사건은 3단계입니다. 양쪽 눈 감김 3초는 즉시 3단계 긴급 조건입니다. 모델 점수 크기로 심각도를 결정하지 않습니다. 재개방 0.5초가 확인돼야 다음 사건을 셀 수 있습니다. 1단계는 정상 관측 10초 후 해제합니다. v7에서는 2·3단계 모두 사용자가 경보를 중지하고 앱이 `POST /detection/alarm/acknowledge`를 호출한 뒤 정상 상태 10초가 유지되면 바로 0단계로 회복합니다. 중지 확인 전 정상 시간은 포함하지 않습니다. 회복 중 0.5초 미만의 짧은 깜빡임은 허용하며 0단계 회복 시 사건 이력을 비웁니다. 앱의 경고음은 사용자 중지 버튼으로 제어합니다. 자세한 계약은 `backend/APPLICATION_INTEGRATION.md`에 있습니다.

실시간 입력과 재학습 입력은 같은 `EarSequenceBuffer`를 사용해 최근 약 0.97초 EAR를 30개로 보간합니다. 보간은 새로운 카메라 관측을 만들지 않으며, 10 FPS에서 빠른 깜빡임을 복원할 수는 없습니다. 기존 모델에 실제 학습 FPS 메타데이터가 없어 기존 학습 코드의 30 FPS 가정을 사용합니다. 얼굴·눈 관측이 끊기면 입력 윈도우를 다시 준비합니다.

새 학습 데이터는 촬영 세션별로 구분되어 `model_training/ear_sessions.csv`에 저장됩니다. 정상과 졸음 데이터를 여러 번 나누어 수집한 뒤 재학습하세요.

```powershell
python model_training/collect_data.py
python model_training/train_model.py --help
```

현재 학습 CLI는 타임스탬프와 정답 라벨이 있는 CSV를 요구합니다. 기존 2열 `ear_data.csv`는 실제 시간과 촬영 세션을 복원할 수 없어 새 학습·검증에는 사용하지 않습니다.

수집 스크립트의 두 번째 촬영 단계는 정답 키 입력 전까지 저장하지 않습니다. 촬영 창을 선택하고 눈 감기 시작 시 `1`, 다시 뜰 때 `0`, 라벨이 애매하면 `u`를 누릅니다. 따라서 눈을 뜬 프레임에 졸음 라벨을 일괄 적용하지 않습니다. 재학습 결과는 `backend/ai_models/candidates/<실행시간>/`에 모델·스케일러·입력 설정을 묶어서 저장하며, 현재 사용하는 모델은 자동 교체하지 않습니다.

저장된 기록으로 판정 규칙을 비교할 수 있습니다.

```powershell
python model_training/compare_decisions.py backend/diagnostics/session_8_20261013_000911.csv
```

이 비교는 기록된 EAR·모델 점수를 그대로 사용하므로 모델 재추론이나 변경된 시간 윈도우의 효과까지 검증하지 않습니다. 규칙 개발에 사용한 기록에서의 개선 수치는 독립 테스트 성능이 아닙니다.

### 모델 재학습 및 후보 실행

학습 스크립트는 pandas 없이 CSV를 읽습니다. 모델이 예측한 점수가 아닌 사용자가 입력한 정답 라벨을 사용하며, 알 수 없는 라벨이나 관측 실패에서 입력을 끊습니다. 임계값은 검증 세션만으로 선택하고 테스트 세션은 학습과 임계값 선택에서 제외합니다. 개인별 입력 보정은 진단 CSV의 좌우 눈 기준을 사용합니다.

```powershell
python model_training/train_model.py --train backend/diagnostics/session_7_20261012_235458.csv --validation backend/diagnostics/session_8_20261013_000911.csv --test backend/diagnostics/session_9_20261013_002920.csv --epochs 30
```

일반 수집 CSV에는 개인별 기준 열이 없으므로 `--normalization raw`를 지정하고 서로 다른 촬영 파일을 학습·검증·테스트로 지정합니다. 타임스탬프가 없거나 파일·촬영 세션이 겹치는 분할은 거부합니다.

실제로 학습한 후보는 `backend/ai_models/candidates/20261013_195253_465521/`에 보존하고, 검증한 묶음은 기본 배포 폴더로 복사했습니다. 기본 실행에는 후보 환경변수가 필요하지 않습니다. 실험 후보를 명시적으로 선택하려는 경우에만 다음 명령을 사용합니다.

```powershell
cd backend
$env:DROWSINESS_MODEL_DIR = (Resolve-Path "ai_models/candidates/20261013_195253_465521").Path
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

다른 터미널의 프로젝트 루트에서 `python backend/test_client.py`를 실행합니다. 실제 모델 경로, 입력 보정 방식 `personal_baseline`, 임계값 `0.02`, 코드 버전이 출력됩니다. 이전 서버 코드는 테스트 시작 시 거부합니다. `/health`에서도 동일한 정보를 확인할 수 있습니다.

현재 버전은 `event-risk-v7`이며 최소 사건 진입 시간은 `1.25초`로 출력되어야 합니다. v7에서는 회복 중 0.5초 미만의 짧은 깜빡임이 10초 타이머를 초기화하지 않습니다. 0단계 복귀는 양쪽 눈이 다시 열린 상태에서만 완료하며, 0.5초 이상 눈 처짐·눈 감김이나 관측 소실은 회복 시간을 끊습니다. 모델 파일·학습·추론 임계값과 사건 진입 조건은 유지합니다. 테스트 클라이언트의 `m`은 경보 중지 확인 API를 호출합니다. 최종 단계는 위험 이력·회복 대기를 포함하므로 눈 감김 프레임 정답과 모델 정확도를 구분해야 합니다. 전체 진행 상태는 `model_training/improvement_progress.md`, 이전 v3/v4 조건 비교는 `model_training/entry_rule_comparison.md`에 기록했습니다.

기존 모델로 되돌리려면 서버를 종료하고 같은 터미널에서 `$env:DROWSINESS_MODEL_DIR = (Resolve-Path "ai_models").Path`를 설정한 뒤 서버를 재시작합니다. 기본 배포 선택으로 돌아오려면 해당 환경변수와 `.env`의 같은 설정을 제거합니다. 모델과 스케일러·메타데이터는 반드시 같은 모델 폴더의 묶음으로 사용합니다.

모델 자체와 전체 판정 비교 결과는 `model_training/model_comparison.md`에 있습니다. 새 후보의 원시 모델 성능이 좋아졌지만, 동일한 개선 판정 규칙까지 적용했을 때 새 오경보 횟수는 기존 모델과 동일했습니다. 다른 사용자와 새 촬영에 대한 검증은 아직 수행하지 않았습니다.

### 진단 테스트

서버가 실행된 상태에서 프로젝트 루트의 다른 터미널에서 `python backend/test_client.py`를 실행합니다. 보정이 끝나면 `0`으로 정상 상태를 기록하고, 눈을 오래 감기 시작하면서 `1`, 다시 뜨면서 `0`을 누릅니다. 각 시험 사이에는 경고가 해제될 때까지 기다립니다. `q`로 종료하면 CSV와 요약을 저장합니다.

요약에는 원시 LSTM 점수 기준과 화면의 최종 경고 단계 기준의 정확도·정밀도·재현율이 따로 표시됩니다. 최종 판정 프레임 지표는 눈을 뜬 뒤 경고가 남아 있는 회복 구간도 오류에 포함합니다. 회복 중인 기존 경고는 정상 구간의 새 경고 횟수로 세지 않고, 경고 해제 시간을 따로 측정합니다. 보정 중이거나 얼굴·눈 판정이 불가능한 라벨 프레임 수는 별도로 표시합니다.

눈 감김 시험은 `1`에서 다른 라벨로 바뀌었을 때 완료됩니다. 시작 전부터 경고 중인 시험은 성공률과 지연 평가에서 제외하며, 종료 시 진행 중인 시험은 미완료로 표시합니다. 경고 지연 평균은 경고가 발생한 시험만 대상으로 하므로 성공·누락 횟수도 함께 확인해야 합니다. 시간은 실제 눈 움직임이 아니라 키 입력부터 결과 수신까지를 기준으로 측정합니다.

## ✅ 검증 결과

- 가상환경 생성 및 의존성 설치: **성공**
- 서버 기동: **성공**
- DB 테이블 자동 생성 (users, driving_sessions, detection_logs): **성공**

## 초기 개발 단계 메모 (현재 구현 현황 아님)

1. **회원가입/로그인 API 테스트** — Swagger UI에서 직접 테스트 가능
2. **SMS 발송 모듈** — `app/routers/sms.py` 추가 (알리고 API 연동)
3. **3주차 준비** — WebSocket 엔드포인트, 실시간 감지 파이프라인 통합
