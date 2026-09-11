"""FastAPI 의존성 모듈."""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import decode_access_token
from app.database import get_db
from app.models import LoginSession, User

security = HTTPBearer()


async def resolve_current_user(token: str, db: AsyncSession) -> User:
    """JWT와 서버의 단일 활성 로그인 세션을 함께 검증합니다."""
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="로그인이 만료되었습니다")

    user_id = payload.get("sub")
    token_id = payload.get("sid")
    if user_id is None or token_id is None:
        raise HTTPException(status_code=401, detail="다시 로그인해주세요")

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="사용자를 찾을 수 없습니다")

    login_result = await db.execute(
        select(LoginSession).where(
            LoginSession.user_id == user.id,
            LoginSession.token_id == str(token_id),
        )
    )
    if login_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=401,
            detail="다른 기기에서 로그인하여 현재 로그인이 종료되었습니다",
        )
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """JWT 토큰에서 현재 사용자를 추출하는 의존성."""
    return await resolve_current_user(credentials.credentials, db)
