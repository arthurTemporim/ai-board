"""Valid input variables for every prompt, shared by the prompt, schema and injection tests."""

from __future__ import annotations

from typing import Any

RUBRIC = [
    {"key": "customer_impact", "label": "Customer impact", "anchors": {"1": "minor annoyance", "5": "blocks adoption"}},
    {"key": "strategic_fit", "label": "Strategic fit", "anchors": {"1": "off strategy", "5": "core to strategy"}},
    {"key": "effort_fit", "label": "Effort", "anchors": {"1": "months", "5": "days"}},
]

TITLE = "Export report tables to CSV"
DESCRIPTION = "Finance teams rebuild the same spreadsheet by hand from our dashboard every month."

SAMPLE_VARIABLES: dict[str, dict[str, Any]] = {
    "analyze_need": {
        "requester_type": "customer",
        "card_title": TITLE,
        "card_description": DESCRIPTION,
        "feedback_quotes": ["My finance team wants the raw numbers."],
    },
    "judge_duplicate": {
        "similarity_score": 0.83,
        "card_a_title": "Support SAML single sign-on",
        "card_a_description": "IT requires SAML sign-on before we can roll the product out.",
        "card_b_title": "Let staff log in with Okta",
        "card_b_description": "Nobody wants another password to manage.",
    },
    "group_problem": {
        "card_title": "Audit log of admin actions",
        "card_description": "Our auditors want to see who changed which setting.",
        "existing_problems": [
            {
                "id": 7,
                "title": "Security and compliance gaps",
                "statement": "Enterprise buyers cannot prove compliance.",
            }
        ],
    },
    "score_request": {
        "vote_count": 6,
        "rubric": RUBRIC,
        "card_title": TITLE,
        "card_description": DESCRIPTION,
        "feedback_quotes": ["We cannot close the books without it."],
    },
    "suggest_tags": {
        "card_title": "Dark mode for night shifts",
        "card_description": "The bright ux tires our operators.",
        "existing_tags": ["ux", "security", "reporting"],
    },
    "decision_brief": {
        "vote_count": 6,
        "score_summary": {"total": 7.5, "scale_max": 10},
        "card_title": TITLE,
        "card_description": DESCRIPTION,
        "analysis": {"underlying_need": "Get data into spreadsheets.", "is_actionable": True},
        "related_requests": [{"title": "Scheduled report emails", "verdict": "related"}],
    },
    "draft_update": {
        "audience": "requester",
        "recommendation": "review",
        "card_title": TITLE,
        "decision_summary": "Useful for finance teams; to be compared with other reporting work.",
    },
}

PROMPT_NAMES = sorted(SAMPLE_VARIABLES)
