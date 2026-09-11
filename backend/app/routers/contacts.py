"""긴급 연락처 CRUD 라우터."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Contact, User
from app.schemas import ContactCreate, ContactResponse, SavedContactUpdate

router = APIRouter(prefix="/contacts", tags=["contacts"])


def normalize_phone(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


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
    result = await db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.user_id == current_user.id)
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=404, detail="연락처를 찾을 수 없습니다")

    await db.delete(contact)
    await db.commit()
