"""JWT 및 비밀번호 유틸리티."""

from datetime import datetime, timedelta, timezone
import hashlib
import hmac

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import get_settings

settings = get_settings()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def storage_account_key(user) -> str:
    """Opaque identity without changing existing DBs; bcrypt salt distinguishes reused IDs.

    Stable across logins. A future password/key rotation needs an explicit cache migration.
    Neither the password hash nor signing key is included in the token.
    """
    identity = f"nsnd-account-v1:{user.id}:{user.hashed_password}".encode("utf-8")
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), identity, hashlib.sha256).hexdigest()


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm="HS256")


def decode_access_token(token: str) -> dict | None:
    """토큰 디코딩. 실패 시 None 반환."""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        return None
