"""MockLLM: deterministic, schema-valid outputs with no network and no API key (MOCK_AI=1).

Outputs are a pure function of the prompt's variables, built from simple text overlap so the
whole system behaves plausibly in demos and tests. Nothing in the text of a request is ever
interpreted as an instruction, and every result is validated against the real schema.
"""

from __future__ import annotations

import re
from typing import Any

from ai.llm.factory import LLMResult
from ai.llm.registry import RenderedPrompt

_WORD = re.compile(r"[a-z0-9]{3,}")
_STOP = {"the", "and", "for", "that", "with", "this", "our", "are", "can", "not", "all", "from", "have", "your", "you"}
MOCK_NOTE = "Deterministic mock output (MOCK_AI=1)."


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _tokens(*texts: str) -> set[str]:
    return {w for text in texts for w in _WORD.findall(text.lower()) if w not in _STOP}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def _seed(prompt: RenderedPrompt) -> int:
    return int(prompt.input_hash[:12], 16)


def _vague(title: str, description: str) -> bool:
    return len(_tokens(title, description)) < 5


def _analyze_need(v: dict[str, Any], _seed_value: int) -> dict[str, Any]:
    title, description = str(v["card_title"]), str(v["card_description"])
    vague = _vague(title, description)
    return {
        "underlying_need": _clip(f"Be able to: {title.rstrip('. ')}", 700),
        "problem_statement": _clip(f"Customers cannot currently {title.strip().rstrip('.').lower()}.", 700),
        "is_actionable": not vague,
        "clarifying_questions": ["What problem are you trying to solve?", "Who is affected, and how often?"]
        if vague
        else [],
        "confidence": 0.35 if vague else 0.8,
        "rationale": MOCK_NOTE,
    }


def _judge_duplicate(v: dict[str, Any], _seed_value: int) -> dict[str, Any]:
    a = _tokens(str(v["card_a_title"]), str(v["card_a_description"]))
    b = _tokens(str(v["card_b_title"]), str(v["card_b_description"]))
    overlap = _jaccard(a, b)
    verdict = "duplicate" if overlap >= 0.5 else "related" if overlap >= 0.2 else "distinct"
    shared = sorted(a & b)[:3]
    return {
        "verdict": verdict,
        "evidence": [f"shared terms: {', '.join(shared)}"] if shared else [],
        "confidence": round(0.5 + min(overlap, 0.4), 2),
        "rationale": MOCK_NOTE,
    }


def _group_problem(v: dict[str, Any], _seed_value: int) -> dict[str, Any]:
    title, description = str(v["card_title"]), str(v["card_description"])
    card = _tokens(title, description)
    best_id, best = None, 0.0
    for problem in v["existing_problems"]:
        score = _jaccard(card, _tokens(str(problem.get("title", "")), str(problem.get("statement", ""))))
        if score > best:
            best_id, best = problem.get("id"), score
    base = {"existing_problem_id": None, "new_problem_title": None, "new_problem_statement": None}
    if best_id is not None and best >= 0.15:
        return {
            **base,
            "decision": "existing",
            "existing_problem_id": best_id,
            "confidence": 0.7,
            "rationale": MOCK_NOTE,
        }
    if _vague(title, description):
        return {**base, "decision": "none", "confidence": 0.3, "rationale": MOCK_NOTE}
    return {
        **base,
        "decision": "new",
        "new_problem_title": _clip(title, 100),
        "new_problem_statement": _clip(f"Customers need a way to {title.strip().rstrip('.').lower()}.", 600),
        "confidence": 0.6,
        "rationale": MOCK_NOTE,
    }


def _score_request(v: dict[str, Any], seed: int) -> dict[str, Any]:
    criteria = []
    for index, criterion in enumerate(v["rubric"]):
        key = criterion["key"] if isinstance(criterion, dict) else str(criterion)
        criteria.append({"key": key, "rating": 1 + ((seed >> (index * 3)) % 5), "rationale": "Mock rating."})
    return {"criteria": criteria, "confidence": 0.5, "rationale": MOCK_NOTE}


def _suggest_tags(v: dict[str, Any], _seed_value: int) -> dict[str, Any]:
    text = f"{v['card_title']} {v['card_description']}".lower()
    tags = [
        {"name": str(tag), "is_existing": True, "reason": f"The request mentions '{tag}'."}
        for tag in v["existing_tags"]
        if str(tag).lower() in text
    ][:5]
    return {"tags": tags, "confidence": 0.7 if tags else 0.4, "rationale": MOCK_NOTE}


def _decision_brief(v: dict[str, Any], _seed_value: int) -> dict[str, Any]:
    summary = v["score_summary"] if isinstance(v["score_summary"], dict) else {}
    total, top = summary.get("total"), summary.get("scale_max")
    ratio = (
        float(total) / float(top) if isinstance(total, int | float) and isinstance(top, int | float) and top else 0.5
    )
    analysis = v["analysis"]
    actionable = analysis.get("is_actionable", True) if isinstance(analysis, dict) else True
    recommendation = (
        "needs_info" if not actionable else "plan" if ratio >= 0.7 else "decline" if ratio < 0.3 else "review"
    )
    return {
        "summary": _clip(f"{v['card_title']}. Score ratio {ratio:.2f}, {v['vote_count']} votes.", 700),
        "recommendation": recommendation,
        "risks": [],
        "open_questions": ["What is missing to decide?"] if recommendation == "needs_info" else [],
        "confidence": 0.6,
        "rationale": MOCK_NOTE,
    }


_DRAFTS = {
    "plan": ("is being scheduled", "celebratory"),
    "review": ("is under review", "neutral"),
    "decline": ("will not be pursued for now", "apologetic"),
    "needs_info": ("needs a little more detail from you", "neutral"),
}


def _draft_update(v: dict[str, Any], _seed_value: int) -> dict[str, Any]:
    phrase, tone = _DRAFTS.get(str(v["recommendation"]), _DRAFTS["review"])
    return {
        "subject": _clip(f"Update on your request: {v['card_title']}", 110),
        "body": _clip(f"Hello, thank you for your request. It {phrase}. We will keep you informed.", 1500),
        "tone": tone,
        "confidence": 0.6,
        "rationale": MOCK_NOTE,
    }


_BUILDERS = {
    "AnalyzeNeedOutput": _analyze_need,
    "JudgeDuplicateOutput": _judge_duplicate,
    "GroupProblemOutput": _group_problem,
    "ScoreRequestOutput": _score_request,
    "SuggestTagsOutput": _suggest_tags,
    "DecisionBriefOutput": _decision_brief,
    "DraftUpdateOutput": _draft_update,
}


class MockLLM:
    def model_for(self, prompt: RenderedPrompt) -> str:
        return "mock"

    def invoke(self, prompt: RenderedPrompt) -> LLMResult:
        builder = _BUILDERS.get(prompt.output_schema.__name__)
        if builder is None:
            raise NotImplementedError(f"MockLLM has no builder for {prompt.output_schema.__name__}")
        data = builder(prompt.variables, _seed(prompt))
        return LLMResult(prompt.output_schema.model_validate(data), tokens_in=0, tokens_out=0)
