from __future__ import annotations

from fastapi import APIRouter

from apps.api.deps import SessionDep
from apps.api.schemas import UserRead
from core.repositories.boards import UserRepository

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserRead])
def list_users(session: SessionDep):
    return UserRepository(session).list()
