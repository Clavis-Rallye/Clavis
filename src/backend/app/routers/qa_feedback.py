"""
The feedback module for test sessions, mounted at ``/api/qa``.

- ``PUT /session``         kit: upload the plan, start it, get a new join key
- ``DELETE /session``      kit: clear the plan, the key and the message
- ``GET /state``           joined or kit: the current card, the blocks, the message
- ``PATCH /state``         kit: set the current step, hide every bar, set the message
- ``POST /notes``          joined, rate limited: an event; ok, skip, back and goto move the step
- ``GET /notes``           kit: events after a cursor, ascending
- ``GET /notes/export``    kit: every event as JSON or CSV
- ``DELETE /notes``        kit: delete every event

"Kit" is the header ``X-QA-Token`` equal to ``QA_FEEDBACK_TOKEN``, or an operator
session. "Joined" is the header ``X-QA-Key`` with the key of the current
session: the plan may hold the stand's logins, so a browser without the key
learns nothing. The whole router answers 404 while ``QA_FEEDBACK_ENABLED`` is off.
The check is a router dependency, so it runs before any credential check.

The bar's requests never carry a session cookie, so nothing here renews an
idle staff session.
"""

import csv
import hashlib
import hmac
import io
import json
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_instance_admin
from app.auth.tokens import INSTANCE_ADMIN_COOKIE_NAME
from app.config import get_settings
from app.database import get_db
from app.middleware.rate_limit import limiter
from app.models.qa_feedback import QaNote, QaState
from app.schemas.qa_feedback import (
    QaBlock,
    QaDeleted,
    QaNoteCreate,
    QaNoteCreated,
    QaNoteList,
    QaNoteOut,
    QaPlanStep,
    QaSessionCreated,
    QaSessionPut,
    QaStateOut,
    QaStatePatch,
)

logger = structlog.get_logger(__name__)


async def require_qa_enabled() -> None:
    """404 while the module is switched off."""
    if not get_settings().qa_feedback.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


router = APIRouter(dependencies=[Depends(require_qa_enabled)])


def _token_ok(x_qa_token: str | None) -> bool:
    configured = get_settings().qa_feedback.token
    return bool(configured and x_qa_token is not None and hmac.compare_digest(x_qa_token.encode(), configured.encode()))


async def require_qa_reader(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_qa_token: str | None = Header(default=None, alias="X-QA-Token"),
) -> str:
    """The kit's token or an operator session. Returns who it was, for the log.

    A token header that is present but wrong is a 401 of its own and never falls
    through to the operator check: a misconfigured kit must see its mistake.
    """
    if x_qa_token is not None:
        if _token_ok(x_qa_token):
            return "token"
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid QA token")
    admin = await require_instance_admin(
        db=db,
        instance_token=request.cookies.get(INSTANCE_ADMIN_COOKIE_NAME),
        access_token=request.cookies.get("access_token"),
        authorization=request.headers.get("authorization"),
    )
    return f"operator:{admin.id}"


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _key_ok(row: QaState | None, key: str | None) -> bool:
    """Whether the browser holds the key of the current session, in constant time."""
    if row is None or not row.key_hash or not key:
        return False
    return hmac.compare_digest(_hash_key(key), row.key_hash)


def _plan(row: QaState) -> list[QaPlanStep]:
    return [QaPlanStep.model_validate(step) for step in (row.plan or [])]


def _blocks(plan: list[QaPlanStep]) -> list[QaBlock]:
    blocks: dict[str, QaBlock] = {}
    for step in plan:
        key = step.block or ""
        if key in blocks:
            blocks[key].count += 1
        else:
            blocks[key] = QaBlock(id=key, title=step.block_title, first=step.id, count=1)
    return list(blocks.values())


def _state_out(row: QaState) -> QaStateOut:
    plan = _plan(row)
    ids = [s.id for s in plan]
    index = ids.index(row.step) if row.step in ids else None
    return QaStateOut(
        session_id=row.session_id,
        current=row.step,
        index=index,
        count=len(plan),
        card=plan[index] if index is not None else None,
        blocks=_blocks(plan),
        hidden=row.hidden,
        message=row.message,
        session=row.session,
        updated_at=row.updated_at,
    )


async def _locked_state(db: AsyncSession) -> QaState:
    """The state row, created if missing, locked for the rest of the transaction."""
    await db.execute(pg_insert(QaState).values(id=1).on_conflict_do_nothing(index_elements=["id"]))
    return (await db.execute(select(QaState).where(QaState.id == 1).with_for_update())).scalar_one()


def _move(row: QaState, note: QaNoteCreate) -> str | None:
    """The step this event makes current, or None.

    Every move, goto included, applies only when the event names the current step,
    so a double tap, a late retry from the outbox or a stale block jump never
    overwrites a newer step. Nothing moves past the plan's ends.
    """
    ids = [s.id for s in _plan(row)]
    if row.step not in ids or note.step != row.step:
        return None
    if note.kind == "goto":
        return note.target if note.target in ids and note.target != row.step else None
    if note.kind not in ("ok", "skip", "back"):
        return None
    i = ids.index(row.step)
    j = i - 1 if note.kind == "back" else i + 1
    return ids[j] if 0 <= j < len(ids) else None


# ------------------------------------------------------------------ session ----


@router.put("/session", response_model=QaSessionCreated)
async def put_session(
    body: QaSessionPut,
    db: AsyncSession = Depends(get_db),
    who: str = Depends(require_qa_reader),
) -> QaSessionCreated:
    """Upload the plan of a test session and start it.

    Open to the test kit, which is a caller with the header `X-QA-Token` or an
    instance operator session. The response holds a new join key. Browsers that
    joined with an earlier key lose access. The route resets the message, shows the
    bars again and starts at `start` or at the first step. It answers 404 while the
    feedback module is off.
    """
    key = secrets.token_urlsafe(24)
    row = await _locked_state(db)
    row.plan = [step.model_dump() for step in body.steps]
    row.step = body.start or body.steps[0].id
    row.key_hash = _hash_key(key)
    row.session_id = str(uuid.uuid4())
    row.session = body.label
    row.message = None
    row.message_at = None
    row.hidden = False
    row.updated_at = datetime.now(UTC)
    await db.commit()
    logger.info("qa_session_started", by=who, steps=len(body.steps), start=row.step)
    return QaSessionCreated(join_key=key, session_id=row.session_id, current=row.step, count=len(body.steps))


@router.delete("/session")
async def delete_session(
    db: AsyncSession = Depends(get_db),
    who: str = Depends(require_qa_reader),
) -> dict[str, bool]:
    """End the test session: clear the plan, the join key and the message.

    Open to the test kit. The stored events stay. It answers 404 while the
    feedback module is off.
    """
    row = await _locked_state(db)
    row.plan, row.step, row.key_hash, row.session_id = [], None, None, None
    row.message, row.message_at = None, None
    row.updated_at = datetime.now(UTC)
    await db.commit()
    logger.info("qa_session_cleared", by=who)
    return {"cleared": True}


# -------------------------------------------------------------------- state ----


@router.get("/state", response_model=None)
@limiter.limit("300/minute")
async def get_state(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_qa_key: str | None = Header(default=None, alias="X-QA-Key"),
    x_qa_token: str | None = Header(default=None, alias="X-QA-Token"),
) -> dict[str, Any]:
    """Return the current step card, the blocks of the plan and the message of the session.

    Up to 300 requests per minute. A browser that joined with the header `X-QA-Key`
    reads the state, and so does the test kit. Any other caller gets only
    `joined: false`. A caller that sends kit credentials gets the answer of the kit
    or its error. A wrong `X-QA-Token` answers 401, so that a misconfigured kit
    never reads "no session". It answers 404 while the feedback module is off.
    """
    row = await db.get(QaState, 1)
    if row is not None and _key_ok(row, x_qa_key):
        return _state_out(row).model_dump(mode="json")
    presents_credentials = (
        x_qa_token is not None
        or request.headers.get("authorization") is not None
        or request.cookies.get(INSTANCE_ADMIN_COOKIE_NAME) is not None
        or request.cookies.get("access_token") is not None
    )
    if presents_credentials:
        await require_qa_reader(request=request, db=db, x_qa_token=x_qa_token)
        return (_state_out(row) if row is not None else QaStateOut()).model_dump(mode="json")
    return {"joined": False}


@router.patch("/state", response_model=QaStateOut)
async def patch_state(
    body: QaStatePatch,
    db: AsyncSession = Depends(get_db),
    who: str = Depends(require_qa_reader),
) -> QaStateOut:
    """Set the current step, hide or show every bar, and set or clear the message.

    Open to the test kit. A `current` that names no step of the plan answers 422.
    An empty message clears the message. It answers 404 while the feedback module
    is off.
    """
    row = await _locked_state(db)
    fields = body.model_fields_set
    if "current" in fields:
        if body.current not in {s.id for s in _plan(row)}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="current must name a step of the plan"
            )
        row.step = body.current
    if "hidden" in fields and body.hidden is not None:
        row.hidden = body.hidden
    if "message" in fields:
        row.message = (body.message or "").strip() or None
        row.message_at = datetime.now(UTC) if row.message else None
    row.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    logger.info("qa_state_updated", by=who, step=row.step, hidden=row.hidden, message=row.message is not None)
    return _state_out(row)


# -------------------------------------------------------------------- notes ----


@router.post(
    "/notes",
    response_model=QaNoteCreated,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"description": "The client_id is known, the stored event is returned"}},
)
@limiter.limit(lambda: get_settings().qa_feedback.note_rate_limit)
async def create_note(
    request: Request,  # noqa: ARG001 — required by slowapi limiter
    response: Response,
    body: QaNoteCreate,
    db: AsyncSession = Depends(get_db),
    x_qa_key: str | None = Header(default=None, alias="X-QA-Key"),
) -> QaNoteCreated:
    """Store one event of a tester and move the current step in the same transaction.

    Open to a browser that joined with the header `X-QA-Key`, at the rate limit
    that the configuration sets. The events `ok`, `skip`, `back` and `goto` move the
    step, and only when the event names the current step. So a double tap or a late
    retry never overwrites a newer step. Two events that arrive together apply
    one after the other.

    A new event answers 201. An event whose `client_id` is stored already answers 200
    with the stored event and moves nothing. A caller without the current key
    answers 401. An event of an ended session answers 409. A note that answers
    the current message clears that message. It answers 404 while the feedback
    module is off.
    """
    row = await db.get(QaState, 1, with_for_update=True)
    if row is None or not _key_ok(row, x_qa_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not joined to the current session")
    if body.session_id != row.session_id:
        # An event queued in an ended session must not land in the next one.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The session has ended")

    existing = (await db.execute(select(QaNote).where(QaNote.client_id == body.client_id))).scalar_one_or_none()
    if existing is None:
        to_step = _move(row, body)
        card = next((c for c in _plan(row) if c.id == body.step), None)
        note = QaNote(
            client_id=body.client_id,
            session_id=row.session_id,
            kind=body.kind,
            step=body.step,
            step_title=card.title if card else None,
            target=body.target,
            text=body.text,
            route=body.route,
            selector=body.selector,
            element_text=body.element_text,
            answers_message=body.answers_message,
            context=body.context.model_dump(exclude_none=True),
            trail=[entry.model_dump(exclude_none=True) for entry in body.trail],
            moved=to_step is not None,
            to_step=to_step,
        )
        db.add(note)
        if to_step is not None:
            row.step = to_step
            row.updated_at = datetime.now(UTC)
        if body.kind == "note" and body.answers_message and body.answers_message == row.message:
            row.message, row.message_at = None, None
            row.updated_at = datetime.now(UTC)
        try:
            await db.commit()
        except IntegrityError:
            # A retry of the same event won the race between our check and insert.
            await db.rollback()
            existing = (await db.execute(select(QaNote).where(QaNote.client_id == body.client_id))).scalar_one()
        else:
            await db.refresh(note)
            await db.refresh(row)
            return QaNoteCreated(
                id=note.id,
                seq=note.seq,
                created_at=note.created_at,
                duplicate=False,
                moved=note.moved,
                state=_state_out(row),
            )
    current = await db.get(QaState, 1, populate_existing=True)
    response.status_code = status.HTTP_200_OK
    return QaNoteCreated(
        id=existing.id,
        seq=existing.seq,
        created_at=existing.created_at,
        duplicate=True,
        moved=False,
        state=_state_out(current) if current is not None else QaStateOut(),
    )


@router.get("/notes", response_model=QaNoteList)
async def list_notes(
    after: int = Query(0, ge=0, description="Return notes with a higher seq"),
    limit: int = Query(200, ge=1, le=500),
    session: str | None = Query(None, max_length=36, description="Only events of this session"),
    db: AsyncSession = Depends(get_db),
    _who: str = Depends(require_qa_reader),
) -> QaNoteList:
    """List the events with a `seq` above the cursor `after`, in ascending order.

    Open to the test kit. The kit polls with the `last_seq` of the previous answer.
    The optional `session` limits the list to one session. It answers 404 while the
    feedback module is off.
    """
    stmt = select(QaNote).where(QaNote.seq > after)
    if session:
        stmt = stmt.where(QaNote.session_id == session)
    rows = (await db.execute(stmt.order_by(QaNote.seq).limit(limit))).scalars().all()
    items = [QaNoteOut.model_validate(r) for r in rows]
    return QaNoteList(items=items, last_seq=items[-1].seq if items else after)


_CSV_COLUMNS = (
    "seq",
    "created_at",
    "session_id",
    "kind",
    "step",
    "step_title",
    "target",
    "moved",
    "to_step",
    "text",
    "route",
    "selector",
    "element_text",
    "answers_message",
)
_CSV_CONTEXT = ("role", "org", "lang", "viewport", "user_agent")


def _csv_cell(value: object) -> str:
    """A cell a spreadsheet will not run as a formula (OWASP CSV injection)."""
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


@router.get("/notes/export")
async def export_notes(
    format: Literal["json", "csv"] = Query("json"),  # noqa: A002 — the public query name
    session: str | None = Query(None, max_length=36, description="Only events of this session"),
    db: AsyncSession = Depends(get_db),
    _who: str = Depends(require_qa_reader),
) -> Response:
    """Download every event, or every event of one session, as a JSON or CSV file.

    Open to the test kit. A CSV cell that starts with a character a spreadsheet
    reads as a formula gets a leading apostrophe. It answers 404 while the
    feedback module is off.
    """
    stmt = select(QaNote)
    if session:
        stmt = stmt.where(QaNote.session_id == session)
    rows = (await db.execute(stmt.order_by(QaNote.seq))).scalars().all()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    if format == "json":
        payload = [QaNoteOut.model_validate(r).model_dump(mode="json") for r in rows]
        return JSONResponse(
            content=payload,
            headers={"Content-Disposition": f'attachment; filename="qa-notes-{stamp}.json"'},
        )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([*_CSV_COLUMNS, *_CSV_CONTEXT, "trail", "client_id", "id"])
    for r in rows:
        writer.writerow(
            [
                *(_csv_cell(getattr(r, c)) for c in _CSV_COLUMNS),
                *(_csv_cell((r.context or {}).get(k)) for k in _CSV_CONTEXT),
                _csv_cell(json.dumps(r.trail or [], ensure_ascii=False)),
                _csv_cell(r.client_id),
                str(r.id),
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="qa-notes-{stamp}.csv"'},
    )


@router.delete("/notes", response_model=QaDeleted)
async def delete_notes(
    db: AsyncSession = Depends(get_db),
    who: str = Depends(require_qa_reader),
) -> QaDeleted:
    """Delete every stored event and return how many were deleted.

    Open to the test kit. The deletion cannot be undone, so export the events
    first. It answers 404 while the feedback module is off.
    """
    result = await db.execute(delete(QaNote))
    await db.commit()
    deleted = result.rowcount or 0  # type: ignore[attr-defined]
    logger.info("qa_notes_deleted", by=who, deleted=deleted)
    return QaDeleted(deleted=deleted)
