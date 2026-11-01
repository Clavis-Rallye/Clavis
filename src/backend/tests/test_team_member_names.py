"""Integration tests for the member names on the run page.

The team list carries the display names of the members for the
organization's staff (coordinator+) only, oldest member first. A
participant's or a guest's request for the same list carries no names.

Live update: every join, leave and removal broadcasts `team_members_changed`
on the run channel (`run_state:{run_id}`), so the run page names a new member
at once. The payload carries the run and the team, never a name.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import Run, RunState
from app.models.team import Team
from app.models.team_member import TeamMember
from tests.journey_helpers import setup_journey_environment


async def _make_run(db_session: AsyncSession, org_id: UUID) -> Run:
    run = Run(
        organization_id=org_id,
        rallye_id=None,
        name=f"Names Run {uuid4().hex[:6]}",
        state=RunState.ACTIVE,
        join_code=uuid4().hex[:6].upper(),
        settings={"max_members_per_team": 10},
    )
    db_session.add(run)
    await db_session.flush()
    return run


async def _make_team(db_session: AsyncSession, org_id: UUID, run: Run, name: str) -> Team:
    team = Team(
        organization_id=org_id,
        run_id=run.id,
        name=name,
        color="#123456",
        share_code=uuid4().hex[:5].upper(),
    )
    db_session.add(team)
    await db_session.flush()
    return team


async def _add_member(db_session: AsyncSession, org_id: UUID, team: Team, name: str, joined_at: datetime) -> TeamMember:
    member = TeamMember(
        organization_id=org_id,
        team_id=team.id,
        user_id=None,
        display_name=name,
        is_guest=True,
        joined_at=joined_at,
    )
    db_session.add(member)
    await db_session.flush()
    return member


class BroadcastSpy:
    """Records ws_manager broadcasts instead of sending them."""

    def __init__(self) -> None:
        self.broadcasts: list[tuple[str, dict]] = []

    async def broadcast(self, channel: str, message: dict) -> None:
        self.broadcasts.append((channel, message))


@pytest.fixture
def broadcast_spy(monkeypatch: pytest.MonkeyPatch) -> BroadcastSpy:
    """Replace the ws_manager the notify helpers broadcast through."""
    import app.services.notify as notify_module

    spy = BroadcastSpy()
    monkeypatch.setattr(notify_module, "ws_manager", spy)
    return spy


def _member_events(spy: BroadcastSpy) -> list[tuple[str, dict]]:
    return [(c, m) for c, m in spy.broadcasts if m.get("type") == "team_members_changed"]


@pytest.mark.asyncio
async def test_team_list_names_members_for_staff_only(client: AsyncClient, db_session: AsyncSession):
    """Coordinator and org admin read the names, oldest first; a
    participant and a guest read the same list without any name."""
    env = await setup_journey_environment(db_session, client, org_slug="names-list")
    run = await _make_run(db_session, env.org.id)
    eight = await _make_team(db_session, env.org.id, run, "Die Lechpiraten")
    empty = await _make_team(db_session, env.org.id, run, "Noch leer")
    start = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)
    names = ["Samira", "Jonas", "Aylin", "Oleksandr", "Maria", "Anh", "Ahmad", "Emre"]
    # Inserted in reverse, so the order of the answer comes from joined_at.
    for offset, name in reversed(list(enumerate(names))):
        await _add_member(db_session, env.org.id, eight, name, start + timedelta(minutes=offset))
    await db_session.commit()

    url = f"/api/orgs/{env.slug}/runs/{run.id}/teams/"
    for role in ("coordinator", "org_admin"):
        response = await client.get(url, headers=env.auth_headers[role])
        assert response.status_code == 200
        teams = {t["name"]: t for t in response.json()["items"]}
        assert [m["display_name"] for m in teams["Die Lechpiraten"]["members"]] == names
        assert teams["Die Lechpiraten"]["member_count"] == 8
        assert teams["Noch leer"]["members"] == []

    response = await client.get(url, headers=env.auth_headers["participant1"])
    assert response.status_code == 200
    assert all(t["members"] is None for t in response.json()["items"])

    # A guest in a team of its own run: still no names, not even of its team.
    guest = await client.post(
        f"/api/join/{run.join_code}",
        json={"mode": "join", "display_name": "Zeynep", "team_code": empty.share_code},
    )
    assert guest.status_code == 201
    client.cookies.clear()
    response = await client.get(url, headers={"Authorization": f"Bearer {guest.json()['token']}"})
    assert response.status_code == 200
    body = response.text
    assert all(t["members"] is None for t in response.json()["items"])
    for name in names:
        assert name not in body


@pytest.mark.asyncio
async def test_guest_join_broadcasts_members_changed(
    client: AsyncClient, db_session: AsyncSession, broadcast_spy: BroadcastSpy
):
    """A guest who creates a team and a guest who joins it each send one
    event with the run and the team, and no name."""
    env = await setup_journey_environment(db_session, client, org_slug="names-guest-join")
    run = await _make_run(db_session, env.org.id)
    await db_session.commit()

    created = await client.post(
        f"/api/join/{run.join_code}",
        json={"mode": "create", "display_name": "Samira", "team_name": "Fuggerei-Füchse"},
    )
    assert created.status_code == 201
    team_id = created.json()["team_id"]
    client.cookies.clear()
    joined = await client.post(
        f"/api/join/{run.join_code}",
        json={"mode": "join", "display_name": "Lea", "team_code": created.json()["team_share_code"]},
    )
    assert joined.status_code == 201

    events = _member_events(broadcast_spy)
    assert len(events) == 2
    for channel, message in events:
        assert channel == f"run_state:{run.id}"
        # Exact equality: the payload names no person.
        assert message["data"] == {"run_id": str(run.id), "team_id": team_id}


@pytest.mark.asyncio
async def test_participant_join_leave_and_removal_broadcast_members_changed(
    client: AsyncClient, db_session: AsyncSession, broadcast_spy: BroadcastSpy
):
    """Join by code, join by id, leave and a removal by the Spielleitung each
    send one event for the team they change."""
    env = await setup_journey_environment(db_session, client, org_slug="names-participant")
    run = await _make_run(db_session, env.org.id)
    team = await _make_team(db_session, env.org.id, run, "Team Rathaus")
    await db_session.commit()
    base = f"/api/orgs/{env.slug}/runs/{run.id}/teams"
    expected = {"run_id": str(run.id), "team_id": str(team.id)}

    by_code = await client.post(
        f"{base}/by-code", headers=env.auth_headers["participant1"], json={"team_code": team.share_code}
    )
    assert by_code.status_code == 204
    by_id = await client.post(f"{base}/{team.id}/join", headers=env.auth_headers["participant2"])
    assert by_id.status_code == 204
    assert [m["data"] for _, m in _member_events(broadcast_spy)] == [expected, expected]

    left = await client.post(f"{base}/{team.id}/leave", headers=env.auth_headers["participant2"])
    assert left.status_code == 204
    assert len(_member_events(broadcast_spy)) == 3

    detail = await client.get(f"{base}/{team.id}", headers=env.auth_headers["coordinator"])
    member_id = detail.json()["members"][0]["id"]
    removed = await client.delete(
        f"{base}/{team.id}/members/{member_id}",
        headers=env.auth_headers["coordinator"],
        params={"confirm_dissolution": "true"},
    )
    assert removed.status_code == 204
    events = _member_events(broadcast_spy)
    assert len(events) == 4
    assert all(channel == f"run_state:{run.id}" and m["data"] == expected for channel, m in events)
