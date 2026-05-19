"""인증 관련 API 라우터.

엔드포인트:
  POST /auth/register  — 회원가입
  POST /auth/login     — 로그인 (JWT 발급)
  GET  /auth/me        — 내 정보 조회
  PUT  /auth/contacts  — 비상 연락처 수정
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token, hash_password, verify_password
from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas import ContactUpdate, LoginRequest, Token, UserCreate, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(body: UserCreate, db: AsyncSession = Depends(get_db)):
    """회원가입 — 이름, ID, PW, 비상연락처."""
    # 중복 확인
    existing = await db.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 존재하는 아이디입니다")

    user = User(
        username=body.username,
        name=body.name,
        hashed_password=hash_password(body.password),
        emergency_contact=body.emergency_contact,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=Token)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """로그인 → JWT 토큰 발급."""
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 올바르지 않습니다",
        )

    token = create_access_token({"sub": str(user.id)})
    return Token(access_token=token)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """현재 로그인된 사용자 정보 조회."""
    return current_user


@router.put("/contacts", response_model=UserResponse)
async def update_contacts(
    body: ContactUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """비상 연락처 수정."""
    current_user.emergency_contact = body.emergency_contact
    await db.commit()
    await db.refresh(current_user)
    return current_user
