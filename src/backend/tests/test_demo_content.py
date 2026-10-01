"""
Demo content: the content file, the loader, the run and the CLI.

Covers the content file itself (shape of the content API, validate_scoring_rules),
the loader (fresh load in file order, refusal without --replace, --replace keeping
runs and their snapshots, missing or archived organization) and the run a coordinator
starts from the loaded rallye (quiz and QR score themselves. Photo, text and a
text sent as a photo fallback wait for grading, with a real photo answer).

The audit test reuses the ``configured_logging`` fixture of ``test_audit.py`` and
verifies the written file with ``app/tools/verify_audit_log.py``. It never
replaces ``record_audit_event``.
"""

import ast
import json
import re
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import AsyncClient
from minio import Minio
from minio.error import S3Error
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.demo.loader import (
    DEMO_CONTENT_PATH,
    DemoContentExistsError,
    DemoLoadResult,
    DemoOrganizationArchivedError,
    DemoOrganizationNotFoundError,
    _content_languages,
    load_demo_content,
)
from app.models.challenge import ChallengeType
from app.models.run import Run, RunState
from app.models.run_station import RunStation
from app.models.user import UserRole
from app.repositories.challenge import ChallengeRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.rallye_station import RallyeStationRepository
from app.repositories.rallye_station_challenge import RallyeStationChallengeRepository
from app.repositories.score import ScoreRepository
from app.repositories.station import StationRepository
from app.routers.schemas import ChallengeCreateRequest, StationCreateRequest
from app.services.scoring_rules import validate_scoring_rules
from app.storage import ensure_bucket, get_minio_client
from app.tools.verify_audit_log import verify_audit_log
from tests.factories import create_test_organization, create_test_user_with_org, get_auth_headers
from tests.test_audit import configured_logging, read_audit_log, temp_log_dir  # noqa: F401  (fixtures)
from tests.test_image_variants import plain_image

# The demo content in the order of the content file.
EXPECTED_STATION_TITLES = [
    ("Rathausplatz", "Town hall square"),
    ("Perlachturm", "Perlach Tower"),
    ("Fuggerei", "Fuggerei"),
]
EXPECTED_CHALLENGE_TYPES = [
    ["photo", "text"],
    ["quiz", "quiz"],
    ["quiz", "photo", "qr_scan"],
]


@pytest_asyncio.fixture(scope="function")
async def storage_bucket() -> Minio:
    """Ensure the S3 bucket exists for the photo upload, wipe it after (as test_submissions.py)."""
    ensure_bucket()
    client = get_minio_client()
    yield client

    try:
        objects = client.list_objects(settings.storage.bucket, recursive=True)
        for obj in objects:
            client.remove_object(settings.storage.bucket, obj.object_name)
    except S3Error:
        pass  # Cleanup is best-effort


# ============================================================================
# The content file
# ============================================================================


def test_content_file_matches_content_api_and_validates():
    """
    The bundled file holds exactly the rallye, three stations and seven
    challenges, in order, every text in ``de`` and ``en``, its
    station/challenge fields accepted by the content API's create requests,
    and every challenge passing validate_scoring_rules.
    """
    content = json.loads(DEMO_CONTENT_PATH.read_text(encoding="utf-8"))
    rallye = content["rallye"]

    assert set(rallye) == {"title", "description", "stations"}
    assert set(rallye["title"]) == {"de", "en"}
    assert set(rallye["description"]) == {"de", "en"}

    stations = rallye["stations"]
    assert len(stations) == 3
    challenges_total = 0
    for station_data, expected_types in zip(stations, EXPECTED_CHALLENGE_TYPES, strict=True):
        station_fields = {k: v for k, v in station_data.items() if k != "challenges"}
        StationCreateRequest(**station_fields)  # raises unless it fits the content API
        assert set(station_data["title"]) == {"de", "en"}
        assert set(station_data["description"]) == {"de", "en"}

        challenges = station_data["challenges"]
        assert [c["challenge_type"] for c in challenges] == expected_types
        for challenge_data in challenges:
            ChallengeCreateRequest(**challenge_data)  # raises unless it fits the content API
            assert set(challenge_data["title"]) == {"de", "en"}
            assert set(challenge_data["instructions"]) == {"de", "en"}
            validate_scoring_rules(ChallengeType(challenge_data["challenge_type"]), challenge_data["scoring_rules"])
        challenges_total += len(challenges)
    assert challenges_total == 7


# A language tag as the organization settings accept it (``LanguageTag`` in app/routers/schemas.py).
_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")


def _localized_objects(node: object, field: str = "") -> list[tuple[str, list[str]]]:
    """Every object of the content whose keys are all language tags and whose values are all text."""
    found: list[tuple[str, list[str]]] = []
    if isinstance(node, dict):
        if (
            node
            and all(isinstance(k, str) and _LANGUAGE_TAG.match(k) for k in node)
            and all(isinstance(v, str) for v in node.values())
        ):
            found.append((field, list(node)))
        for key, value in node.items():
            found.extend(_localized_objects(value, key))
    elif isinstance(node, list):
        for item in node:
            found.extend(_localized_objects(item, field))
    return found


def test_loader_finds_every_language_of_the_content():
    """
    --enable-languages adds the languages that _content_languages finds, and that
    function looks only at a fixed list of field names. A localized field added to
    the content under a new name, or a language added in such a field, would be
    missed without an error. Find every localized object of the file by its shape
    and require the loader to see the same languages.
    """
    rallye = json.loads(DEMO_CONTENT_PATH.read_text(encoding="utf-8"))["rallye"]
    localized = _localized_objects(rallye)
    assert localized, "the content holds no localized text at all"

    in_content = {code for _, codes in localized for code in codes}
    assert set(_content_languages(rallye)) == in_content, (
        f"localized fields of the content: {sorted({field for field, _ in localized})}; "
        "add a new one to _LOCALIZED_FIELDS in app/demo/loader.py"
    )


# ============================================================================
# A load into a fresh organization
# ============================================================================


@pytest.mark.asyncio
async def test_load_creates_content_of_file_in_order(
    db_session: AsyncSession,
    configured_logging: str,  # noqa: F811  (fixture imported from tests.test_audit)
):
    """
    A load into a fresh organization creates exactly 3 stations, 7 challenges
    and 1 rallye — in the order of the file, in both languages — and exactly
    one demo_content.loaded audit event.
    """
    org = await create_test_organization(db_session, name="Demo Org", slug="demo-fresh-org")

    result = await load_demo_content(db_session, org.slug, replace=False)

    assert result == DemoLoadResult(stations=3, challenges=7, rallyes=1)
    assert await StationRepository(db_session, org.id).count() == 3
    assert await ChallengeRepository(db_session, org.id).count() == 7
    rallyes = await RallyeRepository(db_session, org.id).list()
    assert len(rallyes) == 1

    rallye = rallyes[0]
    assert rallye.title == {"de": "Clavis-Demo: Augsburger Altstadt", "en": "Clavis demo: Augsburg old town"}
    assert rallye.is_published is True

    # Station order and per-station challenge order come from the link rows.
    links = await RallyeStationRepository(db_session, org.id).list_by_rallye(rallye.id)
    assert [link.sort_order for link in links] == [0, 1, 2]
    assert [(link.station.title["de"], link.station.title["en"]) for link in links] == EXPECTED_STATION_TITLES

    challenge_repo = ChallengeRepository(db_session, org.id)
    rsc_repo = RallyeStationChallengeRepository(db_session, org.id)
    for link, expected_types in zip(links, EXPECTED_CHALLENGE_TYPES, strict=True):
        station = link.station
        assert station.title["de"] and station.title["en"]
        assert set(station.description) == {"de", "en"}
        assert station.address is not None
        assert station.location_lat is not None
        assert station.location_lng is not None

        challenge_links = await rsc_repo.list_by_rallye_station(link.id)
        assert [cl.sort_order for cl in challenge_links] == list(range(len(expected_types)))
        assert [cl.challenge.challenge_type.value for cl in challenge_links] == expected_types
        for cl in challenge_links:
            challenge = await challenge_repo.get_by_id(cl.challenge_id)
            assert challenge is not None
            assert set(challenge.title) == {"de", "en"}
            assert set(challenge.instructions) == {"de", "en"}
            # "Ein gutes Zuhause" is a photo task with a text fallback.
            # It counts double: 20 points and 10 minutes.
            good_home = challenge.title["de"] == "Ein gutes Zuhause"
            expected_points = 20 if good_home else 10
            assert challenge.scoring_rules["max_points"] == expected_points
            if challenge.challenge_type in (ChallengeType.QUIZ, ChallengeType.QR_SCAN):
                assert challenge.scoring_rules["auto_approve"] is True
            else:
                assert "auto_approve" not in challenge.scoring_rules
            expected_minutes = 10 if good_home else 5
            assert challenge.time_estimate_minutes == expected_minutes

    # The QR challenge expects the printed demo code.
    qr_links = await rsc_repo.list_by_rallye_station(links[2].id)
    assert qr_links[2].challenge.scoring_rules["expected_value"] == "CLAVIS-DEMO-ZIEL"

    # Exactly one audit event, with the organization, the counts and the cli actor.
    events = [e for e in read_audit_log(configured_logging) if e.get("event") == "demo_content.loaded"]
    assert len(events) == 1
    assert events[0]["actor"] == {"type": "cli"}
    assert events[0]["organization_id"] == str(org.id)
    assert events[0]["stations"] == 3
    assert events[0]["challenges"] == 7
    assert events[0]["rallyes"] == 1


# ============================================================================
# Refusal without --replace, reload with it
# ============================================================================


@pytest.mark.asyncio
async def test_second_load_without_replace_refuses_and_changes_nothing(db_session: AsyncSession):
    """A second load without replace refuses with the typed error and leaves everything as it was."""
    org = await create_test_organization(db_session, name="Demo Org", slug="demo-twice-org")
    first = await load_demo_content(db_session, org.slug, replace=False)
    station_repo = StationRepository(db_session, org.id)
    stations_before = [s.id for s in await station_repo.list()]

    with pytest.raises(DemoContentExistsError):
        await load_demo_content(db_session, org.slug, replace=False)

    assert await station_repo.count() == first.stations
    assert [s.id for s in await station_repo.list()] == stations_before
    assert await ChallengeRepository(db_session, org.id).count() == first.challenges
    assert await RallyeRepository(db_session, org.id).count() == 1


@pytest.mark.asyncio
async def test_replace_soft_deletes_previous_load_and_keeps_runs(
    client: AsyncClient,
    db_session: AsyncSession,
    configured_logging: str,  # noqa: F811  (fixture imported from tests.test_audit)
):
    """
    A load with --replace soft-deletes the earlier demo rallye, stations and
    challenges, leaves exactly one demo rallye, and keeps an existing run and
    its frozen snapshot unchanged.
    """
    user, org = await create_test_user_with_org(
        db_session,
        email="demo-replace@test.com",
        role=UserRole.COORDINATOR,
        org_slug="demo-replace-org",
    )
    headers = get_auth_headers(user)
    await load_demo_content(db_session, org.slug, replace=False)

    first_rallye = (await RallyeRepository(db_session, org.id).list())[0]
    rallye_station_repo = RallyeStationRepository(db_session, org.id)
    first_stations = [link.station_id for link in await rallye_station_repo.list_by_rallye(first_rallye.id)]

    # Start a run from the demo rallye, as the presenter does.
    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(first_rallye.id), "name": "Demo run"},
    )
    assert run_response.status_code == 201
    run_id = UUID(run_response.json()["id"])
    assert (await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)).status_code == 200

    async def _snapshot() -> list[tuple[UUID, str | None, str | None]]:
        stmt = select(RunStation).where(RunStation.run_id == run_id).order_by(RunStation.sort_order)
        rows = (await db_session.execute(stmt)).scalars().all()
        return [
            (row.id, str(row.source_station_id) if row.source_station_id else None, row.title.get("de")) for row in rows
        ]

    snapshot_before = await _snapshot()
    assert len(snapshot_before) == 3

    result = await load_demo_content(db_session, org.slug, replace=True)

    # Exactly one live demo rallye; the earlier load is soft-deleted, runs untouched.
    assert result == DemoLoadResult(stations=3, challenges=7, rallyes=1)
    rallye_repo = RallyeRepository(db_session, org.id)
    assert await rallye_repo.count() == 1
    stale = await rallye_repo.get_by_id(first_rallye.id, include_deleted=True)
    assert stale is not None and stale.is_deleted is True

    station_repo = StationRepository(db_session, org.id)
    assert await station_repo.count() == 3
    assert await station_repo.count(include_deleted=True) == 6
    assert await ChallengeRepository(db_session, org.id).count() == 7
    assert await ChallengeRepository(db_session, org.id).count(include_deleted=True) == 14
    live_rallye = (await rallye_repo.list())[0]
    live_stations = [link.station_id for link in await rallye_station_repo.list_by_rallye(live_rallye.id)]
    assert not set(live_stations) & set(first_stations)

    run = (await db_session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.state == RunState.ACTIVE
    assert await _snapshot() == snapshot_before


# ============================================================================
# Missing slug and archived organization
# ============================================================================


@pytest.mark.asyncio
async def test_missing_slug_refuses_without_writing(db_session: AsyncSession):
    org = await create_test_organization(db_session, name="Other Org", slug="demo-real-org")

    with pytest.raises(DemoOrganizationNotFoundError):
        await load_demo_content(db_session, "does-not-exist", replace=False)

    assert await StationRepository(db_session, org.id).count() == 0


@pytest.mark.asyncio
async def test_archived_organization_refuses_without_writing(db_session: AsyncSession):
    org = await create_test_organization(db_session, name="Archived Org", slug="demo-archived-org")
    org.archived_at = datetime.now(UTC)
    await db_session.commit()

    with pytest.raises(DemoOrganizationArchivedError):
        await load_demo_content(db_session, org.slug, replace=False)

    assert await StationRepository(db_session, org.id).count() == 0


# ============================================================================
# A run from the loaded rallye
# ============================================================================


@pytest.mark.asyncio
async def test_run_from_loaded_rallye_scores_quiz_and_qr_and_waits_on_photo_and_text(
    client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
):
    """
    A coordinator starts a run from the loaded rallye: a correct quiz answer
    and the right QR value score themselves; a photo, a text and a text sent
    as the fallback of the second photo task (the first photo submitted for
    real, with an uploaded file) each stay without a score until a
    coordinator grades them.
    """
    user, org = await create_test_user_with_org(
        db_session,
        email="demo-run@test.com",
        role=UserRole.COORDINATOR,
        org_slug="demo-run-org",
    )
    headers = get_auth_headers(user)
    await load_demo_content(db_session, org.slug, replace=False)

    rallye = (await RallyeRepository(db_session, org.id).list())[0]
    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Demo run"},
    )
    assert run_response.status_code == 201
    run_id = run_response.json()["id"]
    assert (await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)).status_code == 200

    # Walk the snapshot: three stations, seven challenges, in file order.
    run_challenges: dict[str, list[dict]] = {}
    stations_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    assert len(stations_response.json()["items"]) == 3
    for station_item in stations_response.json()["items"]:
        challenges_response = await client.get(
            f"/api/orgs/{org.slug}/runs/{run_id}/stations/{station_item['id']}/challenges/", headers=headers
        )
        for item in challenges_response.json()["items"]:
            run_challenges.setdefault(item["challenge_type"], []).append(item)
    assert {kind: len(items) for kind, items in run_challenges.items()} == {
        "photo": 2,
        "text": 1,
        "quiz": 3,
        "qr_scan": 1,
    }

    # Create a team and join it, because submissions belong to a team.
    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Demo team"},
    )
    assert team_response.status_code == 201
    join_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_response.json()['id']}/join", headers=headers
    )
    assert join_response.status_code == 204

    submissions_url = f"/api/orgs/{org.slug}/runs/{run_id}/submissions/"

    async def _submit(run_challenge: dict, content: dict) -> dict:
        response = await client.post(
            submissions_url,
            headers=headers,
            json={"run_challenge_id": run_challenge["id"], "content": content},
        )
        assert response.status_code == 201, response.text
        return response.json()

    # A real photo answer: upload a file, submit its object key.
    upload_response = await client.post(
        f"{submissions_url}upload/",
        headers=headers,
        files={"file": ("demo-team.jpg", BytesIO(plain_image((8, 8), "JPEG")), "image/jpeg")},
    )
    assert upload_response.status_code == 201
    first_photo, good_home = sorted(run_challenges["photo"], key=lambda rc: rc["title"]["de"] == "Ein gutes Zuhause")
    photo = await _submit(first_photo, {"photo_url": upload_response.json()["object_key"]})

    text = await _submit(run_challenges["text"][0], {"text": "Wir sind ein Team aus Augsburg."})
    fallback = await _submit(good_home, {"text": "Ein gutes Zuhause hat Platz fuer alle."})

    height_quiz = next(rc for rc in run_challenges["quiz"] if rc["title"]["de"] == "Wie hoch ist der Perlachturm?")
    quiz = await _submit(height_quiz, {"selected_option": "70 m"})
    qr = await _submit(run_challenges["qr_scan"][0], {"scanned_value": "CLAVIS-DEMO-ZIEL"})

    score_repo = ScoreRepository(db_session, org.id)
    quiz_score = await score_repo.get_by_submission(UUID(quiz["id"]))
    assert quiz_score is not None
    assert quiz_score.points == 10
    assert quiz_score.max_points == 10
    qr_score = await score_repo.get_by_submission(UUID(qr["id"]))
    assert qr_score is not None
    assert qr_score.points == 10
    assert qr_score.max_points == 10

    # Photo, text and the fallback text wait for grading: no score exists for them.
    assert await score_repo.get_by_submission(UUID(photo["id"])) is None
    assert await score_repo.get_by_submission(UUID(text["id"])) is None
    assert await score_repo.get_by_submission(UUID(fallback["id"])) is None


# ============================================================================
# The audit event: chained into the real audit log file
# ============================================================================


@pytest.mark.asyncio
async def test_load_audit_event_lands_in_chained_audit_log(
    db_session: AsyncSession,
    configured_logging: str,  # noqa: F811  (fixture imported from tests.test_audit)
):
    """
    The demo_content.loaded event reaches the audit log file through the real
    handler (record_audit_event untouched), and the whole chain verifies with
    app/tools/verify_audit_log.py.
    """
    org = await create_test_organization(db_session, name="Demo Audit Org", slug="demo-audit-org")

    await load_demo_content(db_session, org.slug, replace=False)

    events = [e for e in read_audit_log(configured_logging) if e.get("event") == "demo_content.loaded"]
    assert len(events) == 1
    assert events[0]["actor"] == {"type": "cli"}
    assert events[0]["organization_id"] == str(org.id)
    assert events[0]["stations"] == 3
    assert events[0]["challenges"] == 7
    assert events[0]["rallyes"] == 1

    assert verify_audit_log(Path(configured_logging)) is True


# ============================================================================
# CLI wiring: main([...]) exit codes
# ============================================================================


def test_cli_demo_content_load_exit_codes(monkeypatch, capsys):
    """
    The CLI entry point configures logging and the audit chain before it
    loads, prints the counts and exits 0 on success, and prints each typed
    error's reason on stderr with exit code 2.
    """
    from app import cli

    ensured: list[bool] = []
    chained: list[bool] = []
    monkeypatch.setattr(cli, "ensure_logging_configured", lambda: ensured.append(True))
    monkeypatch.setattr(cli, "init_audit_chain", lambda: chained.append(True))

    async def refuse(org_slug: str, replace: bool, enable_languages: bool = False) -> DemoLoadResult:
        raise DemoContentExistsError(f"Organization '{org_slug}' already has the demo rallye")

    monkeypatch.setattr(cli, "_load_demo_content", refuse)
    exit_code = cli.main(["demo-content", "load", "--org-slug", "demo-org"])
    assert exit_code == 2
    assert "already has the demo rallye" in capsys.readouterr().err

    async def succeed(org_slug: str, replace: bool, enable_languages: bool = False) -> DemoLoadResult:
        return DemoLoadResult(stations=3, challenges=7, rallyes=1)

    monkeypatch.setattr(cli, "_load_demo_content", succeed)
    exit_code = cli.main(["demo-content", "load", "--org-slug", "demo-org", "--replace"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "3 stations" in captured.out
    assert "7 challenges" in captured.out

    assert ensured == [True, True]
    assert chained == [True, True]


# ============================================================================
# --enable-languages: the languages of the content, in the same transaction
# ============================================================================


@pytest.mark.asyncio
async def test_enable_languages_adds_missing_languages_and_names_them_in_audit(
    db_session: AsyncSession,
    configured_logging: str,  # noqa: F811  (fixture imported from tests.test_audit)
):
    """
    With the flag the loader adds the languages of the content that the
    organization lacks (here: en), keeps the ones it has, and the audit event
    names the added languages.
    """
    org = await create_test_organization(db_session, name="Lang Org", slug="demo-lang-org", languages=["de"])

    result = await load_demo_content(db_session, org.slug, replace=False, enable_languages=True)

    await db_session.refresh(org)
    assert org.languages == ["de", "en"]
    assert result.languages_added == ("en",)
    events = [e for e in read_audit_log(configured_logging) if e.get("event") == "demo_content.loaded"]
    assert len(events) == 1
    assert events[0]["languages_added"] == ["en"]


@pytest.mark.asyncio
async def test_without_enable_languages_the_organization_keeps_its_languages(db_session: AsyncSession):
    """Without the flag nothing about the languages changes."""
    org = await create_test_organization(db_session, name="Lang Org 2", slug="demo-lang-org-2", languages=["de"])

    result = await load_demo_content(db_session, org.slug, replace=False)

    await db_session.refresh(org)
    assert org.languages == ["de"]
    assert result.languages_added == ()


@pytest.mark.asyncio
async def test_enable_languages_with_all_languages_present_adds_nothing(db_session: AsyncSession):
    """An organization that has both languages is left as it is, in its own order."""
    org = await create_test_organization(db_session, name="Lang Org 3", slug="demo-lang-org-3", languages=["en", "de"])

    result = await load_demo_content(db_session, org.slug, replace=False, enable_languages=True)

    await db_session.refresh(org)
    assert org.languages == ["en", "de"]
    assert result.languages_added == ()


@pytest.mark.asyncio
async def test_enable_languages_changes_nothing_when_the_load_refuses(db_session: AsyncSession):
    """A refused load (content exists) must not add languages on the side."""
    org = await create_test_organization(db_session, name="Lang Org 4", slug="demo-lang-org-4", languages=["de"])
    await load_demo_content(db_session, org.slug, replace=False)

    with pytest.raises(DemoContentExistsError):
        await load_demo_content(db_session, org.slug, replace=False, enable_languages=True)

    await db_session.refresh(org)
    assert org.languages == ["de"]


def test_cli_passes_enable_languages_flag(monkeypatch):
    """--enable-languages reaches the loader; without it the loader gets False."""
    from app import cli

    monkeypatch.setattr(cli, "ensure_logging_configured", lambda: None)
    monkeypatch.setattr(cli, "init_audit_chain", lambda: None)
    seen: list[bool] = []

    async def record(org_slug: str, replace: bool, enable_languages: bool = False) -> DemoLoadResult:
        seen.append(enable_languages)
        return DemoLoadResult(stations=3, challenges=7, rallyes=1)

    monkeypatch.setattr(cli, "_load_demo_content", record)
    assert cli.main(["demo-content", "load", "--org-slug", "demo"]) == 0
    assert cli.main(["demo-content", "load", "--org-slug", "demo", "--enable-languages"]) == 0
    assert seen == [False, True]


# ============================================================================
# --enable-languages writes an organization setting: the CLI is its only caller
# ============================================================================

APP_DIR = Path(__file__).resolve().parent.parent / "app"
# The command line and the loader's own package may name the loader; nothing else in the app may.
_LOADER_ALLOWED = {APP_DIR / "cli.py"} | set((APP_DIR / "demo").rglob("*.py"))
# Where a request, a socket, the startup or a background task runs; each must be inside the scan.
_SERVER_SIDE = [
    APP_DIR / "routers",
    APP_DIR / "routers" / "ws.py",
    APP_DIR / "services" / "ws_manager.py",
    APP_DIR / "main.py",
    APP_DIR / "database.py",
    APP_DIR / "services" / "retention_tasks.py",
    APP_DIR / "services" / "pg_notify.py",
]


_LOADER_NAME = "load_demo_content"


def _is_demo_package(module: str) -> bool:
    return module == "app.demo" or module.startswith("app.demo.")


def _module_of(path: Path) -> str:
    """Dotted module path of a file under app/; a package's __init__.py keeps "__init__" as its last part."""
    return ".".join(path.relative_to(APP_DIR.parent).with_suffix("").parts)


def _resolve_import_from(node: ast.ImportFrom, module: str) -> str:
    """The absolute module of a from-import in ``module``; level 1 is the package of the file itself."""
    if node.level == 0:
        return node.module or ""
    package = module.split(".")[:-1]
    base = package[: len(package) - (node.level - 1)] if node.level - 1 <= len(package) else []
    return ".".join([*base, *([node.module] if node.module else [])])


def _names_the_loader(source: str, module: str) -> list[str]:
    """
    Every way one source file reaches app.demo or load_demo_content: an absolute or
    relative import of the demo package or of its modules, an import of the name
    load_demo_content from anywhere and under any alias, a use of that name, and a
    text constant that names app.demo (importlib.import_module("app.demo.loader")).
    ``module`` is the dotted path of the file, e.g. ``app.routers.example``.
    """
    hits: list[str] = []
    for node in ast.walk(ast.parse(source, filename=module)):
        if isinstance(node, ast.Import):
            hits.extend(f"import {a.name}" for a in node.names if _is_demo_package(a.name))
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_import_from(node, module)
            names = [a.name for a in node.names]
            if _is_demo_package(resolved):
                hits.append(f"from {resolved} import {', '.join(names)} (line {node.lineno})")
            elif resolved == "app" and "demo" in names:
                hits.append(f"from app import demo (line {node.lineno})")
            elif _LOADER_NAME in names:
                hits.append(f"from {resolved} import {_LOADER_NAME} (line {node.lineno})")
        elif isinstance(node, ast.Name) and node.id == _LOADER_NAME:
            hits.append(f"{_LOADER_NAME} (line {node.lineno})")
        elif isinstance(node, ast.Attribute) and node.attr == _LOADER_NAME:
            hits.append(f".{_LOADER_NAME} (line {node.lineno})")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and ("app.demo" in node.value or _LOADER_NAME in node.value)
        ):
            hits.append(f"text constant {node.value!r} (line {node.lineno})")
    return hits


@pytest.mark.parametrize(
    "source",
    [
        "from ..demo.loader import load_demo_content as seed\nseed(s, 'demo', False, enable_languages=True)\n",
        "from .. import demo\n",
        "from ..demo import loader\n",
        "from ...app.demo import loader\n",
        "from app.services.anywhere import load_demo_content as seed\n",
        "from . import load_demo_content\n",
        "import importlib\nimportlib.import_module('app.demo.loader')\n",
        "import app.demo.loader as seed\n",
        "from app import demo\n",
        "import app\napp.demo.loader.load_demo_content(s, 'demo', False)\n",
    ],
    ids=[
        "relative-alias",
        "relative-package",
        "relative-module",
        "relative-above-app",
        "name-from-any-module",
        "name-relative",
        "importlib-text",
        "absolute-alias",
        "absolute-package",
        "attribute-call",
    ],
)
def test_loader_guard_finds_each_way_to_the_loader(source: str):
    assert _names_the_loader(source, "app.routers.example"), source


def test_loader_guard_passes_a_clean_router():
    source = (
        "from fastapi import APIRouter\n"
        "from ..services import retention_tasks\n"
        "from app.demo_like import thing\n"
        "router = APIRouter()\n"
        "DEMO = 'demo'\n"
    )
    assert _names_the_loader(source, "app.routers.example") == []


def test_only_the_cli_reaches_the_demo_loader():
    """
    load_demo_content(enable_languages=True) changes the languages of an
    organization without a session, without the administrator's authenticator-app
    rule. That is right for the command line, which runs on the server, and wrong
    for anything the network or the running server reaches. Read the sources of
    the app, do not start it: no router, WebSocket handler, startup code or
    background task may import or call the loader.
    """
    scanned = [p for p in sorted(APP_DIR.rglob("*.py")) if p not in _LOADER_ALLOWED]
    for place in _SERVER_SIDE:
        assert place.exists(), f"{place} is gone; point the guard at where that code lives now"
        assert any(p == place or place in p.parents for p in scanned), f"{place} is not scanned"

    offenders = {
        str(p.relative_to(APP_DIR)): hits
        for p in scanned
        if (hits := _names_the_loader(p.read_text(encoding="utf-8"), _module_of(p)))
    }
    assert offenders == {}, f"only app/cli.py may reach the demo loader: {offenders}"
