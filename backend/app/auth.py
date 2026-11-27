from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings, now_local
from .db import get_session
from .models import AppUser, Plant, UserPlantAccess

_ph = PasswordHasher()
_bearer = HTTPBearer(auto_error=False)


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, h: str) -> bool:
    try:
        return _ph.verify(h, pw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def make_token(user: AppUser) -> str:
    cfg = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(minutes=cfg.jwt_ttl_minutes)
    return jwt.encode({"sub": str(user.id), "role": user.role, "exp": exp}, cfg.jwt_secret, algorithm="HS256")


async def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
                       s: AsyncSession = Depends(get_session)) -> AppUser:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in")
    try:
        payload = jwt.decode(creds.credentials, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired or invalid")
    user = await s.get(AppUser, int(payload["sub"]))
    if user is None or user.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account is not active")
    return user


def require_role(*roles: str):
    async def dep(user: AppUser = Depends(current_user)) -> AppUser:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed for your role")
        return user
    return dep


require_admin = require_role("gso_admin")


async def visible_plant_ids(user: AppUser, s: AsyncSession) -> set[int]:
    """GSO roles see every plant; LSS users only the plants assigned to them."""
    if user.role in ("gso_admin", "gso_user"):
        return set((await s.execute(select(Plant.id))).scalars().all())
    return set((await s.execute(select(UserPlantAccess.plant_id).where(
        UserPlantAccess.user_id == user.id))).scalars().all())


async def plant_for_user(mnemonic: str, user: AppUser, s: AsyncSession) -> Plant:
    plant = (await s.execute(select(Plant).where(Plant.mnemonic == mnemonic.lower()))).scalar_one_or_none()
    if plant is None or plant.id not in await visible_plant_ids(user, s):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plant not found")
    return plant
