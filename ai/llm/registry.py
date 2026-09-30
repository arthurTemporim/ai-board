"""Prompt registry: loads ai/prompts/<name>/vN.md, validates it, and renders messages.

File layout
    ai/prompts/registry.yaml         which version of each prompt is active (+ hashes of released ones)
    ai/prompts/<name>/vN.md          YAML frontmatter, then a Jinja2 body with two sections:
                                     a line `=== system ===` and a line `=== user ===`

Untrusted input
    Variables declared with `untrusted: true` hold text written by customers, colleagues or earlier
    model output. render() wraps each one in a <user_data name="..."> block, neutralising any
    attempt to close it, and the loader refuses templates that place such a variable outside the
    user message. Templates therefore never wrap data themselves.

Released versions are immutable: registry.yaml stores a sha256 per released version and the
loader rejects a file that no longer matches. Change a prompt by adding vN+1.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jinja2 import StrictUndefined, TemplateSyntaxError, meta
from jinja2.sandbox import ImmutableSandboxedEnvironment
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ai.llm.errors import PromptFormatError, PromptNotFoundError, PromptVariableError

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "prompts"
REGISTRY_FILE = "registry.yaml"

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_VERSION_FILE = re.compile(r"^v(\d+)\.md$")
_SECTION = re.compile(r"^=== (system|user) ===\s*$", re.MULTILINE)
_CLOSE_TAG = re.compile(r"<(/?)user_data", re.IGNORECASE)


# ---- frontmatter -------------------------------------------------------------------------------
class VariableDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    untrusted: bool = False

    @field_validator("name")
    @classmethod
    def _identifier(cls, value: str) -> str:
        if not value.isidentifier():
            raise ValueError(f"'{value}' is not a valid variable name")
        return value


class Frontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: int = Field(ge=1)
    model: str = Field(min_length=1)
    temperature: float = Field(ge=0.0, le=1.0)
    max_tokens: int = Field(default=1024, ge=64, le=8192)
    input_variables: list[VariableDecl]
    output_schema: str = Field(description="'package.module:ClassName' of a pydantic model")

    @field_validator("name")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError("name must be lowercase letters, digits and underscores")
        return value

    @field_validator("input_variables", mode="before")
    @classmethod
    def _allow_bare_names(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [{"name": v} if isinstance(v, str) else v for v in value]
        return value

    @model_validator(mode="after")
    def _unique_variables(self) -> Frontmatter:
        names = [v.name for v in self.input_variables]
        if len(set(names)) != len(names):
            raise ValueError("input_variables contains duplicates")
        return self


# ---- loaded / rendered objects -----------------------------------------------------------------
@dataclass(frozen=True)
class PromptSpec:
    name: str
    version: int
    model: str
    temperature: float
    max_tokens: int
    variables: tuple[VariableDecl, ...]
    output_schema: type[BaseModel]
    system_template: str
    user_template: str
    sha256: str
    path: Path

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class Message:
    role: str  # "system" | "user"
    content: str


@dataclass(frozen=True)
class RenderedPrompt:
    """What to send to the model plus everything needed to log and reproduce the call."""

    name: str
    version: int
    model: str
    temperature: float
    max_tokens: int
    messages: tuple[Message, ...]
    output_schema: type[BaseModel]
    variables: dict[str, Any] = field(compare=False)
    untrusted: frozenset[str] = frozenset()
    input_hash: str = ""

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"


def wrap_untrusted(name: str, value: Any) -> str:
    """Render a value as a delimited data block the model is told never to obey."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    # A data block must not be able to close itself or open a look-alike block.
    text = _CLOSE_TAG.sub(lambda m: f"&lt;{m.group(1)}user_data", text)
    return f'<user_data name="{name}">\n{text}\n</user_data>'


def input_hash(ref: str, variables: dict[str, Any]) -> str:
    canonical = json.dumps({"prompt": ref, "variables": variables}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---- registry ----------------------------------------------------------------------------------
def _env() -> ImmutableSandboxedEnvironment:
    env = ImmutableSandboxedEnvironment(
        undefined=StrictUndefined, autoescape=False, trim_blocks=True, lstrip_blocks=True
    )
    env.filters["to_json"] = lambda v: json.dumps(v, ensure_ascii=False, indent=2, sort_keys=True)
    return env


class PromptRegistry:
    def __init__(self, root: Path | str = DEFAULT_ROOT):
        self.root = Path(root)
        self._env = _env()
        self._cache: dict[tuple[str, int], PromptSpec] = {}

    # -- registry.yaml --
    def _registry(self) -> dict[str, dict[str, Any]]:
        path = self.root / REGISTRY_FILE
        if not path.is_file():
            raise PromptFormatError(f"{path} not found")
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise PromptFormatError(f"{path}: invalid YAML: {exc}") from exc
        prompts = data.get("prompts") if isinstance(data, dict) else None
        if not isinstance(prompts, dict):
            raise PromptFormatError(f"{path}: expected a top-level 'prompts' mapping")
        for name, entry in prompts.items():
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("active"), int)
                or isinstance(entry.get("active"), bool)
            ):
                raise PromptFormatError(f"{path}: prompt '{name}' needs an integer 'active' version")
            released = entry.get("released", {})
            if not isinstance(released, dict):
                raise PromptFormatError(f"{path}: prompt '{name}': 'released' must map version -> sha256")
        return prompts

    def names(self) -> list[str]:
        return sorted(self._registry())

    def active_version(self, name: str) -> int:
        entry = self._registry().get(name)
        if entry is None:
            raise PromptNotFoundError(f"Unknown prompt '{name}'. Known: {', '.join(self.names()) or 'none'}")
        return int(entry["active"])

    def versions(self, name: str) -> list[int]:
        folder = self.root / name
        found = [int(m.group(1)) for p in folder.glob("v*.md") if (m := _VERSION_FILE.match(p.name))]
        return sorted(found)

    # -- loading --
    def load(self, name: str, version: int | None = None) -> PromptSpec:
        """Load a prompt; without `version` the active one from registry.yaml is used."""
        resolved = version if version is not None else self.active_version(name)
        key = (name, resolved)
        if key not in self._cache:
            self._cache[key] = self._load_file(name, resolved)
        return self._cache[key]

    def _load_file(self, name: str, version: int) -> PromptSpec:
        path = self.root / name / f"v{version}.md"
        if not path.is_file():
            available = self.versions(name)
            raise PromptNotFoundError(
                f"Prompt '{name}' has no version {version} ({path}). Available: {available or 'none'}"
            )
        raw = path.read_text(encoding="utf-8")
        front, body = self._split_frontmatter(raw, path)
        if front.name != name:
            raise PromptFormatError(f"{path}: frontmatter name '{front.name}' does not match folder '{name}'")
        if front.version != version:
            raise PromptFormatError(f"{path}: frontmatter version {front.version} does not match file name v{version}")

        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        released = (self._registry().get(name) or {}).get("released", {})
        expected = released.get(version)
        if expected is not None and expected != digest:
            raise PromptFormatError(
                f"{path} was modified after release. Released versions are immutable: "
                f"restore it, or create v{version + 1} and make that active."
            )

        system_tpl, user_tpl = self._split_sections(body, path)
        self._validate_templates(front, system_tpl, user_tpl, path)
        return PromptSpec(
            name=front.name,
            version=front.version,
            model=front.model,
            temperature=front.temperature,
            max_tokens=front.max_tokens,
            variables=tuple(front.input_variables),
            output_schema=self._resolve_schema(front.output_schema, path),
            system_template=system_tpl,
            user_template=user_tpl,
            sha256=digest,
            path=path,
        )

    @staticmethod
    def _split_frontmatter(raw: str, path: Path) -> tuple[Frontmatter, str]:
        match = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n(.*)\Z", raw, re.DOTALL)
        if not match:
            raise PromptFormatError(f"{path}: file must start with a '---' delimited YAML frontmatter block")
        try:
            data = yaml.safe_load(match.group(1))
        except yaml.YAMLError as exc:
            raise PromptFormatError(f"{path}: invalid YAML in frontmatter: {exc}") from exc
        if not isinstance(data, dict):
            raise PromptFormatError(f"{path}: frontmatter must be a YAML mapping")
        try:
            return Frontmatter.model_validate(data), match.group(2)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'frontmatter'}: {e['msg']}" for e in exc.errors()
            )
            raise PromptFormatError(f"{path}: invalid frontmatter: {problems}") from exc

    @staticmethod
    def _split_sections(body: str, path: Path) -> tuple[str, str]:
        marks = list(_SECTION.finditer(body))
        if [m.group(1) for m in marks] != ["system", "user"]:
            raise PromptFormatError(f"{path}: body needs exactly '=== system ===' then '=== user ===' lines")
        if body[: marks[0].start()].strip():
            raise PromptFormatError(f"{path}: text before the '=== system ===' line")
        system = body[marks[0].end() : marks[1].start()].strip()
        user = body[marks[1].end() :].strip()
        if not system or not user:
            raise PromptFormatError(f"{path}: system and user sections must not be empty")
        return system, user

    @staticmethod
    def _resolve_schema(reference: str, path: Path) -> type[BaseModel]:
        module_name, sep, class_name = reference.partition(":")
        if not sep or not module_name or not class_name:
            raise PromptFormatError(f"{path}: output_schema must look like 'package.module:ClassName'")
        try:
            schema = getattr(importlib.import_module(module_name), class_name)
        except (ImportError, AttributeError) as exc:
            raise PromptFormatError(f"{path}: cannot resolve output_schema '{reference}': {exc}") from exc
        if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
            raise PromptFormatError(f"{path}: output_schema '{reference}' is not a pydantic model")
        return schema

    def _validate_templates(self, front: Frontmatter, system_tpl: str, user_tpl: str, path: Path) -> None:
        used: dict[str, set[str]] = {}
        for label, source in (("system", system_tpl), ("user", user_tpl)):
            try:
                used[label] = set(meta.find_undeclared_variables(self._env.parse(source)))
            except TemplateSyntaxError as exc:
                raise PromptFormatError(
                    f"{path}: template error in {label} section, line {exc.lineno}: {exc.message}"
                ) from exc
        declared = {v.name for v in front.input_variables}
        all_used = used["system"] | used["user"]
        if all_used - declared:
            raise PromptFormatError(f"{path}: template uses undeclared variables: {sorted(all_used - declared)}")
        if declared - all_used:
            raise PromptFormatError(f"{path}: declared but unused variables: {sorted(declared - all_used)}")
        leaked = used["system"] & {v.name for v in front.input_variables if v.untrusted}
        if leaked:
            raise PromptFormatError(
                f"{path}: untrusted variables must only appear in the user section, found in system: {sorted(leaked)}"
            )

    # -- rendering --
    def render(self, name: str, variables: dict[str, Any], version: int | None = None) -> RenderedPrompt:
        spec = self.load(name, version)
        declared = {v.name for v in spec.variables}
        missing = sorted(declared - variables.keys())
        extra = sorted(variables.keys() - declared)
        if missing or extra:
            parts = []
            if missing:
                parts.append(f"missing variables {missing}")
            if extra:
                parts.append(f"unexpected variables {extra}")
            raise PromptVariableError(f"{spec.ref}: " + "; ".join(parts))
        for key, value in variables.items():
            if value is None:
                raise PromptVariableError(f"{spec.ref}: variable '{key}' is None; pass an empty value instead")
            try:
                json.dumps(value)
            except TypeError as exc:
                raise PromptVariableError(f"{spec.ref}: variable '{key}' is not JSON-serialisable: {exc}") from exc

        untrusted = frozenset(v.name for v in spec.variables if v.untrusted)
        context = {k: (wrap_untrusted(k, v) if k in untrusted else v) for k, v in variables.items()}
        system = self._env.from_string(spec.system_template).render(context).strip()
        user = self._env.from_string(spec.user_template).render(context).strip()
        return RenderedPrompt(
            name=spec.name,
            version=spec.version,
            model=spec.model,
            temperature=spec.temperature,
            max_tokens=spec.max_tokens,
            messages=(Message("system", system), Message("user", user)),
            output_schema=spec.output_schema,
            variables=dict(variables),
            untrusted=untrusted,
            input_hash=input_hash(spec.ref, variables),
        )

    # -- releasing --
    def freeze(self) -> list[str]:
        """Record the hash of every prompt version that is not yet released; returns what was added."""
        path = self.root / REGISTRY_FILE
        prompts = self._registry()
        added: list[str] = []
        for name, entry in prompts.items():
            released = entry.setdefault("released", {})
            for version in self.versions(name):
                if version not in released:
                    released[version] = self.load(name, version).sha256
                    added.append(f"{name}@{version}")
        if added:
            path.write_text(yaml.safe_dump({"prompts": prompts}, sort_keys=True), encoding="utf-8")
        return added


@lru_cache
def default_registry() -> PromptRegistry:
    return PromptRegistry()


def render(name: str, variables: dict[str, Any], version: int | None = None) -> RenderedPrompt:
    """Render the active (or given) version of a prompt from ai/prompts."""
    return default_registry().render(name, variables, version)


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["freeze"]:
        done = default_registry().freeze()
        print("released: " + ", ".join(done) if done else "nothing to release")
    else:
        print("usage: python -m ai.llm.registry freeze")
        raise SystemExit(2)
