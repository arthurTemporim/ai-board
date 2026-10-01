"""State schema, step names and configuration for the triage graph."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from typing import Annotated, Any, TypedDict


def merge_dicts(left: dict | None, right: dict | None) -> dict:
    """Reducer: later updates add to (and override) earlier keys instead of replacing the dict."""
    return {**(left or {}), **(right or {})}


def concat(left: list | None, right: list | None) -> list:
    return [*(left or []), *(right or [])]


class WorkflowState(TypedDict, total=False):
    """Everything a run knows. Only JSON-safe values: it is checkpointed to SQLite after each node."""

    card_id: int
    board_id: int
    run_id: int  # id of the workflow AIRun; also the LangGraph thread id
    steps_requested: list[str] | None  # canonical step names; None means every step
    results: Annotated[dict[str, Any], merge_dicts]  # step -> its result
    statuses: Annotated[dict[str, str], merge_dicts]  # step -> succeeded | failed | skipped | ...
    errors: Annotated[list[dict[str, str]], concat]  # [{"step", "error"}]
    human_decisions: Annotated[list[dict[str, Any]], concat]
    # routing, set by nodes and read by the conditional edges
    route: str
    checkpoint_request: dict[str, Any]  # what human_checkpoint should ask
    resume_to: str  # where to continue after a checkpoint
    merge_target: dict[str, Any]  # the duplicate to propose merging with
    outcome: str  # completed | needs_info | duplicate_proposed | failed


# Steps a caller can request, in execution order. human_checkpoint, needs_info and merge_proposal
# are not steps: they are routes the graph takes because of what earlier steps found.
STEPS: tuple[str, ...] = (
    "validate_intake",
    "analyze_need",
    "find_related",
    "group_problem",
    "score",
    "suggest_tags",
    "decision_brief",
    "draft_comms",
)

ALIASES: dict[str, str] = {
    "intake": "validate_intake",
    "validate": "validate_intake",
    "analyze": "analyze_need",
    "need": "analyze_need",
    "related": "find_related",
    "duplicates": "find_related",
    "group": "group_problem",
    "problem": "group_problem",
    "tags": "suggest_tags",
    "brief": "decision_brief",
    "comms": "draft_comms",
    "draft": "draft_comms",
}

# Steps whose result another step needs. If it was not produced in this run it is loaded from the
# card's most recent earlier run; if there is none, the dependent step is skipped.
DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "decision_brief": ("analyze_need", "score"),
    "draft_comms": ("decision_brief",),
}
OPTIONAL_DEPENDENCIES: dict[str, tuple[str, ...]] = {"decision_brief": ("find_related",)}


def normalise_steps(steps: list[str] | str | None) -> list[str] | None:
    """Accept names or aliases (comma separated or a list); None or empty means every step."""
    if steps is None:
        return None
    raw = steps.split(",") if isinstance(steps, str) else list(steps)
    names = [s.strip().lower() for s in raw if s and s.strip()]
    if not names or names == ["all"]:
        return None
    resolved: list[str] = []
    for name in names:
        name = ALIASES.get(name, name)
        if name not in STEPS:
            known = ", ".join(sorted({*STEPS, *ALIASES}))
            raise ValueError(f"Unknown step '{name}'. Known steps and aliases: {known}")
        if name not in resolved:
            resolved.append(name)
    return [s for s in STEPS if s in resolved]  # always run in graph order


@dataclass(frozen=True)
class GraphConfig:
    """Tunable thresholds; every field can be set with a GRAPH_<NAME> environment variable."""

    top_k: int = 5  # most similar cards to judge
    min_similarity: float = 0.2  # candidates below this embedding score are not judged
    link_threshold: float = 0.6  # minimum judge confidence to create a suggested link at all
    merge_threshold: float = 0.85  # a "duplicate" at or above this becomes a merge proposal
    # A "duplicate" between link_threshold and merge_threshold is not trusted: a person decides.
    analysis_min_confidence: float = 0.4  # an actionable analysis below this asks a person to confirm
    comms_audiences: tuple[str, ...] = ("requester",)
    # Cost and safety limits
    max_llm_calls: int = 40  # model calls one run may make (a full run with top_k 5 makes about 11)
    max_text_chars: int = 4000  # longest card text sent to a model; the rest is cut
    max_feedback_quotes: int = 10  # feedback quotes sent to a model, each cut to 500 characters
    tool_timeout: float = 120.0  # seconds one MCP tool call may take
    run_timeout: float = 900.0  # seconds one run may be active before it is failed

    def __post_init__(self) -> None:
        for name in ("min_similarity", "link_threshold", "merge_threshold", "analysis_min_confidence"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.link_threshold > self.merge_threshold:
            raise ValueError("link_threshold cannot be higher than merge_threshold")
        if not 1 <= self.top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        if not 1 <= self.max_llm_calls <= 500:
            raise ValueError("max_llm_calls must be between 1 and 500")
        if not 200 <= self.max_text_chars <= 50_000:
            raise ValueError("max_text_chars must be between 200 and 50000")
        if not 0 <= self.max_feedback_quotes <= 50:
            raise ValueError("max_feedback_quotes must be between 0 and 50")
        if self.tool_timeout <= 0 or self.run_timeout <= 0:
            raise ValueError("timeouts must be positive")
        bad = [a for a in self.comms_audiences if a not in ("requester", "internal", "sales")]
        if bad or not self.comms_audiences:
            raise ValueError(f"comms_audiences must be a non-empty subset of requester, internal, sales (got {bad})")

    @classmethod
    def from_env(cls, **overrides: Any) -> GraphConfig:
        values: dict[str, Any] = {}
        for f in fields(cls):
            raw = os.environ.get(f"GRAPH_{f.name.upper()}")
            if raw is None or raw == "":
                continue
            if f.name in ("top_k", "max_llm_calls", "max_text_chars", "max_feedback_quotes"):
                values[f.name] = int(raw)
            elif f.name == "comms_audiences":
                values[f.name] = tuple(a.strip() for a in raw.split(",") if a.strip())
            else:
                values[f.name] = float(raw)
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)
