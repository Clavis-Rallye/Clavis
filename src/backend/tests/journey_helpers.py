"""
Shared setup for the journey tests (test_journey_*.py).

It builds an organization with its users and a published rallye, and wraps the API calls a
journey repeats. The three acts that dissolve a team send the confirmation the API asks for.
read_run_numbers reads every number a client sees of the teams of a run.
"""

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.repositories.challenge import ChallengeRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.rallye_station import RallyeStationRepository
from app.repositories.rallye_station_challenge import RallyeStationChallengeRepository
from app.repositories.station import StationRepository
from tests.factories import create_test_user, create_test_user_with_org, get_auth_headers


@dataclass
class JourneyEnv:
    """
    The organization, users, auth headers and content of a journey test.

    ``users`` and ``auth_headers`` are keyed by role name: org_admin, creator, coordinator,
    participant1 and participant2. ``station_ids`` and ``rallye_station_ids`` follow sort_order.
    ``challenge_ids`` holds one list of challenge ids per station, in the same order.
    """

    org: Organization
    slug: str
    users: dict[str, User]
    auth_headers: dict[str, dict[str, str]]
    rallye_id: UUID
    station_ids: list[UUID]
    rallye_station_ids: list[UUID]
    challenge_ids: list[list[UUID]]
    challenge_types: dict[UUID, ChallengeType]


async def setup_journey_environment(
    db_session: AsyncSession,
    client: AsyncClient,
    org_slug: str = "journey-org",
    num_stations: int = 3,
    challenges_per_station: int = 2,
    challenge_type_override: ChallengeType | None = None,
) -> JourneyEnv:
    """
    Create and commit an organization, its users and a published rallye.

    The users are an org admin, a creator, a coordinator and two registered participants,
    each with auth headers. The rallye has ``num_stations`` stations of ``challenges_per_station``
    challenges. Their types cycle through all five, unless ``challenge_type_override`` sets one.
    Every challenge has scoring_rules with max_points. The content is in de and en.
    ``client`` is unused and kept for signature compatibility.
    """
    # org_slug makes the emails unique across calls.
    org_admin, org = await create_test_user_with_org(
        db_session,
        email=f"admin@{org_slug}.test",
        password="AdminPass123!",
        display_name="Admin User",
        role=UserRole.ORG_ADMIN,
        org_name="Journey Test Organization",
        org_slug=org_slug,
    )

    creator = await create_test_user(
        db_session,
        email=f"creator@{org_slug}.test",
        password="CreatorPass123!",
        display_name="Creator User",
        role=UserRole.CREATOR,
        organization=org,
    )

    coordinator = await create_test_user(
        db_session,
        email=f"coordinator@{org_slug}.test",
        password="CoordinatorPass123!",
        display_name="Coordinator User",
        role=UserRole.COORDINATOR,
        organization=org,
    )

    participant1 = await create_test_user(
        db_session,
        email=f"participant1@{org_slug}.test",
        password="ParticipantPass123!",
        display_name="Participant 1",
        role=UserRole.PARTICIPANT,
        organization=org,
    )

    participant2 = await create_test_user(
        db_session,
        email=f"participant2@{org_slug}.test",
        password="ParticipantPass123!",
        display_name="Participant 2",
        role=UserRole.PARTICIPANT,
        organization=org,
    )

    users = {
        "org_admin": org_admin,
        "creator": creator,
        "coordinator": coordinator,
        "participant1": participant1,
        "participant2": participant2,
    }

    auth_headers = {role: get_auth_headers(user) for role, user in users.items()}

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Testrallye", "en": "Test Rallye"},
        description={"de": "Eine Testrallye für Journey-Tests", "en": "A test rallye for journey tests"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    rallye_station_repo = RallyeStationRepository(db_session, org.id)
    rallye_station_challenge_repo = RallyeStationChallengeRepository(db_session, org.id)
    station_ids: list[UUID] = []
    rallye_station_ids: list[UUID] = []
    challenge_ids: list[list[UUID]] = []
    challenge_types: dict[UUID, ChallengeType] = {}

    # The default 3 stations × 2 challenges cover all five types, TEXT twice.
    all_challenge_types = [
        ChallengeType.TEXT,
        ChallengeType.PHOTO,
        ChallengeType.QR_SCAN,
        ChallengeType.QUIZ,
        ChallengeType.CREATIVE,
        ChallengeType.TEXT,
    ]
    max_points_sequence = [10, 20, 15, 5, 25, 10]

    challenge_counter = 0

    for station_idx in range(num_stations):
        # A library station, without a parent rallye_id, linked into the rallye.
        station = await station_repo.create(
            title={"de": f"Station {station_idx + 1}", "en": f"Station {station_idx + 1}"},
            description={
                "de": f"Beschreibung Station {station_idx + 1}",
                "en": f"Description Station {station_idx + 1}",
            },
            location_lat=48.3705 + station_idx * 0.001,
            location_lng=10.8978 + station_idx * 0.001,
        )
        rallye_station = await rallye_station_repo.create(
            rallye_id=rallye.id,
            station_id=station.id,
            sort_order=station_idx,
        )
        station_ids.append(station.id)
        rallye_station_ids.append(rallye_station.id)

        challenge_repo = ChallengeRepository(db_session, org.id)
        station_challenges: list[UUID] = []

        for challenge_idx in range(challenges_per_station):
            if challenge_type_override is not None:
                challenge_type = challenge_type_override
            else:
                challenge_type = all_challenge_types[challenge_counter % len(all_challenge_types)]
            max_points = max_points_sequence[challenge_counter % len(max_points_sequence)]

            # A library challenge, without a parent station_id, linked into the station.
            challenge = await challenge_repo.create(
                challenge_type=challenge_type,
                title={
                    "de": f"Aufgabe {challenge_idx + 1} ({challenge_type.value})",
                    "en": f"Challenge {challenge_idx + 1} ({challenge_type.value})",
                },
                instructions={
                    "de": f"Anleitung für {challenge_type.value}",
                    "en": f"Instructions for {challenge_type.value}",
                },
                scoring_rules={"max_points": max_points},
            )
            await rallye_station_challenge_repo.create(
                rallye_station_id=rallye_station.id,
                challenge_id=challenge.id,
                sort_order=challenge_idx,
            )

            station_challenges.append(challenge.id)
            challenge_types[challenge.id] = challenge_type
            challenge_counter += 1

        challenge_ids.append(station_challenges)

    await db_session.commit()

    return JourneyEnv(
        org=org,
        slug=org_slug,
        users=users,
        auth_headers=auth_headers,
        rallye_id=rallye.id,
        station_ids=station_ids,
        rallye_station_ids=rallye_station_ids,
        challenge_ids=challenge_ids,
        challenge_types=challenge_types,
    )


async def activate_run(
    client: AsyncClient,
    slug: str,
    run_id: UUID | str,
    headers: dict[str, str],
) -> dict[str, Any]:
    """Start a run with POST /api/orgs/{slug}/runs/{run_id}/start and return the JSON. Raises on an error status."""
    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/start",
        headers=headers,
    )
    response.raise_for_status()
    return cast(dict[str, Any], response.json())


async def create_team_and_join(
    client: AsyncClient,
    slug: str,
    run_id: UUID | str,
    headers: dict[str, str],
    team_name: str,
    color: str,
) -> dict[str, Any]:
    """
    Create a team, join it with the same headers, and return the team as read after the join.

    ``color`` is a hex string such as "#FF0000". Raises on an error status.
    """
    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": team_name, "color": color},
    )
    response.raise_for_status()
    team = cast(dict[str, Any], response.json())
    team_id = team["id"]

    join_response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/teams/{team_id}/join",
        headers=headers,
    )
    join_response.raise_for_status()

    # Only a read after the join carries the right member_count.
    updated_team_response = await client.get(
        f"/api/orgs/{slug}/runs/{run_id}/teams/{team_id}",
        headers=headers,
    )
    updated_team_response.raise_for_status()
    return cast(dict[str, Any], updated_team_response.json())


async def submit_challenge(
    client: AsyncClient,
    slug: str,
    run_id: UUID | str,
    headers: dict[str, str],
    run_challenge_id: UUID | str,
    content: dict[str, Any],
) -> dict[str, Any]:
    """
    Submit an answer with POST /api/orgs/{slug}/runs/{run_id}/submissions/ and return the JSON.

    ``run_challenge_id`` comes from the run's frozen snapshot, not from the template challenge.
    ``content`` depends on the challenge type. Raises on an error status.
    """
    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": str(run_challenge_id), "content": content},
    )
    response.raise_for_status()
    return cast(dict[str, Any], response.json())


# The acts that dissolve a team. Without the confirmation the API answers 409
# TEAM_WOULD_DISSOLVE and changes nothing. Each kind of act sends it here, in the
# form its endpoint reads: the query parameter on the two DELETE calls, the body
# field on the leave and on the move. A test of the refusal itself builds its own
# call and does not use these.
CONFIRM_DISSOLUTION_QUERY = {"confirm_dissolution": "true"}
CONFIRM_DISSOLUTION_BODY = {"confirm_dissolution": True}


async def remove_team(
    client: AsyncClient, slug: str, run_id: UUID | str, team_id: UUID | str, headers: dict[str, str]
) -> Response:
    """Remove a team as a whole, confirmed: DELETE /teams/{team_id}?confirm_dissolution=true."""
    return await client.delete(
        f"/api/orgs/{slug}/runs/{run_id}/teams/{team_id}", headers=headers, params=CONFIRM_DISSOLUTION_QUERY
    )


async def remove_last_member(
    client: AsyncClient,
    slug: str,
    run_id: UUID | str,
    team_id: UUID | str,
    member_id: UUID | str,
    headers: dict[str, str],
) -> Response:
    """Remove the last member of a team, confirmed: DELETE /teams/{team_id}/members/{member_id}?confirm_dissolution=true."""
    return await client.delete(
        f"/api/orgs/{slug}/runs/{run_id}/teams/{team_id}/members/{member_id}",
        headers=headers,
        params=CONFIRM_DISSOLUTION_QUERY,
    )


async def leave_as_last_member(
    client: AsyncClient, slug: str, run_id: UUID | str, team_id: UUID | str, headers: dict[str, str]
) -> Response:
    """Leave a team as its last member, confirmed: POST /teams/{team_id}/leave with confirm_dissolution in the body."""
    return await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/teams/{team_id}/leave", headers=headers, json=CONFIRM_DISSOLUTION_BODY
    )


async def join_as_guest(
    client: AsyncClient,
    join_code: str,
    *,
    display_name: str,
    team_name: str | None = None,
    team_code: str | None = None,
) -> dict[str, Any]:
    """
    Join a run as a guest, with a new team (``team_name``) or into a team (``team_code``).

    Wraps POST /api/join/{code}. The join leaves the guest's session cookie on the
    shared client, and a second join with that cookie would act for the same guest.
    The cookie is removed here. The returned JSON (token, user_id, team_id,
    team_share_code, ...) carries ``headers`` with the guest's token instead.
    """
    body: dict[str, Any]
    if team_code is not None:
        body = {"mode": "join", "display_name": display_name, "team_code": team_code}
    else:
        body = {"mode": "create", "display_name": display_name, "team_name": team_name}
    client.cookies.clear()
    response = await client.post(f"/api/join/{join_code}", json=body)
    response.raise_for_status()
    client.cookies.clear()
    joined = cast(dict[str, Any], response.json())
    joined["headers"] = {"Authorization": f"Bearer {joined['token']}"}
    return joined


async def read_run_numbers(
    client: AsyncClient,
    slug: str,
    run_id: UUID | str,
    staff_headers: dict[str, str],
    *,
    team_names: dict[str, str],
    member_headers: dict[str, str] | None = None,
    result_slug: str | None = None,
    compare_run_id: UUID | str | None = None,
) -> dict[str, Any]:
    """
    Read every number a client sees of the teams of a run, one entry per reader.

    The calls are the ones the run page, the scoreboard, the review, the
    statistics with their exports, the statistics of a period, the comparison
    of runs, the feed, the team chat, the run history of a member and the
    public result page make. Each reader is reduced to plain values keyed by
    team name, so two readings compare with ``==`` and a difference names its
    reader.

    Args:
        staff_headers: Auth headers of a coordinator or above
        team_names: Team id (str) to name, for the readers that carry the id only.
            A team id that is not in it shows as ``unknown:<id>``.
        member_headers: Auth headers of a member of a team; adds the readers ``leaderboard``
            and ``run_history`` and the member's view of the feed
        result_slug: Slug of a published result; adds the reader ``public_result``
        compare_run_id: A second run of the organization; adds the reader ``comparison``,
            which holds the numbers of this run only

    Returns:
        Dict with the readers grading_complete, team_list, scoreboard, progress, review, scores,
        statistics, statistics_export, period, feed, chat, result_link, and leaderboard,
        run_history, comparison and public_result when asked for.
    """
    base = f"/api/orgs/{slug}/runs/{run_id}"

    def name_of(team_id: Any) -> str:
        return team_names.get(str(team_id), f"unknown:{team_id}")

    def by_name(rows: list[tuple[Any, Any]]) -> dict[str, Any]:
        # Two teams of one name must both show: the second gets a mark, so a
        # dissolved team cannot hide behind a new team that took its name.
        keyed: dict[str, Any] = {}
        for name, values in rows:
            key = str(name)
            while key in keyed:
                key += " (again)"
            keyed[key] = values
        return keyed

    async def get(path: str, headers: dict[str, str], **params: Any) -> dict[str, Any]:
        response = await client.get(path, headers=headers, params=params or None)
        assert response.status_code == 200, f"GET {path}: {response.status_code} {response.text}"
        return cast(dict[str, Any], response.json())

    def board(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "entries": [
                (
                    e["rank"],
                    e["team_name"],
                    e["total_points"],
                    e["total_max_points"],
                    e["scored_count"],
                    e["is_winner"],
                )
                for e in data["entries"]
            ],
            "submission_count": data["submission_count"],
            "scored_count": data["scored_count"],
            "is_final": data["is_final"],
        }

    numbers: dict[str, Any] = {}

    # The run itself: whether every answer is graded. The run page reads it of an ended run,
    # and a run that has not ended always answers false.
    numbers["grading_complete"] = (await get(base, staff_headers))["grading_complete"]

    teams = await get(f"{base}/teams/", staff_headers)
    numbers["team_list"] = {
        "total": teams["total"],
        "teams": by_name(
            [(t["name"], (t["member_count"], sum(1 for m in t["members"] if m["is_guest"]))) for t in teams["items"]]
        ),
    }

    numbers["scoreboard"] = board(await get(f"{base}/scores/scoreboard", staff_headers))
    if member_headers is not None:
        numbers["leaderboard"] = board(await get(f"{base}/scores/scoreboard", member_headers))

    progress = await get(f"{base}/team-progress", staff_headers)
    numbers["progress"] = by_name(
        [
            (
                t["team_name"],
                (
                    t["member_count"],
                    t["stations_completed"],
                    t["stations_total"],
                    t["challenges_submitted"],
                    t["challenges_total"],
                    t["challenges_scored"],
                    t["total_points"],
                    t["total_max_points"],
                ),
            )
            for t in progress["teams"]
        ]
    )

    review = await get(f"{base}/submissions/", staff_headers, limit=1000)
    by_team: dict[str, tuple[int, int]] = {}
    for item in review["items"]:
        answers, points = by_team.get(name_of(item["team_id"]), (0, 0))
        by_team[name_of(item["team_id"])] = (answers + 1, points + (item["points"] or 0))
    numbers["review"] = {
        "total": review["total"],
        "listed": len(review["items"]),
        "ungraded": sum(1 for item in review["items"] if not item["is_scored"]),
        "by_team": by_team,
    }

    # The score list carries the answer only, so the review list above names the team.
    # A score whose answer the review does not list shows as ``not in the review``.
    team_of_answer = {item["id"]: name_of(item["team_id"]) for item in review["items"]}
    scores = await get(f"{base}/scores/", staff_headers, limit=1000)
    scores_by_team: dict[str, tuple[int, int]] = {}
    for item in scores["items"]:
        team = team_of_answer.get(item["submission_id"], "not in the review")
        graded, points = scores_by_team.get(team, (0, 0))
        scores_by_team[team] = (graded + 1, points + item["points"])
    numbers["scores"] = {"total": scores["total"], "listed": len(scores["items"]), "by_team": scores_by_team}

    statistics = await get(f"{base}/analytics", staff_headers)
    participation = statistics["participation"]
    working_time = statistics["working_time"]
    numbers["statistics"] = {
        "participation": (
            participation["team_count"],
            participation["participant_count"],
            participation["guest_count"],
            participation["registered_count"],
        ),
        "teams": by_name(
            [
                (
                    t["team_name"],
                    (
                        t["member_count"],
                        t["guest_count"],
                        t["total_points"],
                        t["challenges_attempted"],
                        t["challenges_scored"],
                    ),
                )
                for t in statistics["teams"]
            ]
        ),
        "stations": [(s["teams_attempted"], s["teams_scored"]) for s in statistics["stations"]],
        "challenges": [
            (
                c["submissions"],
                c["scored"],
                None if c["avg_points"] is None else round(c["avg_points"], 2),
                c["max_points"],
                c["time_team_count"],
            )
            for c in statistics["challenges"]
        ],
        "timeline_submissions": sum(bucket["submissions"] for bucket in statistics["timeline"]),
        "teams_measured": working_time["teams_measured"] if working_time else 0,
    }

    async def table(name: str) -> list[list[str]]:
        response = await client.get(
            f"{base}/analytics/export", headers={**staff_headers, "X-Clavis-Language": "en"}, params={"table": name}
        )
        assert response.status_code == 200, f"export {name}: {response.status_code} {response.text}"
        return list(csv.reader(io.StringIO(response.text.removeprefix("\ufeff"))))[1:]

    numbers["statistics_export"] = {
        "teams": by_name([(row[0], tuple(int(cell) for cell in row[1:6])) for row in await table("teams")]),
        "challenges": [
            (int(row[3]), int(row[4]), float(row[5]) if row[5] else None) for row in await table("challenges")
        ],
        "working_time": by_name([(row[0], (int(row[1]), int(row[2]))) for row in await table("working_time")]),
    }

    # The statistics of a period over all runs of the organization, in a range around today.
    today = datetime.now(UTC).date()
    period = await get(
        f"/api/orgs/{slug}/analytics/timeseries",
        staff_headers,
        date_from=(today - timedelta(days=2)).isoformat(),
        date_to=(today + timedelta(days=2)).isoformat(),
        bucket="day",
    )
    totals = period["totals"]
    numbers["period"] = {
        "totals": (
            totals["team_count"],
            totals["participant_count"],
            totals["guest_count"],
            totals["registered_count"],
            totals["submission_count"],
            totals["scored_count"],
            totals["unscored_count"],
            totals["total_points"],
            totals["teams_measured"],
        ),
        "buckets": (
            sum(b["team_count"] for b in period["buckets"]),
            sum(b["submission_count"] for b in period["buckets"]),
            sum(b["total_points"] for b in period["buckets"]),
        ),
        "teams": by_name(
            [
                (t["team_name"], (t["points"], t["answers"], t["scored"]))
                for t in period["teams"]
                if t["run_id"] == str(run_id)
            ]
        ),
        "tasks": sorted(
            (t["submissions"], t["scored"], None if t["avg_points"] is None else round(t["avg_points"], 2))
            for t in period["tasks"]
        ),
    }

    if compare_run_id is not None:
        compared = await client.get(
            f"/api/orgs/{slug}/analytics/compare",
            headers=staff_headers,
            params={"run_ids": [str(run_id), str(compare_run_id)]},
        )
        assert compared.status_code == 200, f"comparison: {compared.status_code} {compared.text}"
        comparison = compared.json()
        this_run = next(r for r in comparison["runs"] if r["run_id"] == str(run_id))
        numbers["comparison"] = {
            "run": (
                this_run["team_count"],
                this_run["participant_count"],
                this_run["guest_count"],
                this_run["registered_count"],
                this_run["submission_count"],
                this_run["scored_count"],
                this_run["teams_finished"],
                this_run["total_points"],
                this_run["avg_points_per_team"],
                this_run["working_time"]["teams_measured"],
            ),
            "stations": [
                (cell["team_count"], cell["teams_attempted"])
                for s in comparison["stations"]
                if (cell := s["cells"].get(str(run_id))) is not None
            ],
            "tasks": [
                (cell["team_count"], cell["submissions"], cell["scored"], cell["avg_points"])
                for t in comparison["tasks"]
                if (cell := t["cells"].get(str(run_id))) is not None
            ],
        }

    feed = await get(f"{base}/feed/", staff_headers, limit=1000)
    numbers["feed"] = {"total": feed["total"], "entries": [item["id"] for item in feed["items"]]}
    if member_headers is not None:
        member_feed = await get(f"{base}/feed/", member_headers, limit=1000)
        numbers["feed"]["member_entries"] = [item["id"] for item in member_feed["items"]]

    # The chat of every team the caller knows, as the Spielleitung reads it: the number of
    # messages, ``no team`` for a team that is not found, ``closed`` when no member asked for help.
    chat: list[tuple[Any, Any]] = []
    for team_id, name in team_names.items():
        response = await client.get(f"{base}/teams/{team_id}/chat/", headers=staff_headers, params={"limit": 500})
        if response.status_code == 200:
            chat.append((name, response.json()["total"]))
        else:
            assert response.status_code in (403, 404), f"chat of {name}: {response.status_code} {response.text}"
            chat.append((name, "no team" if response.status_code == 404 else "closed"))
    numbers["chat"] = by_name(chat)

    if member_headers is not None:
        history = await get("/api/me/run-history", member_headers)
        numbers["run_history"] = [
            (entry["team_name"], entry["placement"], entry["total_teams"], entry["total_points"])
            for entry in history["items"]
            if entry["run_id"] == str(run_id)
        ]

    link = await get(f"{base}/results/link", staff_headers)
    numbers["result_link"] = (link["status"], link["published_at"], link["expires_at"])

    if result_slug is not None:
        public = await client.post("/api/public/results/lookup", json={"slug": result_slug})
        assert public.status_code == 200, f"public result: {public.status_code} {public.text}"
        numbers["public_result"] = [
            (e["rank"], e["team_name"], e["points"], e["is_winner"]) for e in public.json()["entries"]
        ]

    return numbers
