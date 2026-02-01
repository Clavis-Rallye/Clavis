"""``POST /api/join/{code}``: how anonymous guests join runs, and how the join by code is limited and locked."""

import asyncio
from contextlib import asynccontextmanager

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import get_settings
from app.main import app
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User, UserRole
from app.repositories.team import TeamRepository
from app.services import team_code_rate_limit
from app.services.team_dissolution import dissolve_team
from tests.journey_helpers import activate_run, setup_journey_environment


@pytest.mark.asyncio
async def test_guest_join_with_valid_code(client: AsyncClient, db_session: AsyncSession) -> None:
    """A guest joins an active run with its join code and gets a token, a team and the run id."""
    env = await setup_journey_environment(db_session, client, org_slug="guest-join-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Guest Test Run"},
    )
    assert create_response.status_code == 201
    run_data = create_response.json()
    run_id = run_data["id"]
    join_code = run_data["join_code"]

    await activate_run(client, env.slug, run_id, coord_headers)

    join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Test Guest", "team_name": "Guest Team"},
    )
    assert join_response.status_code == 201
    join_data = join_response.json()

    assert "token" in join_data
    assert "user_id" in join_data
    assert "display_name" in join_data
    assert "team_id" in join_data
    assert "team_name" in join_data
    assert "run_id" in join_data

    assert join_data["display_name"] == "Test Guest"
    assert join_data["team_name"] == "Guest Team"
    assert join_data["run_id"] == run_id


@pytest.mark.asyncio
async def test_guest_join_generates_valid_jwt(client: AsyncClient, db_session: AsyncSession) -> None:
    """The token of a guest carries the role guest and the organization of the run."""
    env = await setup_journey_environment(db_session, client, org_slug="guest-jwt-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Guest JWT Test Run"},
    )
    assert create_response.status_code == 201
    run_data = create_response.json()
    join_code = run_data["join_code"]
    await activate_run(client, env.slug, run_data["id"], coord_headers)

    join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "JWT Test Guest"},
    )
    assert join_response.status_code == 201
    token = join_response.json()["token"]

    settings = get_settings()
    payload = jwt.decode(token, settings.auth.jwt_secret_key, algorithms=["HS256"])

    assert payload["role"] == "guest"
    assert "sub" in payload  # user_id
    assert "org_id" in payload
    assert str(env.org.id) == payload["org_id"]


@pytest.mark.asyncio
async def test_guest_join_with_invalid_code(client: AsyncClient, db_session: AsyncSession) -> None:
    """An unknown join code answers 404."""
    join_response = await client.post(
        "/api/join/XXXYYY",
        json={"mode": "create", "display_name": "Ghost Guest"},
    )
    assert join_response.status_code == 404
    assert "not found" in join_response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_guest_join_with_inactive_run(client: AsyncClient, db_session: AsyncSession) -> None:
    """A run that was not started refuses the join with RUN_NOT_STARTED."""
    env = await setup_journey_environment(db_session, client, org_slug="guest-inactive-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Inactive Test Run"},
    )
    assert create_response.status_code == 201
    join_code = create_response.json()["join_code"]

    join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Eager Guest"},
    )
    assert join_response.status_code == 422
    assert join_response.json()["error_code"] == "RUN_NOT_STARTED"


@pytest.mark.asyncio
async def test_guest_join_with_ended_run(client: AsyncClient, db_session: AsyncSession) -> None:
    """An ended run refuses the join with RUN_ENDED."""
    env = await setup_journey_environment(db_session, client, org_slug="guest-ended-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Ended Test Run"},
    )
    run_id = create_response.json()["id"]
    join_code = create_response.json()["join_code"]
    await activate_run(client, env.slug, run_id, coord_headers)

    end_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/end",
        headers=coord_headers,
    )
    assert end_response.status_code == 200

    join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Late Guest"},
    )
    assert join_response.status_code == 422
    assert join_response.json()["error_code"] == "RUN_ENDED"


@pytest.mark.asyncio
async def test_guest_join_creates_guest_user(client: AsyncClient, db_session: AsyncSession) -> None:
    """A join creates a guest user without email and password, in the organization of the run."""
    env = await setup_journey_environment(db_session, client, org_slug="guest-user-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "User Test Run"},
    )
    run_id = create_response.json()["id"]
    join_code = create_response.json()["join_code"]
    await activate_run(client, env.slug, run_id, coord_headers)

    join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "DB Test Guest"},
    )
    assert join_response.status_code == 201
    user_id = join_response.json()["user_id"]

    result = await db_session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    assert user is not None
    assert user.role == UserRole.GUEST
    assert user.email is None
    assert user.display_name == "DB Test Guest"
    assert user.password_hash == ""
    assert user.organization_id == env.org.id


@pytest.mark.asyncio
async def test_guest_join_creates_team_and_member(client: AsyncClient, db_session: AsyncSession) -> None:
    """A join in create mode creates the team with a color, and the guest as its member."""
    env = await setup_journey_environment(db_session, client, org_slug="guest-team-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Team Test Run"},
    )
    run_id = create_response.json()["id"]
    join_code = create_response.json()["join_code"]
    await activate_run(client, env.slug, run_id, coord_headers)

    join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Team Test Guest", "team_name": "Awesome Team"},
    )
    assert join_response.status_code == 201
    data = join_response.json()
    team_id = data["team_id"]
    user_id = data["user_id"]

    result = await db_session.execute(select(Team).where(Team.id == team_id))
    team = result.scalar_one_or_none()

    assert team is not None
    assert team.name == "Awesome Team"
    assert str(team.run_id) == run_id
    assert team.color is not None

    result = await db_session.execute(select(TeamMember).where(TeamMember.team_id == team_id))
    member = result.scalar_one_or_none()

    assert member is not None
    assert str(member.user_id) == user_id
    assert member.is_guest is True
    assert member.display_name == "Team Test Guest"


@pytest.mark.asyncio
async def test_guest_join_auto_generates_names(client: AsyncClient, db_session: AsyncSession) -> None:
    """Without names the guest gets a generated display name, and the team stays unnamed."""
    env = await setup_journey_environment(db_session, client, org_slug="guest-auto-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Auto Name Test Run"},
    )
    join_code = create_response.json()["join_code"]
    await activate_run(client, env.slug, create_response.json()["id"], coord_headers)

    join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create"},
    )
    assert join_response.status_code == 201
    data = join_response.json()

    # The team has no name until the guest chooses one.
    assert data["display_name"].startswith("Guest")
    assert data["team_name"] is None


@pytest.mark.asyncio
async def test_guest_join_case_insensitive_code(client: AsyncClient, db_session: AsyncSession) -> None:
    """A join code in lowercase still joins."""
    env = await setup_journey_environment(db_session, client, org_slug="guest-case-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Case Test Run"},
    )
    join_code = create_response.json()["join_code"]
    await activate_run(client, env.slug, create_response.json()["id"], coord_headers)

    join_response = await client.post(
        f"/api/join/{join_code.lower()}",
        json={"mode": "create", "display_name": "Lowercase Guest"},
    )
    assert join_response.status_code == 201


@pytest.mark.asyncio
async def test_guest_join_respects_max_teams(client: AsyncClient, db_session: AsyncSession) -> None:
    """A guest who would create a team beyond max_teams gets TEAM_LIMIT_REACHED."""
    env = await setup_journey_environment(db_session, client, org_slug="guest-max-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={
            "rallye_id": str(env.rallye_id),
            "name": "Max Teams Test Run",
            "max_teams": 1,
        },
    )
    join_code = create_response.json()["join_code"]
    await activate_run(client, env.slug, create_response.json()["id"], coord_headers)

    join_response1 = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "First Guest", "team_name": "Team 1"},
    )
    assert join_response1.status_code == 201

    # Clear cookies so the second request is a fresh guest. The same guest would get
    # the team of the first request back, because a repeated join is idempotent.
    client.cookies.clear()

    join_response2 = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Second Guest", "team_name": "Team 2"},
    )
    assert join_response2.status_code == 422
    assert join_response2.json()["error_code"] == "TEAM_LIMIT_REACHED"


@pytest.mark.asyncio
async def test_guest_join_can_join_existing_team(client: AsyncClient, db_session: AsyncSession) -> None:
    """A guest with the share code of a team joins it, although max_teams is reached."""
    env = await setup_journey_environment(db_session, client, org_slug="guest-existing-org")
    coord_headers = env.auth_headers["coordinator"]

    # max_teams=1 forces the second guest to join the existing team.
    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={
            "rallye_id": str(env.rallye_id),
            "name": "Existing Team Test Run",
            "max_teams": 1,
        },
    )
    join_code = create_response.json()["join_code"]
    run_id = create_response.json()["id"]
    await activate_run(client, env.slug, run_id, coord_headers)

    join_response1 = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "First Guest", "team_name": "Original Team"},
    )
    assert join_response1.status_code == 201
    team_id = join_response1.json()["team_id"]
    team_share_code = join_response1.json()["team_share_code"]

    join_response2 = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "join", "display_name": "Second Guest", "team_code": team_share_code},
    )
    assert join_response2.status_code == 201

    assert join_response2.json()["team_id"] == team_id
    assert join_response2.json()["team_name"] == "Original Team"


@pytest.mark.asyncio
async def test_guest_join_create_with_taken_team_name_409(client: AsyncClient, db_session: AsyncSession) -> None:
    """A guest cannot create a team whose name the run already holds, the comparison ignores case and padding."""
    env = await setup_journey_environment(db_session, client, org_slug="guest-name-taken")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Guest Name Clash Run"},
    )
    assert create_response.status_code == 201
    run_data = create_response.json()
    join_code = run_data["join_code"]
    await activate_run(client, env.slug, run_data["id"], coord_headers)

    first = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Erste", "team_name": "Schnelle Füchse"},
    )
    assert first.status_code == 201, first.text

    # Clear cookies so the second request is a fresh guest, not the first one
    # replaying their create.
    client.cookies.clear()

    second = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Zweite", "team_name": "  schnelle füchse  "},
    )
    assert second.status_code == 409, second.text
    assert second.json()["error_code"] == "TEAM_NAME_TAKEN"


@pytest.mark.asyncio
async def test_guest_join_by_code_full_team_error_code(client: AsyncClient, db_session: AsyncSession) -> None:
    """Joining a full team by its share code answers TEAM_FULL."""
    env = await setup_journey_environment(db_session, client, org_slug="guest-team-full")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={
            "rallye_id": str(env.rallye_id),
            "name": "Full Team Run",
            "settings": {"max_members_per_team": 1},
        },
    )
    assert create_response.status_code == 201
    run_data = create_response.json()
    join_code = run_data["join_code"]
    await activate_run(client, env.slug, run_data["id"], coord_headers)

    first = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Erste", "team_name": "Volles Team"},
    )
    assert first.status_code == 201, first.text
    team_share_code = first.json()["team_share_code"]

    # A fresh guest. The team already holds its one allowed member.
    client.cookies.clear()

    second = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "join", "display_name": "Zweite", "team_code": team_share_code},
    )
    assert second.status_code == 422, second.text
    assert second.json()["error_code"] == "TEAM_FULL"


@pytest.mark.asyncio
async def test_guest_join_by_code_already_in_other_team_error_code(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Joining a second team in the same run answers ALREADY_IN_OTHER_TEAM."""
    env = await setup_journey_environment(db_session, client, org_slug="guest-already-in-team")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Two Teams Run"},
    )
    assert create_response.status_code == 201
    run_data = create_response.json()
    join_code = run_data["join_code"]
    await activate_run(client, env.slug, run_data["id"], coord_headers)

    # Keep the cookie, so the next request is the same guest.
    first = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Erste", "team_name": "Team Eins"},
    )
    assert first.status_code == 201, first.text

    client.cookies.clear()
    second_guest = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Zweite", "team_name": "Team Zwei"},
    )
    assert second_guest.status_code == 201, second_guest.text
    team_2_share_code = second_guest.json()["team_share_code"]

    # Sign back in as the first guest by restoring its cookie.
    client.cookies.clear()
    client.cookies.update({"access_token": first.cookies.get("access_token")})

    third = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "join", "display_name": "Erste", "team_code": team_2_share_code},
    )
    assert third.status_code == 422, third.text
    assert third.json()["error_code"] == "ALREADY_IN_OTHER_TEAM"


@pytest.mark.asyncio
async def test_guest_join_by_code_blocked_client_gets_429_for_correct_code(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The limiter is asked before the code is looked up.

    Once the failure budget is spent, a correct code is answered with 429 too
    and the guest joins no team, until the block ends.
    """
    from app.services import team_code_rate_limit

    env = await setup_journey_environment(db_session, client, org_slug="guest-blocked-org")
    coord_headers = env.auth_headers["coordinator"]
    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Blocked Client Run", "max_teams": 3},
    )
    join_code = create_response.json()["join_code"]
    run_id = create_response.json()["id"]
    await activate_run(client, env.slug, run_id, coord_headers)

    first = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Owner", "team_name": "Blocked Team"},
    )
    assert first.status_code == 201
    team_id = first.json()["team_id"]
    share_code = first.json()["team_share_code"]
    client.cookies.clear()

    team_code_rate_limit._failures.clear()
    try:
        for _ in range(team_code_rate_limit.MAX_FAILURES):
            wrong = await client.post(
                f"/api/join/{join_code}",
                json={"mode": "join", "display_name": "Guesser", "team_code": "ZZZZZZ"},
            )
            assert wrong.status_code == 404

        blocked = await client.post(
            f"/api/join/{join_code}",
            json={"mode": "join", "display_name": "Guesser", "team_code": share_code},
        )
        assert blocked.status_code == 429
        assert blocked.headers.get("Retry-After") == "60"

        members = (await db_session.execute(select(TeamMember).where(TeamMember.team_id == team_id))).scalars().all()
        assert len(members) == 1
    finally:
        team_code_rate_limit._failures.clear()


# --- The join by code admits one request per client and run at a time ---


async def _run_with_team(concurrent_client: AsyncClient, db_session: AsyncSession, slug: str) -> tuple[str, str, str]:
    """(join_code, run_id, share_code) of an active run with one team."""
    env = await setup_journey_environment(db_session, concurrent_client, org_slug=slug)
    headers = env.auth_headers["coordinator"]
    await db_session.commit()
    created = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(env.rallye_id), "name": "Admission Run"},
    )
    assert created.status_code == 201
    run = created.json()
    await activate_run(concurrent_client, env.slug, run["id"], headers)
    first = await concurrent_client.post(
        f"/api/join/{run['join_code']}",
        json={"mode": "create", "display_name": "Owner", "team_name": "Admission Team"},
    )
    assert first.status_code == 201, first.text
    concurrent_client.cookies.clear()
    return run["join_code"], run["id"], first.json()["team_share_code"]


SIGNAL_TIMEOUT = 10.0  # a missing arrival must fail the test, never pass it


def _client_from(address: str) -> AsyncClient:
    """A client on the App of ``concurrent_client`` whose connection comes from ``address``, the peer."""
    return AsyncClient(transport=ASGITransport(app=app, client=(address, 123)), base_url="http://testserver")


class _AdmissionProbe:
    """Wraps ``team_code_rate_limit.admission`` and acknowledges every request that reaches it.

    ``arrived`` counts requests that called the admission, ``entered`` those that
    got through it. ``arrived > entered`` means requests are waiting at the admission.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.arrived = 0
        self.entered = 0
        self._events: dict[int, asyncio.Event] = {}
        original = team_code_rate_limit.admission

        @asynccontextmanager
        async def probe(client_ip, run_id):  # noqa: ANN001, ANN202
            self.arrived += 1
            for count, event in self._events.items():
                if self.arrived >= count:
                    event.set()
            async with original(client_ip, run_id) as slot:
                self.entered += 1
                yield slot

        monkeypatch.setattr(team_code_rate_limit, "admission", probe)

    async def reached(self, count: int) -> None:
        """Wait until ``count`` requests have arrived at the admission; fail after the timeout."""
        event = self._events.setdefault(count, asyncio.Event())
        if self.arrived >= count:
            event.set()
        await asyncio.wait_for(event.wait(), SIGNAL_TIMEOUT)


@pytest.mark.asyncio
async def test_concurrent_wrong_codes_get_at_most_the_budget_of_lookups(
    db_session: AsyncSession, concurrent_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A burst of wrong codes causes at most MAX_FAILURES lookups and the rest get 429.

    A lookup is held until every request of the burst has reached the admission.
    So the burst overlaps whatever the database does.
    """
    join_code, _, _ = await _run_with_team(concurrent_client, db_session, "guest-admission-burst")
    team_code_rate_limit._failures.clear()
    burst = team_code_rate_limit.MAX_FAILURES * 3
    probe = _AdmissionProbe(monkeypatch)
    lookups = 0
    release = asyncio.Event()
    original = TeamRepository.get_by_share_code

    async def counting(self: TeamRepository, run_id, code):  # noqa: ANN001, ANN202
        nonlocal lookups
        lookups += 1
        await asyncio.wait_for(release.wait(), SIGNAL_TIMEOUT)
        return await original(self, run_id, code)

    monkeypatch.setattr(TeamRepository, "get_by_share_code", counting)
    peer = _client_from("203.0.113.50")
    tasks = [
        asyncio.create_task(
            peer.post(
                f"/api/join/{join_code}",
                json={"mode": "join", "display_name": f"G{i}", "team_code": f"ZZZZ{i:02d}"},
            )
        )
        for i in range(burst)
    ]
    try:
        await probe.reached(burst)
        # Every request is at the admission. Only one may be through it.
        assert probe.entered == 1, f"{probe.entered} of {burst} requests got through the admission at once"
        release.set()
        responses = await asyncio.gather(*tasks)
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await peer.aclose()
        team_code_rate_limit._failures.clear()

    statuses = sorted(r.status_code for r in responses)
    assert lookups <= team_code_rate_limit.MAX_FAILURES, f"{lookups} lookups for a budget of 5"
    assert statuses.count(404) == team_code_rate_limit.MAX_FAILURES
    assert statuses.count(429) == burst - team_code_rate_limit.MAX_FAILURES


@pytest.mark.asyncio
async def test_correct_code_held_in_its_lookup_does_not_let_a_fifth_failure_land_meanwhile(
    db_session: AsyncSession, concurrent_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With four failures on the books, a held correct code and a fifth failure run one after the other.

    The fifth failure must not land while the correct request waits in its
    lookup. Otherwise the correct request would clear the block that the fifth
    failure had set.
    """
    join_code, run_id, share_code = await _run_with_team(concurrent_client, db_session, "guest-admission-held")
    from uuid import UUID

    team_code_rate_limit._failures.clear()
    ip = "203.0.113.51"
    key = (ip, UUID(run_id))
    for _ in range(team_code_rate_limit.MAX_FAILURES - 1):
        await team_code_rate_limit.check_and_record_failure(ip, UUID(run_id))

    probe = _AdmissionProbe(monkeypatch)
    held = asyncio.Event()
    release = asyncio.Event()
    wrong_looked_up = asyncio.Event()
    original = TeamRepository.get_by_share_code

    async def gated(self: TeamRepository, run_uuid, code):  # noqa: ANN001, ANN202
        if code == share_code:
            held.set()
            await asyncio.wait_for(release.wait(), SIGNAL_TIMEOUT)
        else:
            wrong_looked_up.set()
        return await original(self, run_uuid, code)

    monkeypatch.setattr(TeamRepository, "get_by_share_code", gated)
    peer = _client_from(ip)

    def post(code: str):  # noqa: ANN202
        return asyncio.create_task(
            peer.post(
                f"/api/join/{join_code}",
                json={"mode": "join", "display_name": "Gast", "team_code": code},
            )
        )

    tasks: list[asyncio.Task] = []
    try:
        correct = post(share_code)
        tasks.append(correct)
        await asyncio.wait_for(held.wait(), SIGNAL_TIMEOUT)
        wrong = post("ZZZZZZ")
        tasks.append(wrong)
        # The competing request has reached the admission ...
        await probe.reached(2)
        # ... and waits there: it is not through, and it has not looked anything up.
        assert probe.entered == 1, "the competing request got through the admission while the correct code was held"
        assert not wrong_looked_up.is_set(), "the fifth failure's lookup ran while the correct code was held"
        assert len(team_code_rate_limit._failures[key]) == team_code_rate_limit.MAX_FAILURES - 1
        release.set()
        correct_response, wrong_response = await asyncio.gather(correct, wrong)
        # The reset of the correct request came first, then the failure: no block survives, one failure is on the books.
        assert len(team_code_rate_limit._failures[key]) == 1
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await peer.aclose()
        team_code_rate_limit._failures.clear()

    assert correct_response.status_code == 201, correct_response.text
    assert wrong_response.status_code == 404


@pytest.mark.asyncio
async def test_join_by_code_of_a_team_that_dissolves_after_the_admission_gets_unknown_code(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A join whose team dissolves after its code resolved gets the answer for an unknown code and writes nothing.

    The dissolution holds the team row and has not committed. The join looks the
    code up inside the admission, leaves the admission and waits for the team
    row. While it waits, a second request of the same client gets through the
    admission. The lock in memory is not held across the wait for the row.
    """
    join_code, run_id, share_code = await _run_with_team(concurrent_client, db_session, "guest-admission-dissolved")
    team_code_rate_limit._failures.clear()
    from uuid import UUID

    ip = "203.0.113.52"
    key = (ip, UUID(run_id))
    probe = _AdmissionProbe(monkeypatch)
    resolved = asyncio.Event()
    original = TeamRepository.get_by_share_code

    async def lookup(self: TeamRepository, run_uuid, code):  # noqa: ANN001, ANN202
        team = await original(self, run_uuid, code)
        if code == share_code:
            assert team is not None, "the dissolution is not committed, the code must still resolve"
            resolved.set()
        return team

    monkeypatch.setattr(TeamRepository, "get_by_share_code", lookup)
    peer = _client_from(ip)

    def post(code: str, name: str):  # noqa: ANN202
        return asyncio.create_task(
            peer.post(
                f"/api/join/{join_code}",
                json={"mode": "join", "display_name": name, "team_code": code},
            )
        )

    async def join_waits_for_a_row_lock() -> None:
        waiting = text(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
        )
        async with test_engine.connect() as connection:
            while (await connection.execute(waiting)).scalar_one() == 0:
                assert not join.done(), f"the join did not wait for the team row: {join.result().text}"
                await asyncio.sleep(0.01)

    sessions = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    tasks: list[asyncio.Task] = []
    try:
        async with sessions() as dissolving:
            team = (await dissolving.execute(select(Team).where(Team.share_code == share_code))).scalar_one()
            team_id = team.id
            await dissolve_team(dissolving, team)

            join = post(share_code, "Zu spaet")
            tasks.append(join)
            await asyncio.wait_for(resolved.wait(), SIGNAL_TIMEOUT)
            await asyncio.wait_for(join_waits_for_a_row_lock(), SIGNAL_TIMEOUT)
            assert not join.done()

            # The join waits in the database. Its admission is closed. The next
            # request of this client is admitted and answered meanwhile.
            wrong = post("ZZZZZZ", "Falsch")
            tasks.append(wrong)
            wrong_response = await asyncio.wait_for(wrong, SIGNAL_TIMEOUT)
            assert wrong_response.status_code == 404
            assert probe.entered == 2
            assert not join.done()

            await dissolving.commit()
        join_response = await asyncio.wait_for(join, SIGNAL_TIMEOUT)
        failures = len(team_code_rate_limit._failures.get(key, ()))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await peer.aclose()
        team_code_rate_limit._failures.clear()

    assert join_response.status_code == 404, join_response.text
    assert join_response.json() == {"detail": "Team-Code unbekannt."}
    # The code did resolve, so the dissolved team counts no failure: only the wrong code is on the books.
    assert failures == 1
    members = (
        await db_session.execute(select(func.count(TeamMember.id)).where(TeamMember.team_id == team_id))
    ).scalar_one()
    late_guests = (
        await db_session.execute(select(func.count(User.id)).where(User.display_name == "Zu spaet"))
    ).scalar_one()
    assert (members, late_guests) == (0, 0)


# --- The guest entry takes the run row before the account ---------------


@pytest.mark.asyncio
async def test_guest_create_under_a_team_limit_waits_for_the_run_row_without_the_account_row(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: AsyncEngine,
) -> None:
    """The lock order is run, account, team (``services/team_dissolution``).

    A transaction holds the run row. A signed-in guest without a membership in
    that run asks for a new team. The request waits for the run row, and while
    it waits it holds no lock on the row of its account: ``create_team`` takes
    the same two rows as run then account, so an entry that took the account
    first could wait in a ring with it.
    """
    from uuid import UUID

    env = await setup_journey_environment(db_session, concurrent_client, org_slug="guest-run-row-first")
    headers = env.auth_headers["coordinator"]
    await db_session.commit()

    async def active_run(name: str, **settings: int) -> dict:
        created = await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/",
            headers=headers,
            json={"rallye_id": str(env.rallye_id), "name": name, **settings},
        )
        assert created.status_code == 201, created.text
        await activate_run(concurrent_client, env.slug, created.json()["id"], headers)
        return created.json()

    # The session comes from another run of the organization, so the guest is
    # signed in and has no membership in the run with the limit.
    elsewhere = await active_run("Elsewhere")
    limited = await active_run("Limited", max_teams=2)
    signed_in = await concurrent_client.post(
        f"/api/join/{elsewhere['join_code']}",
        json={"mode": "create", "display_name": "Gast", "team_name": "Anderswo"},
    )
    assert signed_in.status_code == 201, signed_in.text
    guest_id = UUID(signed_in.json()["user_id"])
    concurrent_client.cookies.clear()
    concurrent_client.cookies.update({"access_token": signed_in.cookies.get("access_token")})

    waits = text(
        """
        SELECT pg_blocking_pids(a.pid) AS blocked_by,
               (SELECT min(l.relation::regclass::text) FROM pg_locks l
                 WHERE l.pid = a.pid AND l.locktype = 'tuple') AS waits_in
          FROM pg_stat_activity a
         WHERE a.datname = current_database() AND a.wait_event_type = 'Lock'
        """
    )

    async def lock_waits() -> list:
        async with test_engine.connect() as connection:
            while True:
                rows = (await connection.execute(waits)).all()
                if rows:
                    return list(rows)
                assert not join.done(), f"the entry did not wait for the run row: {join.result().text}"
                await asyncio.sleep(0.01)

    sessions = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    join: asyncio.Task = asyncio.create_task(asyncio.sleep(0))
    try:
        async with sessions() as holder:
            holder_pid = (await holder.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            await holder.execute(
                text("SELECT id FROM runs WHERE id = :id FOR NO KEY UPDATE"), {"id": UUID(limited["id"])}
            )

            join = asyncio.create_task(
                concurrent_client.post(
                    f"/api/join/{limited['join_code']}",
                    json={"mode": "create", "display_name": "Gast", "team_name": "Neu"},
                )
            )
            waiting = await asyncio.wait_for(lock_waits(), SIGNAL_TIMEOUT)
            assert [(tuple(row.blocked_by), row.waits_in) for row in waiting] == [((holder_pid,), "runs")]

            # The account row is free while the entry waits: a third
            # transaction gets its lock at once.
            async with sessions() as other:
                locked = await other.execute(
                    text("SELECT id FROM users WHERE id = :id FOR NO KEY UPDATE SKIP LOCKED"), {"id": guest_id}
                )
                account_row_free = locked.first() is not None
                await other.rollback()
            assert account_row_free, "the entry holds its account row while it waits for the run row"
            assert not join.done()

            await holder.commit()
        response = await asyncio.wait_for(join, SIGNAL_TIMEOUT)
    finally:
        join.cancel()
        await asyncio.gather(join, return_exceptions=True)

    assert response.status_code == 201, response.text
    assert response.json()["user_id"] == str(guest_id)
    assert response.json()["team_name"] == "Neu"


@pytest.mark.asyncio
async def test_guest_entry_that_needs_no_run_row_does_not_wait_for_it(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: AsyncEngine,
) -> None:
    """Without a team limit no mode asks for the run row: both answer while another transaction holds it."""
    from uuid import UUID

    join_code, run_id, share_code = await _run_with_team(concurrent_client, db_session, "guest-no-run-row")
    team_code_rate_limit._failures.clear()
    sessions = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async with sessions() as holder:
        await holder.execute(text("SELECT id FROM runs WHERE id = :id FOR NO KEY UPDATE"), {"id": UUID(run_id)})
        joined = await asyncio.wait_for(
            concurrent_client.post(
                f"/api/join/{join_code}",
                json={"mode": "join", "display_name": "Mit Code", "team_code": share_code},
            ),
            SIGNAL_TIMEOUT,
        )
        concurrent_client.cookies.clear()
        created = await asyncio.wait_for(
            concurrent_client.post(
                f"/api/join/{join_code}",
                json={"mode": "create", "display_name": "Ohne Limit", "team_name": "Zweites Team"},
            ),
            SIGNAL_TIMEOUT,
        )
        await holder.rollback()

    assert joined.status_code == 201, joined.text
    assert created.status_code == 201, created.text
