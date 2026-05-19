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
cd drowsy_detection\backend
.venv\Scripts\activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

> Swagger UI: `http://localhost:8000/docs`

## ✅ 검증 결과

- 가상환경 생성 및 의존성 설치: **성공**
- 서버 기동: **성공**
- DB 테이블 자동 생성 (users, driving_sessions, detection_logs): **성공**

## 📋 다음 단계 (2주차 기능 개발)

1. **회원가입/로그인 API 테스트** — Swagger UI에서 직접 테스트 가능
2. **SMS 발송 모듈** — `app/routers/sms.py` 추가 (알리고 API 연동)
3. **3주차 준비** — WebSocket 엔드포인트, 실시간 감지 파이프라인 통합
