from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from string import Formatter
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from core.errors import InvalidInputError

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "config" / "comms_templates.yaml"
Audience = Literal["requester", "internal", "sales"]


class Template(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    audience: Audience
    description: str
    subject: str
    body: str


class TemplateOut(Template):
    placeholders: list[str]


class TemplatesFile(BaseModel):
    templates: list[Template]


def templates_path() -> Path:
    return Path(os.environ.get("COMMS_TEMPLATES_PATH") or DEFAULT_PATH)


@lru_cache(maxsize=8)
def _load(path: str, mtime_ns: int) -> tuple[TemplateOut, ...]:
    try:
        parsed = TemplatesFile.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise InvalidInputError(f"Cannot load comms templates from {path}: {exc}", code="bad_templates") from exc
    out = []
    for template in parsed.templates:
        names = {n for text in (template.subject, template.body) for _, n, _, _ in Formatter().parse(text) if n}
        out.append(TemplateOut(**template.model_dump(), placeholders=sorted(names)))
    return tuple(out)


def load_templates(audience: str | None = None) -> list[TemplateOut]:
    path = templates_path()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError as exc:
        raise InvalidInputError(f"Comms templates file not found: {path}", code="bad_templates") from exc
    templates = list(_load(str(path), mtime))
    return [t for t in templates if audience is None or t.audience == audience]
