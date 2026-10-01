"""Schemas of the feedback module for test sessions.

Every string field is bounded, and every input model forbids unknown keys: the note
endpoint is reachable by any joined browser, so the schema is the size limit of what
a client can store.
"""

import re
from datetime import datetime
from typing import Literal
from urllib.parse import parse_qsl, urlencode
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

QaRole = Literal["operator", "org_admin", "creator", "coordinator", "participant", "guest", "signed_out"]

# A route or an API path can carry a secret, e.g. /api/invitations/<token> or
# /reset-password?token=. The bar redacts before sending; the server redacts again.
_SECRET_PARAM = re.compile(r"^(token|code|key|secret|password|pass|otp|totp|qa-join|invite|reset)$", re.IGNORECASE)
_TOKEN_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{20,}$")
REDACTED = ":redacted"


def _secret_segment(segment: str) -> bool:
    """A random token: long, with upper case, lower case and a digit. Slugs and UUIDs are lower case."""
    return bool(
        _TOKEN_SEGMENT.match(segment)
        and re.search(r"[A-Z]", segment)
        and re.search(r"[a-z]", segment)
        and re.search(r"[0-9]", segment)
    )


_CODE = re.compile(r"^[A-Z0-9]{4,10}$")
# Join and share codes inside a longer text, e.g. a copy button's name.
_CODE_WORD = re.compile(r"\b[A-Z0-9]{5,10}\b")
_WORD = re.compile(r"[A-Za-z0-9_-]{20,}")
_URL_IN_TEXT = re.compile(r"(?:https?://[^\s'\"<>]+|/api/[^\s'\"<>]+)")


def redact_text(value: str | None) -> str | None:
    """Click text and error messages: a join or share code goes, so do token-like words and
    secrets inside any address in the text."""
    if not value:
        return value
    if _CODE.match(value.strip()):
        return REDACTED
    value = _URL_IN_TEXT.sub(lambda m: redact_url(m.group(0)) or "", value)
    value = _WORD.sub(lambda m: REDACTED if _secret_segment(m.group(0)) else m.group(0), value)
    return _CODE_WORD.sub(REDACTED, value)


def redact_url(value: str | None) -> str | None:
    """Drop the fragment, blank secret query values and token-like path segments."""
    if not value:
        return value
    base = value.split("#", 1)[0]
    path, _, query = base.partition("?")
    path = "/".join(REDACTED if _secret_segment(seg) else seg for seg in path.split("/"))
    if not query:
        return path
    pairs = [(k, REDACTED if _SECRET_PARAM.match(k) else v) for k, v in parse_qsl(query, keep_blank_values=True)]
    return f"{path}?{urlencode(pairs, safe=':/')}"


class QaContext(BaseModel):
    """What the page saw when the event was made. Free text, never a reference."""

    model_config = ConfigDict(extra="forbid")

    role: str | None = Field(default=None, max_length=32)
    org: str | None = Field(default=None, max_length=100)
    lang: str | None = Field(default=None, max_length=16)
    viewport: str | None = Field(default=None, max_length=20)
    user_agent: str | None = Field(default=None, max_length=200)


class QaTrailEntry(BaseModel):
    """One action the bar saw since the step began. Never a typed value."""

    model_config = ConfigDict(extra="forbid")

    t: Literal["click", "route", "error", "http"]
    sel: str | None = Field(default=None, max_length=200)
    txt: str | None = Field(default=None, max_length=80)
    to: str | None = Field(default=None, max_length=200)
    msg: str | None = Field(default=None, max_length=200)
    method: str | None = Field(default=None, max_length=10)
    path: str | None = Field(default=None, max_length=200)
    status: int | None = Field(default=None, ge=0, le=999)
    at: str | None = Field(default=None, max_length=40)

    @field_validator("to", "path")
    @classmethod
    def no_secrets(cls, v: str | None) -> str | None:
        return redact_url(v)

    @field_validator("txt", "msg")
    @classmethod
    def no_secret_text(cls, v: str | None) -> str | None:
        return redact_text(v)


class QaPlanStep(BaseModel):
    """One step of the session plan with its plain-language card."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=32)
    block: str = Field(min_length=1, max_length=8)
    block_title: str | None = Field(default=None, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    do: str = Field(min_length=1, max_length=4000)
    expect: str = Field(min_length=1, max_length=4000)
    # Null means "no precondition", which is a real answer for a sign-in step.
    needs: str | None = Field(default=None, max_length=2000)
    role: QaRole | None = None
    device: Literal["desktop", "phone"] | None = None

    @field_validator("id", "block", "title", "do", "expect")
    @classmethod
    def not_blank(cls, v: str) -> str:
        """A card the tester cannot follow is no card."""
        if not v.strip():
            raise ValueError("must not be blank")
        return v


class QaSessionPut(BaseModel):
    """The kit uploads a session: the plan, and optionally where it starts."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, max_length=100)
    steps: list[QaPlanStep] = Field(min_length=1, max_length=400)
    start: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def ids_unique_and_start_known(self) -> "QaSessionPut":
        ids = [s.id for s in self.steps]
        if len(set(ids)) != len(ids):
            raise ValueError("step ids must be unique")
        if self.start is not None and self.start not in ids:
            raise ValueError("start must name a step of the plan")
        return self


class QaSessionCreated(BaseModel):
    join_key: str
    session_id: str
    current: str
    count: int


class QaBlock(BaseModel):
    id: str
    title: str | None
    first: str
    count: int


class QaStateOut(BaseModel):
    """The state for a joined browser or the kit. A browser without a key gets ``{"joined": false}``."""

    joined: bool = True
    session_id: str | None = None
    current: str | None = None
    index: int | None = None
    count: int = 0
    card: QaPlanStep | None = None
    blocks: list[QaBlock] = Field(default_factory=list)
    hidden: bool = False
    message: str | None = None
    session: str | None = None
    updated_at: datetime | None = None


class QaStatePatch(BaseModel):
    """Only the fields the kit sends change. An explicit null clears the message."""

    model_config = ConfigDict(extra="forbid")

    current: str | None = Field(default=None, max_length=32)
    hidden: bool | None = None
    message: str | None = Field(default=None, max_length=2000)


class QaNoteCreate(BaseModel):
    """An event from the feedback bar."""

    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=1, max_length=64)
    # The session the event was made in. A late event of an ended session is refused.
    session_id: str = Field(min_length=1, max_length=36)
    kind: Literal["ok", "skip", "back", "goto", "note"]
    step: str | None = Field(default=None, max_length=32)
    target: str | None = Field(default=None, max_length=32)
    text: str | None = Field(default=None, max_length=4000)
    route: str | None = Field(default=None, max_length=500)
    selector: str | None = Field(default=None, max_length=500)
    element_text: str | None = Field(default=None, max_length=300)
    answers_message: str | None = Field(default=None, max_length=2000)
    context: QaContext = Field(default_factory=QaContext)
    trail: list[QaTrailEntry] = Field(default_factory=list, max_length=30)

    @field_validator("route")
    @classmethod
    def route_without_secrets(cls, v: str | None) -> str | None:
        return redact_url(v)

    @model_validator(mode="after")
    def kind_needs_its_fields(self) -> "QaNoteCreate":
        if self.kind == "note" and not (self.text or "").strip():
            raise ValueError("a note needs a text")
        if self.kind == "goto" and not self.target:
            raise ValueError("a goto needs a target")
        return self


class QaNoteCreated(BaseModel):
    id: UUID
    seq: int
    created_at: datetime
    duplicate: bool
    moved: bool
    state: QaStateOut


class QaNoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    seq: int
    created_at: datetime
    client_id: str
    session_id: str | None
    kind: str
    step: str | None
    step_title: str | None
    target: str | None
    moved: bool
    to_step: str | None
    text: str | None
    route: str | None
    selector: str | None
    element_text: str | None
    answers_message: str | None
    context: dict[str, str | None]
    trail: list[dict[str, str | int | None]]


class QaNoteList(BaseModel):
    items: list[QaNoteOut]
    last_seq: int


class QaDeleted(BaseModel):
    deleted: int
