from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from core.db import session_scope
from core.errors import UnauthorizedError
from core.models import User
from core.repositories.boards import UserRepository


def get_session() -> Iterator[Session]:
    """One transaction per request: committed on success, rolled back on any error."""
    with session_scope() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def optional_actor_id(
    x_user_id: Annotated[int | None, Header(description="Acting user id (no authentication yet)")] = None,
) -> int | None:
    return x_user_id


def require_actor(session: SessionDep, actor_id: Annotated[int | None, Depends(optional_actor_id)]) -> User:
    if actor_id is None:
        raise UnauthorizedError("The X-User-Id header is required for this action")
    user = UserRepository(session).get(actor_id)
    if user is None:
        raise UnauthorizedError(f"Unknown user {actor_id}", code="unknown_actor")
    return user


ActorDep = Annotated[User, Depends(require_actor)]
OptionalActorDep = Annotated[int | None, Depends(optional_actor_id)]
