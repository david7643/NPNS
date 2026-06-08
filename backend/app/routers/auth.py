"""인증 관련 API 라우터."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token, hash_password, verify_password
from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas import ContactUpdate, LoginRequest, Token, UserCreate, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED, summary="회원가입")
async def register(body: UserCreate, db: AsyncSession = Depends(get_db)):
    """회원가입 — 이름, ID, PW, 비상연락처."""
    result = await db.execute(select(User).where(User.username == body.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="이미 존재하는 아이디입니다")

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


@router.post("/login", response_model=Token, summary="로그인")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """로그인 → JWT 발급."""
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다")

    token = create_access_token({"sub": str(user.id)})
    return Token(access_token=token)


@router.get("/me", response_model=UserResponse, summary="내 정보 조회")
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.put("/contacts", response_model=UserResponse, summary="비상 연락처 수정")
async def update_contacts(
    body: ContactUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.emergency_contact = body.emergency_contact
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT, summary="회원 탈퇴")
async def delete_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """현재 로그인된 계정 탈퇴 및 관련 데이터 전체 삭제."""
    from app.models import Contact, DetectionLog, DrivingSession

    # 관련된 데이터 (로그, 세션, 연락처) 모두 삭제
    await db.execute(delete(DetectionLog).where(DetectionLog.user_id == current_user.id))
    await db.execute(delete(DrivingSession).where(DrivingSession.user_id == current_user.id))
    await db.execute(delete(Contact).where(Contact.user_id == current_user.id))
    
    # 마지막으로 유저 계정 삭제
    await db.delete(current_user)
    await db.commit()

