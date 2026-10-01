"""
Demo-content loader.

Puts the bundled content file of ``app.demo`` into an existing organization:
three stations, seven challenges and one rallye, in the order of the file, in
one transaction. The organization comes from the operator console. This loader
adds what the console cannot: content in one step. It is a plain async
function, so the CLI (``app.cli``) and the tests call it with their own session.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.rallye import Rallye
from app.repositories.challenge import ChallengeRepository
from app.repositories.organization import OrganizationRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.rallye_station import RallyeStationRepository
from app.repositories.rallye_station_challenge import RallyeStationChallengeRepository
from app.repositories.station import StationRepository
from app.services.audit import record_audit_event
from app.services.scoring_rules import validate_scoring_rules

DEMO_CONTENT_PATH = Path(__file__).with_name("content.json")
_LOCALIZED_FIELDS = frozenset({"title", "description", "instructions", "suggested_solution"})


class DemoOrganizationNotFoundError(Exception):
    """No active organization exists under the given slug."""


class DemoOrganizationArchivedError(Exception):
    """The organization exists but is archived, so it accepts no content."""


class DemoContentExistsError(Exception):
    """The organization already holds a rallye with the demo title."""


@dataclass(frozen=True)
class DemoLoadResult:
    """What a successful load created — the counts the CLI prints."""

    stations: int
    challenges: int
    rallyes: int
    languages_added: tuple[str, ...] = ()


async def load_demo_content(
    db: AsyncSession, org_slug: str, replace: bool, enable_languages: bool = False
) -> DemoLoadResult:
    """Load the bundled demo content into the organization behind ``org_slug`` and commit on ``db``.

    Every challenge is validated (``validate_scoring_rules``) before the first
    write. The stations, challenges, rallye and link rows are created in one
    transaction, and the audit event ``demo_content.loaded`` follows the commit.

    ``replace`` soft-deletes an earlier demo load first. Runs are never touched:
    they work on frozen snapshots. ``enable_languages`` adds the content
    languages the organization lacks, in the same transaction. Only the CLI
    passes it. No HTTP route does, so the administrator's authenticator-app
    rule is untouched.

    Raises ``DemoOrganizationNotFoundError`` when no active organization has
    the slug, ``DemoOrganizationArchivedError`` when it is archived, and
    ``DemoContentExistsError`` when the demo rallye exists and ``replace`` is
    false.
    """
    content: dict[str, Any] = json.loads(DEMO_CONTENT_PATH.read_text(encoding="utf-8"))
    rallye_data: dict[str, Any] = content["rallye"]

    # Same validation as the editor, and before the first write. A bad
    # file must not leave a half-replaced organization behind.
    for station_data in rallye_data["stations"]:
        for challenge_data in station_data["challenges"]:
            validate_scoring_rules(
                ChallengeType(challenge_data["challenge_type"]),
                challenge_data["scoring_rules"],
            )

    org = await OrganizationRepository(db).get_by_slug(org_slug)
    if org is None:
        raise DemoOrganizationNotFoundError(f"No organization with slug '{org_slug}'")
    if await OrganizationRepository.is_archived(db, org.id):
        raise DemoOrganizationArchivedError(f"Organization '{org_slug}' is archived")

    station_repo = StationRepository(db, org.id)
    challenge_repo = ChallengeRepository(db, org.id)
    rallye_repo = RallyeRepository(db, org.id)
    rallye_station_repo = RallyeStationRepository(db, org.id)
    rallye_station_challenge_repo = RallyeStationChallengeRepository(db, org.id)

    existing = await _find_demo_rallyes(db, org.id, rallye_data["title"])
    if existing and not replace:
        raise DemoContentExistsError(
            f"Organization '{org_slug}' already has the demo rallye "
            f"'{rallye_data['title']['de']}' — pass --replace to reload it"
        )
    for stale_rallye in existing:
        # Retire the earlier load so a reload cannot double the content. Runs
        # stay: they work on frozen snapshots.
        stale_links = await rallye_station_repo.list_by_rallye(stale_rallye.id)
        for link in stale_links:
            for challenge_link in await rallye_station_challenge_repo.list_by_rallye_station(link.id):
                await challenge_repo.soft_delete(challenge_link.challenge_id)
            await rallye_station_challenge_repo.delete_all_for_rallye_station(link.id)
        for link in stale_links:
            await station_repo.soft_delete(link.station_id)
            await rallye_station_repo.delete_all_for_station(link.station_id)
        await rallye_repo.soft_delete(stale_rallye.id)

    # Published on arrival, so the presenter can start a run at once instead of publishing it in the editor first.
    rallye = await rallye_repo.create(
        title=rallye_data["title"],
        description=rallye_data["description"],
        is_published=True,
    )
    stations_loaded = 0
    challenges_loaded = 0
    for station_order, station_data in enumerate(rallye_data["stations"]):
        station = await station_repo.create(
            title=station_data["title"],
            description=station_data["description"],
            address=station_data.get("address"),
            location_lat=station_data.get("location_lat"),
            location_lng=station_data.get("location_lng"),
        )
        link = await rallye_station_repo.create(
            rallye_id=rallye.id,
            station_id=station.id,
            sort_order=station_order,
        )
        for challenge_order, challenge_data in enumerate(station_data["challenges"]):
            challenge = await challenge_repo.create(
                challenge_type=ChallengeType(challenge_data["challenge_type"]),
                title=challenge_data["title"],
                instructions=challenge_data["instructions"],
                scoring_rules=challenge_data["scoring_rules"],
                time_estimate_minutes=challenge_data.get("time_estimate_minutes"),
                suggested_solution=challenge_data.get("suggested_solution"),
            )
            await rallye_station_challenge_repo.create(
                rallye_station_id=link.id,
                challenge_id=challenge.id,
                sort_order=challenge_order,
            )
            challenges_loaded += 1
        stations_loaded += 1

    languages_added: tuple[str, ...] = ()
    if enable_languages:
        missing = tuple(code for code in _content_languages(rallye_data) if code not in org.languages)
        if missing:
            await OrganizationRepository(db).update(org.id, languages=[*org.languages, *missing])
            languages_added = missing

    await db.commit()

    record_audit_event(
        event="demo_content.loaded",
        actor={"type": "cli"},
        organization_id=org.id,
        stations=stations_loaded,
        challenges=challenges_loaded,
        rallyes=1,
        languages_added=list(languages_added),
    )

    return DemoLoadResult(
        stations=stations_loaded,
        challenges=challenges_loaded,
        rallyes=1,
        languages_added=languages_added,
    )


def _content_languages(node: Any) -> list[str]:
    """Language codes used by the localized fields of the content, in order of first appearance."""
    codes: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _LOCALIZED_FIELDS and isinstance(value, dict):
                codes.extend(code for code in value if code not in codes)
            else:
                codes.extend(code for code in _content_languages(value) if code not in codes)
    elif isinstance(node, list):
        for item in node:
            codes.extend(code for code in _content_languages(item) if code not in codes)
    return codes


async def _find_demo_rallyes(db: AsyncSession, organization_id: UUID, title: dict[str, str]) -> list[Rallye]:
    """Non-deleted rallyes of the organization whose title equals the demo title."""
    stmt = (
        select(Rallye)
        .where(
            Rallye.organization_id == organization_id,
            Rallye.is_deleted == False,  # noqa: E712
            Rallye.title == title,
        )
        .order_by(Rallye.created_at.asc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
