"""긴급 연락처 CRUD 라우터."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Contact, User
from app.schemas import ContactCreate, ContactResponse, SavedContactUpdate, SmsSettingsUpdate

router = APIRouter(prefix="/contacts", tags=["contacts"])


def normalize_phone(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


async def lock_contact_settings(db, user_id):
    # Serialize count/change operations in SQLite, including simultaneous last deletes.
    await db.execute(update(User).where(User.id == user_id).values(auto_sms_enabled=User.auto_sms_enabled))


async def settings_snapshot(db, user_id):
    contacts = (await db.execute(select(Contact).where(Contact.user_id == user_id).order_by(Contact.id))).scalars().all()
    # Keep the old response shape, but an old opt-out flag no longer controls SMS.
    return {"enabled": bool(contacts), "automatic_sms_required": True,
            "minimum_contacts": 1, "contact_count": len(contacts),
            "contacts": [ContactResponse.model_validate(contact) for contact in contacts]}


@router.get("/sms-settings")
async def get_sms_settings(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await settings_snapshot(db, current_user.id)


@router.put("/sms-settings")
async def update_sms_settings(body: SmsSettingsUpdate, current_user: User = Depends(get_current_user),
                              db: AsyncSession = Depends(get_db)):
    await lock_contact_settings(db, current_user.id)
    if not body.enabled:
        raise HTTPException(status_code=409, detail="3단계 위험 SMS는 필수 자동 발송이며 끌 수 없습니다")
    snapshot = await settings_snapshot(db, current_user.id)
    if not snapshot["contact_count"]:
        raise HTTPException(status_code=409, detail="자동 SMS를 보내려면 연락처를 한 명 이상 등록해주세요")
    await db.execute(update(User).where(User.id == current_user.id).values(auto_sms_enabled=True))
    await db.commit()
    return await settings_snapshot(db, current_user.id)


@router.get("", response_model=list[ContactResponse])
async def list_contacts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Contact).where(Contact.user_id == current_user.id).order_by(Contact.id)
    )
    return result.scalars().all()


@router.post("", response_model=ContactResponse, status_code=status.HTTP_201_CREATED)
async def create_contact(
    body: ContactCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await lock_contact_settings(db, current_user.id)
    normalized_phone = normalize_phone(body.phone)
    if len(normalized_phone) not in range(8, 16):
        raise HTTPException(status_code=400, detail="전화번호는 8~15자리 숫자여야 합니다")
    existing_result = await db.execute(select(Contact).where(Contact.user_id == current_user.id))
    if any(normalize_phone(item.phone) == normalized_phone for item in existing_result.scalars().all()):
        raise HTTPException(status_code=409, detail="이미 등록된 전화번호입니다")

    contact = Contact(
        user_id=current_user.id,
        name=body.name.strip(),
        phone=normalized_phone,
        message=body.message.strip(),
    )
    db.add(contact)
    await db.execute(update(User).where(User.id == current_user.id).values(auto_sms_enabled=True))
    await db.commit()
    await db.refresh(contact)
    return contact


@router.put("/{contact_id}", response_model=ContactResponse)
async def update_contact(
    contact_id: int,
    body: SavedContactUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await lock_contact_settings(db, current_user.id)
    result = await db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.user_id == current_user.id)
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=404, detail="연락처를 찾을 수 없습니다")

    normalized_phone = normalize_phone(body.phone)
    if len(normalized_phone) not in range(8, 16):
        raise HTTPException(status_code=400, detail="전화번호는 8~15자리 숫자여야 합니다")
    others_result = await db.execute(
        select(Contact).where(Contact.user_id == current_user.id, Contact.id != contact_id)
    )
    if any(normalize_phone(item.phone) == normalized_phone for item in others_result.scalars().all()):
        raise HTTPException(status_code=409, detail="이미 등록된 전화번호입니다")

    contact.name = body.name.strip()
    contact.phone = normalized_phone
    contact.message = body.message.strip()
    await db.commit()
    await db.refresh(contact)
    return contact


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(
    contact_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await lock_contact_settings(db, current_user.id)
    result = await db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.user_id == current_user.id)
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=404, detail="연락처를 찾을 수 없습니다")

    remaining = await db.scalar(select(func.count(Contact.id)).where(Contact.user_id == current_user.id))
    if remaining <= 1:
        raise HTTPException(status_code=409, detail="비상 연락처는 최소 1개를 유지해야 하므로 마지막 연락처는 삭제할 수 없습니다")
    await db.delete(contact)
    await db.commit()
