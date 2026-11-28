from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user, hash_password, make_token, verify_password, visible_plant_ids
from ..config import now_local
from ..db import get_session
from ..models import AppUser, LoginEvent, Organisation, Plant

router = APIRouter(prefix="/auth", tags=["auth"])
MAX_FAILURES, LOCKOUT = 5, timedelta(minutes=15)


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    company: str = Field(min_length=2, max_length=200)
    company_type: str = Field(pattern="^(GSO|LSS)$")


class TourIn(BaseModel):
    show: bool


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str


def user_out(u: AppUser, plants: list[str] | None = None) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "status": u.status,
            "organisation": u.organisation.name if u.organisation else None,
            "organisation_type": u.organisation.type if u.organisation else None,
            "created_at": u.created_at, "approved_at": u.approved_at, "last_login_at": u.last_login_at,
            "plants": plants, "show_tour": u.show_tour is not False}


@router.post("/register", status_code=201)
async def register(body: RegisterIn, s: AsyncSession = Depends(get_session)):
    email = body.email.lower()
    if (await s.execute(select(AppUser).where(AppUser.email == email))).first():
        raise HTTPException(409, "An account with this email already exists")
    org = (await s.execute(select(Organisation).where(func.lower(Organisation.name) == body.company.lower())
                           )).scalar_one_or_none()
    if org is None:
        org = Organisation(name=body.company.strip(), type=body.company_type)
        s.add(org)
        await s.flush()
    s.add(AppUser(email=email, name=body.name.strip(), organisation_id=org.id,
                  password_hash=hash_password(body.password), status="pending", created_at=now_local()))
    await s.commit()
    return {"status": "pending", "message": "Registered. SAE Malaysia must approve your account before you can log in."}


@router.post("/login")
async def login(body: LoginIn, request: Request, s: AsyncSession = Depends(get_session)):
    email, now = body.email.lower(), now_local()
    ip = request.client.host if request.client else None
    recent_fail = (await s.execute(select(func.count()).select_from(LoginEvent).where(
        LoginEvent.email == email, LoginEvent.success.is_(False), LoginEvent.ts > now - LOCKOUT))).scalar_one()
    if recent_fail >= MAX_FAILURES:
        raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes.")
    user = (await s.execute(select(AppUser).where(AppUser.email == email))).scalar_one_or_none()
    ok = user is not None and verify_password(body.password, user.password_hash)
    s.add(LoginEvent(email=email, user_id=user.id if user else None, ts=now, ip=ip, success=ok))
    if not ok:
        await s.commit()
        raise HTTPException(401, "Wrong email or password")
    if user.status == "pending":
        await s.commit()
        raise HTTPException(403, "Your account is waiting for admin approval")
    if user.status != "active":
        await s.commit()
        raise HTTPException(403, "Your account is disabled")
    user.last_login_at = now
    await s.commit()
    return {"token": make_token(user), "user": user_out(user)}


@router.get("/me")
async def me(user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    ids = await visible_plant_ids(user, s)
    plants = (await s.execute(select(Plant.mnemonic).where(Plant.id.in_(ids)).order_by(Plant.mnemonic))).scalars().all()
    return user_out(user, list(plants))


@router.put("/me/tour")
async def set_tour(body: TourIn, user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Save whether the guided tour opens at login. Stored per user so it follows them across browsers."""
    u = await s.get(AppUser, user.id)
    u.show_tour = body.show
    await s.commit()
    return {"show_tour": u.show_tour}


class PasswordIn(BaseModel):
    current: str
    new: str = Field(min_length=10, max_length=200)


@router.put("/me/password")
async def change_password(body: PasswordIn, user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Change your own password (for example after the super admin gave you a temporary one)."""
    u = await s.get(AppUser, user.id)
    if not verify_password(body.current, u.password_hash):
        raise HTTPException(400, "Your current password is wrong")
    u.password_hash = hash_password(body.new)
    await s.commit()
    return {"ok": True}
