"""The feedback module for test sessions.

Covers:

- the switch
- the session plan and the join key
- the server-side step move
- the assistant's message
- the trail
- the kit's endpoints and their credentials
- the life of the data: retention, the kit's deletes, and the deletion of an organization
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.organization import Organization
from app.models.qa_feedback import QaNote, QaState
from app.repositories.organization import OrganizationRepository
from app.services.org_deletion import DELETION_GRACE_DAYS, sweep_due_organizations
from app.services.qa_feedback_retention import sweep_qa_notes
from app.storage import ensure_bucket
from tests.factories import create_test_organization

TOKEN = "qa-test-token-0123456789abcdef0123456789"
KIT = {"X-QA-Token": TOKEN}

PLAN = [
    {"id": "A-01", "block": "A", "block_title": "Operator console", "title": "Anmelden", "role": "operator",
     "needs": "Abgemeldet", "do": "1. Oeffne https://stand.example/instance/login\n2. Melde dich an", "expect": "Die Konsole"},
    {"id": "A-02", "block": "A", "block_title": "Operator console", "title": "Organisationen", "role": "operator",
     "do": "Lies die Liste", "expect": "Zwei Organisationen"},
    {"id": "E-01", "block": "E", "block_title": "Participant as a guest", "title": "Beitreten", "role": "guest",
     "device": "phone", "do": "Oeffne den Link", "expect": "Die Willkommensseite"},
    {"id": "E-02", "block": "E", "block_title": "Participant as a guest", "title": "Team waehlen", "role": "guest",
     "do": "Waehle ein Team", "expect": "Die Stationen"},
]  # fmt: skip


# The session id of the last _start in this test. Events default to it.
SESSION: dict[str, str | None] = {"id": None}


@pytest.fixture
def qa_on() -> Iterator[None]:
    """Switch the module on with a token. get_settings() is cached, so flip and restore."""
    qa = get_settings().qa_feedback
    previous = (qa.enabled, qa.token, qa.note_rate_limit)
    qa.enabled, qa.token = True, TOKEN
    yield
    qa.enabled, qa.token, qa.note_rate_limit = previous


async def _start(client: AsyncClient, start: str | None = None) -> dict[str, str]:
    """Upload PLAN and answer the joined browser's headers."""
    body: dict[str, object] = {"label": "test", "steps": PLAN}
    if start:
        body["start"] = start
    resp = await client.put("/api/qa/session", headers=KIT, json=body)
    assert resp.status_code == 200, resp.text
    SESSION["id"] = resp.json()["session_id"]
    return {"X-QA-Key": resp.json()["join_key"]}


def _event(kind: str, step: str | None = "A-01", client_id: str | None = None, **extra: object) -> dict[str, object]:
    body: dict[str, object] = {
        "client_id": client_id or f"{kind}-{step}-{len(extra)}",
        "session_id": SESSION["id"],
        "kind": kind,
        "step": step,
    }
    body.update(extra)
    return body


def _note(client_id: str = "c-1", **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "client_id": client_id,
        "session_id": SESSION["id"] or "none",
        "kind": "note",
        "step": "A-01",
        "text": "Der Button heisst anders als im Handbuch",
        "route": "/instance?tab=orgs",
        "selector": "button#archive-munich",
        "element_text": "Archivieren",
        "context": {"role": "operator", "org": "muenchen", "lang": "de", "viewport": "1280x800", "user_agent": "UA"},
    }
    body.update(overrides)
    return body


async def _operator_cookie(client: AsyncClient, db_session: AsyncSession) -> None:
    from app.auth.instance import create_instance_admin
    from app.auth.tokens import INSTANCE_ADMIN_COOKIE_NAME, create_instance_admin_token

    admin = await create_instance_admin(db_session, "operator@qa.test", "OperatorPass123!")
    client.cookies.set(INSTANCE_ADMIN_COOKIE_NAME, create_instance_admin_token(admin.id))


# --------------------------------------------------------------- the switch ----


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", "/api/qa/session"),
        ("DELETE", "/api/qa/session"),
        ("GET", "/api/qa/state"),
        ("PATCH", "/api/qa/state"),
        ("POST", "/api/qa/notes"),
        ("GET", "/api/qa/notes"),
        ("GET", "/api/qa/notes/export"),
        ("DELETE", "/api/qa/notes"),
    ],
)
async def test_every_endpoint_is_404_while_the_module_is_off(client: AsyncClient, method: str, path: str):
    """Off reveals nothing, also not to a caller with the right token."""
    qa = get_settings().qa_feedback
    assert qa.enabled is False  # the default
    previous = qa.token
    qa.token = TOKEN
    try:
        body = {"POST": _note(), "PATCH": {"hidden": True}, "PUT": {"steps": PLAN}}.get(method)
        resp = await client.request(method, path, json=body, headers=KIT)
    finally:
        qa.token = previous
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_public_config_reports_the_switch(client: AsyncClient, qa_on: None):
    resp = await client.get("/api/public/config")
    assert resp.status_code == 200
    assert resp.json()["qa_feedback"] is True


def test_a_short_token_is_refused():
    from pydantic import ValidationError

    from app.config import QaFeedbackSettings

    with pytest.raises(ValidationError):
        QaFeedbackSettings(token="too-short")
    assert QaFeedbackSettings(token="").token == ""


# ------------------------------------------------------- session and key ----


@pytest.mark.asyncio
async def test_without_a_key_the_state_reveals_nothing(client: AsyncClient, qa_on: None):
    """No key, a wrong key, or no session at all: only joined false."""
    assert (await client.get("/api/qa/state")).json() == {"joined": False}
    await _start(client)
    assert (await client.get("/api/qa/state")).json() == {"joined": False}
    assert (await client.get("/api/qa/state", headers={"X-QA-Key": "x" * 32})).json() == {"joined": False}


@pytest.mark.asyncio
async def test_a_joined_browser_reads_the_card_and_the_blocks(client: AsyncClient, qa_on: None):
    join = await _start(client)
    state = (await client.get("/api/qa/state", headers=join)).json()
    assert state["joined"] is True
    assert state["current"] == "A-01"
    assert (state["index"], state["count"]) == (0, 4)
    assert state["card"]["needs"] == "Abgemeldet"
    assert state["card"]["role"] == "operator"
    assert state["card"]["do"].startswith("1. Oeffne https://")
    assert state["blocks"] == [
        {"id": "A", "title": "Operator console", "first": "A-01", "count": 2},
        {"id": "E", "title": "Participant as a guest", "first": "E-01", "count": 2},
    ]
    assert state["message"] is None
    assert state["hidden"] is False


@pytest.mark.asyncio
async def test_the_kit_reads_the_state_with_its_token(client: AsyncClient, qa_on: None):
    await _start(client, start="E-01")
    state = (await client.get("/api/qa/state", headers=KIT)).json()
    assert state["joined"] is True
    assert state["current"] == "E-01"


@pytest.mark.asyncio
async def test_a_new_upload_ends_the_old_key(client: AsyncClient, qa_on: None):
    old = await _start(client)
    new = await _start(client)
    assert (await client.get("/api/qa/state", headers=old)).json() == {"joined": False}
    assert (await client.post("/api/qa/notes", headers=old, json=_event("ok"))).status_code == 401
    assert (await client.post("/api/qa/notes", headers=new, json=_event("ok"))).status_code == 201


@pytest.mark.asyncio
async def test_the_key_is_stored_only_as_a_hash(client: AsyncClient, db_session: AsyncSession, qa_on: None):
    join = await _start(client)
    row = await db_session.get(QaState, 1)
    assert row is not None
    assert row.key_hash is not None
    assert join["X-QA-Key"] not in row.key_hash
    assert len(row.key_hash) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"steps": []},
        {"steps": [PLAN[0], PLAN[0]]},
        {"steps": PLAN, "start": "Z-99"},
        {"steps": [{**PLAN[0], "role": "superuser"}]},
        {"steps": [{**PLAN[0], "device": "tablet"}]},
        {"steps": [{**PLAN[0], "password": "x"}]},
        {"steps": [{**PLAN[0], "do": "x" * 4001}]},
        {"steps": [{**PLAN[0], "title": "   "}]},
        {"steps": [{k: v for k, v in PLAN[0].items() if k != "do"}]},
        {"steps": [{k: v for k, v in PLAN[0].items() if k != "expect"}]},
        {"steps": [{k: v for k, v in PLAN[0].items() if k != "block"}]},
    ],
)
async def test_an_invalid_plan_is_refused(client: AsyncClient, qa_on: None, body: dict[str, object]):
    assert (await client.put("/api/qa/session", headers=KIT, json=body)).status_code == 422


@pytest.mark.asyncio
async def test_clearing_the_session_ends_the_key_and_keeps_the_notes(
    client: AsyncClient, db_session: AsyncSession, qa_on: None
):
    join = await _start(client)
    await client.post("/api/qa/notes", headers=join, json=_note())
    resp = await client.delete("/api/qa/session", headers=KIT)
    assert resp.json() == {"cleared": True}
    assert (await client.get("/api/qa/state", headers=join)).json() == {"joined": False}
    assert await db_session.scalar(select(func.count()).select_from(QaNote)) == 1


# ------------------------------------------------------------------ the move ----


@pytest.mark.asyncio
async def test_ok_moves_and_the_answer_carries_the_next_card(client: AsyncClient, qa_on: None):
    """The answer carries the next card, so the tapping device needs no poll."""
    join = await _start(client)
    resp = await client.post("/api/qa/notes", headers=join, json=_event("ok", "A-01"))
    assert resp.status_code == 201
    data = resp.json()
    assert data["moved"] is True
    assert data["state"]["current"] == "A-02"
    assert data["state"]["card"]["title"] == "Organisationen"


@pytest.mark.asyncio
async def test_a_double_tap_moves_once(client: AsyncClient, qa_on: None):
    """The second tap still names A-01, which is no longer current."""
    join = await _start(client)
    first = await client.post("/api/qa/notes", headers=join, json=_event("ok", "A-01", client_id="tap-1"))
    second = await client.post("/api/qa/notes", headers=join, json=_event("ok", "A-01", client_id="tap-2"))
    assert first.json()["moved"] is True
    assert second.status_code == 201
    assert second.json()["moved"] is False
    assert second.json()["state"]["current"] == "A-02"


@pytest.mark.asyncio
async def test_a_repeated_client_id_never_moves_again(client: AsyncClient, db_session: AsyncSession, qa_on: None):
    """The outbox may send an event twice."""
    join = await _start(client)
    first = await client.post("/api/qa/notes", headers=join, json=_event("ok", "A-01", client_id="retry-1"))
    await client.post("/api/qa/notes", headers=join, json=_event("ok", "A-02", client_id="next-1"))
    again = await client.post("/api/qa/notes", headers=join, json=_event("ok", "A-01", client_id="retry-1"))
    assert again.status_code == 200
    assert again.json()["duplicate"] is True
    assert again.json()["moved"] is False
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["state"]["current"] == "E-01"
    assert await db_session.scalar(select(func.count()).select_from(QaNote).where(QaNote.client_id == "retry-1")) == 1


@pytest.mark.asyncio
async def test_back_skip_and_goto(client: AsyncClient, qa_on: None):
    join = await _start(client)

    async def send(kind: str, step: str, **extra: object) -> dict[str, object]:
        resp = await client.post(
            "/api/qa/notes", headers=join, json=_event(kind, step, client_id=f"{kind}-{step}", **extra)
        )
        return resp.json()

    assert (await send("skip", "A-01", text="kein Telefon"))["state"]["current"] == "A-02"  # type: ignore[index]
    assert (await send("back", "A-02"))["state"]["current"] == "A-01"  # type: ignore[index]
    jumped = await send("goto", "A-01", target="E-01")
    assert jumped["moved"] is True
    assert jumped["state"]["current"] == "E-01"  # type: ignore[index]
    assert (await send("goto", "E-01", target="Z-99"))["moved"] is False


@pytest.mark.asyncio
async def test_nothing_moves_past_the_ends_of_the_plan(client: AsyncClient, qa_on: None):
    join = await _start(client, start="E-02")
    end = await client.post("/api/qa/notes", headers=join, json=_event("ok", "E-02", client_id="end"))
    assert end.json()["moved"] is False
    assert end.json()["state"]["current"] == "E-02"

    await client.patch("/api/qa/state", headers=KIT, json={"current": "A-01"})
    start = await client.post("/api/qa/notes", headers=join, json=_event("back", "A-01", client_id="start"))
    assert start.json()["moved"] is False
    assert start.json()["state"]["current"] == "A-01"


@pytest.mark.asyncio
async def test_a_note_never_moves(client: AsyncClient, qa_on: None):
    join = await _start(client)
    resp = await client.post("/api/qa/notes", headers=join, json=_note())
    assert resp.json()["moved"] is False
    assert resp.json()["state"]["current"] == "A-01"


@pytest.mark.asyncio
async def test_the_kit_sets_the_current_step(client: AsyncClient, qa_on: None):
    """The assistant can still lead."""
    join = await _start(client)
    resp = await client.patch("/api/qa/state", headers=KIT, json={"current": "E-02"})
    assert resp.status_code == 200
    assert (await client.get("/api/qa/state", headers=join)).json()["current"] == "E-02"
    assert (await client.patch("/api/qa/state", headers=KIT, json={"current": "Z-99"})).status_code == 422


@pytest.mark.asyncio
async def test_hidden_switches_every_bar_off(client: AsyncClient, qa_on: None):
    join = await _start(client)
    await client.patch("/api/qa/state", headers=KIT, json={"hidden": True})
    assert (await client.get("/api/qa/state", headers=join)).json()["hidden"] is True
    await client.patch("/api/qa/state", headers=KIT, json={"hidden": None})
    assert (await client.get("/api/qa/state", headers=join)).json()["hidden"] is True


# --------------------------------------------------------------- one voice ----


@pytest.mark.asyncio
async def test_the_message_shows_until_a_note_answers_it(client: AsyncClient, db_session: AsyncSession, qa_on: None):
    join = await _start(client)
    await client.patch("/api/qa/state", headers=KIT, json={"message": "Welcher Button war gemeint?"})
    assert (await client.get("/api/qa/state", headers=join)).json()["message"] == "Welcher Button war gemeint?"

    # An ok is no answer, the message stays.
    ok = await client.post("/api/qa/notes", headers=join, json=_event("ok", "A-01", client_id="ok-m"))
    assert ok.json()["state"]["message"] == "Welcher Button war gemeint?"

    answer = await client.post(
        "/api/qa/notes",
        headers=join,
        json=_note("answer-1", step="A-02", text="Der rote", answers_message="Welcher Button war gemeint?"),
    )
    assert answer.json()["state"]["message"] is None
    stored = (await db_session.execute(select(QaNote).where(QaNote.client_id == "answer-1"))).scalar_one()
    assert stored.answers_message == "Welcher Button war gemeint?"


@pytest.mark.asyncio
async def test_an_answer_to_an_old_message_leaves_the_new_one(client: AsyncClient, qa_on: None):
    join = await _start(client)
    await client.patch("/api/qa/state", headers=KIT, json={"message": "Neue Frage"})
    resp = await client.post("/api/qa/notes", headers=join, json=_note("late", answers_message="Alte Frage"))
    assert resp.json()["state"]["message"] == "Neue Frage"
    await client.patch("/api/qa/state", headers=KIT, json={"message": None})
    assert (await client.get("/api/qa/state", headers=join)).json()["message"] is None


# ------------------------------------------------------------------ events ----


@pytest.mark.asyncio
async def test_a_note_is_stored_with_its_trail(client: AsyncClient, db_session: AsyncSession, qa_on: None):
    join = await _start(client)
    trail = [
        {"t": "click", "sel": "button#save", "txt": "Speichern", "at": "2026-10-31T12:00:00Z"},
        {"t": "http", "method": "POST", "path": "/api/orgs/x/stations/", "status": 422, "at": "2026-10-31T12:00:01Z"},
        {"t": "route", "to": "/stations", "at": "2026-10-31T12:00:02Z"},
        {"t": "error", "msg": "TypeError: x is undefined", "at": "2026-10-31T12:00:03Z"},
    ]
    resp = await client.post("/api/qa/notes", headers=join, json=_note(trail=trail))
    assert resp.status_code == 201
    note = (await db_session.execute(select(QaNote).where(QaNote.client_id == "c-1"))).scalar_one()
    assert note.trail[1] == {"t": "http", "method": "POST", "path": "/api/orgs/x/stations/", "status": 422, "at": "2026-10-31T12:00:01Z"}  # fmt: skip
    assert note.selector == "button#archive-munich"
    assert note.context["org"] == "muenchen"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"text": "   "},
        {"text": None},
        {"kind": "rating"},
        {"kind": "goto", "target": None},
        {"context": {"email": "a@b.c"}},
        {"text": "x" * 4001},
        {"selector": "x" * 501},
        {"client_id": ""},
        {"user_id": "someone"},
        {"trail": [{"t": "click"}] * 31},
        {"trail": [{"t": "input", "value": "secret"}]},
        {"trail": [{"t": "click", "value": "secret"}]},
    ],
)
async def test_invalid_events_are_refused(client: AsyncClient, qa_on: None, overrides: dict[str, object]):
    """The schema is the size limit, and a trail holds no values."""
    join = await _start(client)
    resp = await client.post("/api/qa/notes", headers=join, json=_note(**overrides))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_events_need_the_key(client: AsyncClient, qa_on: None):
    await _start(client)
    assert (await client.post("/api/qa/notes", json=_note())).status_code == 401
    assert (await client.post("/api/qa/notes", headers={"X-QA-Key": "x" * 32}, json=_note())).status_code == 401
    # The kit's token is no key: the kit never writes events.
    assert (await client.post("/api/qa/notes", headers=KIT, json=_note())).status_code == 401


@pytest.mark.asyncio
async def test_events_are_rate_limited_per_address(client: AsyncClient, qa_on: None):
    join = await _start(client)
    get_settings().qa_feedback.note_rate_limit = "3/minute"
    codes = [(await client.post("/api/qa/notes", headers=join, json=_note(f"rl-{i}"))).status_code for i in range(4)]
    assert codes == [201, 201, 201, 429]


# ---------------------------------------------------------- reader access ----


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", "/api/qa/session"),
        ("DELETE", "/api/qa/session"),
        ("PATCH", "/api/qa/state"),
        ("GET", "/api/qa/notes"),
        ("GET", "/api/qa/notes/export"),
        ("DELETE", "/api/qa/notes"),
    ],
)
async def test_kit_endpoints_refuse_without_credentials(client: AsyncClient, qa_on: None, method: str, path: str):
    """No token and a wrong token are 401, also with a join key."""
    join = await _start(client)
    body = {"PATCH": {"hidden": True}, "PUT": {"steps": PLAN}}.get(method)
    none = await client.request(method, path, json=body)
    wrong = await client.request(method, path, json=body, headers={"X-QA-Token": "x" * 40})
    joined = await client.request(method, path, json=body, headers=join)
    assert (none.status_code, wrong.status_code, joined.status_code) == (401, 401, 401)


@pytest.mark.asyncio
async def test_an_organization_user_is_refused(client_org_a: AsyncClient, qa_on: None):
    resp = await client_org_a.get("/api/qa/notes")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_an_operator_session_is_accepted(client: AsyncClient, db_session: AsyncSession, qa_on: None):
    await _operator_cookie(client, db_session)
    assert (await client.get("/api/qa/notes")).status_code == 200
    assert (await client.put("/api/qa/session", json={"steps": PLAN})).status_code == 200


@pytest.mark.asyncio
async def test_an_empty_token_setting_accepts_no_token(client: AsyncClient, qa_on: None):
    get_settings().qa_feedback.token = ""
    assert (await client.get("/api/qa/notes", headers={"X-QA-Token": ""})).status_code == 401
    assert (await client.get("/api/qa/notes", headers={"X-QA-Token": TOKEN})).status_code == 401


# ------------------------------------------------------------- the reader ----


@pytest.mark.asyncio
async def test_events_come_after_the_cursor_in_order_with_their_move(client: AsyncClient, qa_on: None):
    join = await _start(client)
    await client.post("/api/qa/notes", headers=join, json=_event("ok", "A-01", client_id="e1"))
    await client.post("/api/qa/notes", headers=join, json=_note("e2", step="A-02", text="Notiz"))
    await client.post("/api/qa/notes", headers=join, json=_event("back", "A-02", client_id="e3"))

    everything = (await client.get("/api/qa/notes", headers=KIT)).json()
    items = everything["items"]
    assert [(n["client_id"], n["moved"], n["to_step"]) for n in items] == [
        ("e1", True, "A-02"),
        ("e2", False, None),
        ("e3", True, "A-01"),
    ]
    assert everything["last_seq"] == items[-1]["seq"]
    later = (await client.get(f"/api/qa/notes?after={items[0]['seq']}", headers=KIT)).json()
    assert [n["client_id"] for n in later["items"]] == ["e2", "e3"]


@pytest.mark.asyncio
async def test_export_csv_escapes_formulas_and_json_matches(client: AsyncClient, qa_on: None):
    join = await _start(client)
    await client.post(
        "/api/qa/notes", headers=join, json=_note("csv-1", text='=HYPERLINK("http://x")', element_text="-1 Punkt")
    )
    await client.post(
        "/api/qa/notes", headers=join, json=_note("csv-2", text="harmlos", trail=[{"t": "click", "sel": "a"}])
    )

    csv_resp = await client.get("/api/qa/notes/export?format=csv", headers=KIT)
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    body = csv_resp.text
    assert body.splitlines()[0].startswith("seq,created_at,session_id,kind,step,step_title,target,moved,to_step,text")
    assert "'=HYPERLINK" in body
    assert "'-1 Punkt" in body
    assert ",harmlos," in body

    exported = (await client.get("/api/qa/notes/export?format=json", headers=KIT)).json()
    assert [n["client_id"] for n in exported] == ["csv-1", "csv-2"]
    assert exported[1]["trail"] == [{"t": "click", "sel": "a"}]


# ------------------------------------------------------- the life of data ----


@pytest.mark.asyncio
async def test_kit_deletes_every_note(client: AsyncClient, db_session: AsyncSession, qa_on: None):
    join = await _start(client)
    for i in range(2):
        await client.post("/api/qa/notes", headers=join, json=_note(f"del-{i}"))
    resp = await client.delete("/api/qa/notes", headers=KIT)
    assert resp.json() == {"deleted": 2}
    assert await db_session.scalar(select(func.count()).select_from(QaNote)) == 0


@pytest.mark.asyncio
async def test_retention_deletes_old_notes_and_an_old_plan_with_the_module_off(db_session: AsyncSession):
    """A plan may hold logins and must not outlive its session."""
    assert get_settings().qa_feedback.enabled is False
    now = datetime.now(UTC)
    days = get_settings().qa_feedback.retention_days
    # One insert each: a bulk insert of uuid_utils ids trips insertmanyvalues sentinel matching.
    for client_id in ("old", "young"):
        db_session.add(QaNote(client_id=client_id, kind="ok"))
        await db_session.flush()
    await db_session.merge(
        QaState(id=1, plan=PLAN, step="A-01", key_hash="0" * 64, updated_at=now - timedelta(days=days + 1))
    )
    await db_session.commit()
    await db_session.execute(update(QaNote).where(QaNote.client_id == "old").values(created_at=now - timedelta(days=days + 1)))  # fmt: skip
    await db_session.commit()

    assert await sweep_qa_notes(db_session, reference_date=now) == 2
    await db_session.commit()

    assert (await db_session.execute(select(QaNote.client_id))).scalars().all() == ["young"]
    row = await db_session.get(QaState, 1, populate_existing=True)
    assert row is not None
    assert (row.plan, row.step, row.key_hash) == ([], None, None)


@pytest.mark.asyncio
async def test_deleting_an_organization_leaves_the_notes(client: AsyncClient, db_session: AsyncSession, qa_on: None):
    # The deletion lists the organization's objects, so the worker's bucket must exist
    # even when this file is the first on its worker to touch the store.
    ensure_bucket()
    join = await _start(client)
    now = datetime.now(UTC)
    org = await create_test_organization(db_session, name="Muenchen", slug="qa-muenchen")
    await client.post(
        "/api/qa/notes", headers=join, json=_note("org-1", context={"org": "qa-muenchen", "role": "org_admin"})
    )
    await OrganizationRepository(db_session).update(
        org.id, archived_at=now, deletion_requested_at=now - timedelta(days=DELETION_GRACE_DAYS + 1)
    )
    await db_session.commit()

    assert await sweep_due_organizations(db_session, reference_date=now) == 1

    assert await db_session.get(Organization, org.id) is None
    note = (await db_session.execute(select(QaNote).where(QaNote.client_id == "org-1"))).scalar_one()
    assert note.context["org"] == "qa-muenchen"


# ------------------------------------------- session scope, redaction, credentials ----


@pytest.mark.asyncio
async def test_an_event_of_an_ended_session_is_refused(client: AsyncClient, qa_on: None):
    """A queued event of the old session must not land in the new one."""
    await _start(client)
    old_session = SESSION["id"]
    join = await _start(client)
    late = await client.post("/api/qa/notes", headers=join, json=_event("ok", "A-01", session_id=old_session))
    assert late.status_code == 409
    assert (await client.get("/api/qa/state", headers=join)).json()["current"] == "A-01"


@pytest.mark.asyncio
async def test_the_kit_reads_one_session_and_the_card_title_stays(client: AsyncClient, qa_on: None):
    """Events are scoped to their session and keep their card title."""
    first = await _start(client)
    first_id = SESSION["id"]
    await client.post("/api/qa/notes", headers=first, json=_note("s1"))
    second = await _start(client)
    await client.post("/api/qa/notes", headers=second, json=_note("s2"))
    await client.delete("/api/qa/session", headers=KIT)

    only_first = (await client.get(f"/api/qa/notes?session={first_id}", headers=KIT)).json()["items"]
    assert [n["client_id"] for n in only_first] == ["s1"]
    assert only_first[0]["session_id"] == first_id
    assert only_first[0]["step_title"] == "Anmelden"
    exported = (await client.get(f"/api/qa/notes/export?session={first_id}", headers=KIT)).json()
    assert [n["client_id"] for n in exported] == ["s1"]


@pytest.mark.asyncio
async def test_secrets_in_routes_and_paths_are_redacted(client: AsyncClient, db_session: AsyncSession, qa_on: None):
    """Invitation tokens, secret query values and fragments never reach the store."""
    join = await _start(client)
    trail = [
        {"t": "http", "method": "GET", "path": "/api/invitations/Ab3dEf6hIj9kLm2nOp5qRs8tUv", "status": 404},
        {"t": "route", "to": "/reset-password?token=abc&lang=de#qa-join=xyz"},
        {"t": "route", "to": "/stations/clavis-demo-augsburger-altstadt-2026"},
        {
            "t": "http",
            "method": "GET",
            "path": "/api/orgs/demo/runs/01a191bb-d483-72b0-aa6c-447518fb04e5",
            "status": 500,
        },
    ]
    await client.post(
        "/api/qa/notes", headers=join, json=_note("red-1", route="/accept-invite?invite=x&tab=2", trail=trail)
    )
    note = (await db_session.execute(select(QaNote).where(QaNote.client_id == "red-1"))).scalar_one()
    assert note.route == "/accept-invite?invite=:redacted&tab=2"
    assert note.trail[0]["path"] == "/api/invitations/:redacted"
    assert note.trail[1]["to"] == "/reset-password?token=:redacted&lang=de"
    assert note.trail[2]["to"] == "/stations/clavis-demo-augsburger-altstadt-2026"
    assert note.trail[3]["path"] == "/api/orgs/demo/runs/01a191bb-d483-72b0-aa6c-447518fb04e5"


@pytest.mark.asyncio
async def test_the_state_read_follows_the_kit_rules(client: AsyncClient, db_session: AsyncSession, qa_on: None):
    """A wrong token is a 401, an operator session reads the state."""
    await _start(client)
    assert (await client.get("/api/qa/state", headers={"X-QA-Token": "x" * 40})).status_code == 401
    await _operator_cookie(client, db_session)
    state = (await client.get("/api/qa/state")).json()
    assert state["joined"] is True
    assert state["current"] == "A-01"


def test_the_default_event_limit():
    """The default event limit is 120 per minute."""
    from app.config import QaFeedbackSettings

    assert QaFeedbackSettings().note_rate_limit == "120/minute"


@pytest.mark.asyncio
async def test_two_taps_at_once_move_once(concurrent_client: AsyncClient, test_engine: object, qa_on: None):
    """Two simultaneous taps on the same step, each in its own session and
    transaction. The row lock serialises them: both are stored, exactly one moves."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncEngine

    assert isinstance(test_engine, AsyncEngine)
    try:
        join = await _start(concurrent_client)
        taps = [_event("ok", "A-01", client_id=f"race-{i}") for i in range(2)]
        answers = await asyncio.gather(*(concurrent_client.post("/api/qa/notes", headers=join, json=t) for t in taps))
        assert [a.status_code for a in answers] == [201, 201]
        assert sorted(a.json()["moved"] for a in answers) == [False, True]
        assert {a.json()["state"]["current"] for a in answers} == {"A-02"}
    finally:
        # The concurrent client commits for real: leave nothing for the next test on this worker.
        async with test_engine.begin() as conn:
            await conn.execute(text("DELETE FROM qa_notes"))
            await conn.execute(text("DELETE FROM qa_state"))


@pytest.mark.asyncio
async def test_a_stale_goto_moves_nothing(client: AsyncClient, qa_on: None):
    """A block jump made on A-01 must not undo a later move."""
    join = await _start(client)
    await client.post("/api/qa/notes", headers=join, json=_event("ok", "A-01", client_id="g-ok"))
    stale = await client.post(
        "/api/qa/notes", headers=join, json=_event("goto", "A-01", client_id="g-stale", target="E-01")
    )
    assert stale.json()["moved"] is False
    assert stale.json()["state"]["current"] == "A-02"


@pytest.mark.asyncio
async def test_click_text_and_errors_lose_codes_and_tokens(client: AsyncClient, db_session: AsyncSession, qa_on: None):
    """The server redacts a second time, also in text."""
    join = await _start(client)
    trail = [
        {"t": "click", "sel": "button.share", "txt": "PP4Y8"},
        {"t": "click", "sel": "button#save", "txt": "Speichern"},
        {"t": "error", "msg": "GET /api/invitations/Ab3dEf6hIj9kLm2nOp5qRs8tUv failed: 404"},
        {"t": "error", "msg": "token Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Ji1 rejected"},
    ]
    await client.post("/api/qa/notes", headers=join, json=_note("txt-1", trail=trail))
    note = (await db_session.execute(select(QaNote).where(QaNote.client_id == "txt-1"))).scalar_one()
    assert note.trail[0]["txt"] == ":redacted"
    assert note.trail[1]["txt"] == "Speichern"
    assert note.trail[2]["msg"] == "GET /api/invitations/:redacted failed: 404"
    assert note.trail[3]["msg"] == "token :redacted rejected"


@pytest.mark.asyncio
async def test_a_code_inside_a_control_name_is_redacted(client: AsyncClient, db_session: AsyncSession, qa_on: None):
    """A code inside a control name is redacted, though the name holds other words."""
    join = await _start(client)
    trail = [{"t": "click", "sel": "button.copy", "txt": "PP4Y8 — Code kopieren"}]
    await client.post("/api/qa/notes", headers=join, json=_note("code-1", trail=trail))
    note = (await db_session.execute(select(QaNote).where(QaNote.client_id == "code-1"))).scalar_one()
    assert note.trail[0]["txt"] == ":redacted — Code kopieren"


@pytest.mark.asyncio
async def test_staff_credentials_on_a_state_read_get_the_kit_rules(
    client: AsyncClient, client_org_a: AsyncClient, db_session: AsyncSession, qa_on: None
):
    """An organization session gets 403, an operator bearer gets the state."""
    await _start(client)
    assert (await client_org_a.get("/api/qa/state")).status_code == 403
    client.headers.pop("Authorization", None)

    from app.auth.instance import create_instance_admin
    from app.auth.tokens import create_instance_admin_token

    admin = await create_instance_admin(db_session, "bearer@qa.test", "OperatorPass123!")
    resp = await client.get(
        "/api/qa/state", headers={"Authorization": f"Bearer {create_instance_admin_token(admin.id)}"}
    )
    assert resp.status_code == 200
    assert resp.json()["current"] == "A-01"


@pytest.mark.asyncio
async def test_an_organization_cookie_on_a_state_read_is_refused(
    client: AsyncClient, user_in_org_a: object, qa_on: None
):
    """An organization cookie without any Authorization header is refused."""
    from tests.factories import get_auth_cookies

    await _start(client)
    for name, value in get_auth_cookies(user_in_org_a).items():  # type: ignore[arg-type]
        client.cookies.set(name, value)
    assert "Authorization" not in client.headers
    assert (await client.get("/api/qa/state")).status_code == 403
