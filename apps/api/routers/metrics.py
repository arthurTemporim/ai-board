from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from apps.api.deps import SessionDep
from core.services.metrics import compute_metrics

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get(
    "",
    summary="Metrics computed from the audit log: time to first decision, acceptance, duplicates, score overrides",
    description="Every number comes from real recorded decisions. The manual baseline is an assumption and is returned "
    "marked as such, with the definition of each metric.",
)
def metrics(session: SessionDep) -> dict[str, Any]:
    return compute_metrics(session)
