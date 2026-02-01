"""Integration tests for rate limiting.

Covers the chat, feed, login and register limits, and the format of the 429 response.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_test_organization, create_test_user
from tests.journey_helpers import activate_run, create_team_and_join, setup_journey_environment


@pytest.mark.asyncio
async def test_chat_rate_limit_30_per_minute(client: AsyncClient, db_session: AsyncSession):
    """The 31st chat message within a minute returns 429."""
    env = await setup_journey_environment(db_session, client, org_slug="chat-limit-org")

    run_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={
            "name": "Chat Limit Test Run",
            "rallye_id": str(env.rallye_id),
            "max_teams": 5,
            "start_time": None,
            "end_time": None,
        },
    )
    assert run_resp.status_code == 201
    run_id = run_resp.json()["id"]

    await activate_run(client, env.slug, run_id, env.auth_headers["coordinator"])

    team_data = await create_team_and_join(
        client,
        env.slug,
        run_id,
        env.auth_headers["participant1"],
        "Test Team",
        "#FF0000",
    )
    team_id = team_data["id"]

    for i in range(30):
        resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
            headers=env.auth_headers["participant1"],
            json={"content": f"Message {i + 1}"},
        )
        assert resp.status_code == 201, f"Message {i + 1} failed with {resp.status_code}"

    resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=env.auth_headers["participant1"],
        json={"content": "Message 31 - should be rate limited"},
    )
    assert resp.status_code == 429
    data = resp.json()
    assert "error" in data or "detail" in data


@pytest.mark.asyncio
async def test_feed_rate_limit_10_per_minute(client: AsyncClient, db_session: AsyncSession):
    """The 11th feed event within a minute returns 429."""
    env = await setup_journey_environment(db_session, client, org_slug="feed-limit-org")

    run_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={
            "name": "Feed Limit Test Run",
            "rallye_id": str(env.rallye_id),
            "max_teams": 5,
            "start_time": None,
            "end_time": None,
        },
    )
    assert run_resp.status_code == 201
    run_id = run_resp.json()["id"]

    await activate_run(client, env.slug, run_id, env.auth_headers["coordinator"])

    for i in range(10):
        resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            headers=env.auth_headers["coordinator"],
            json={
                "event_type": "announcement",
                "content": {"de": f"Ankündigung {i + 1}", "en": f"Announcement {i + 1}"},
            },
        )
        assert resp.status_code == 201, f"Event {i + 1} failed with {resp.status_code}"

    resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=env.auth_headers["coordinator"],
        json={
            "event_type": "announcement",
            "content": {"de": "Ankündigung 11", "en": "Announcement 11 - should be rate limited"},
        },
    )
    assert resp.status_code == 429
    data = resp.json()
    assert "error" in data or "detail" in data


@pytest.mark.asyncio
async def test_login_rate_limit_10_per_minute(client: AsyncClient, db_session: AsyncSession):
    """The 11th login attempt within a minute from one address returns 429."""
    await create_test_user(
        db_session,
        email="login-limit@test.com",
        password="TestPassword123!",
    )

    # The endpoint counts every attempt, whatever its outcome.
    for _ in range(10):
        resp = await client.post(
            "/api/auth/login",
            json={
                "email": "login-limit@test.com",
                "password": "TestPassword123!",
            },
        )
        assert resp.status_code in [200, 401], f"Login failed with unexpected status {resp.status_code}"

    resp = await client.post(
        "/api/auth/login",
        json={
            "email": "login-limit@test.com",
            "password": "TestPassword123!",
        },
    )
    assert resp.status_code == 429
    data = resp.json()
    assert "error" in data or "detail" in data


@pytest.mark.asyncio
async def test_rate_limit_429_response_format(client: AsyncClient, db_session: AsyncSession):
    """A 429 response carries an error detail that mentions the limit."""
    await create_test_user(
        db_session,
        email="format-test@test.com",
        password="TestPassword123!",
    )

    for _ in range(10):
        await client.post(
            "/api/auth/login",
            json={
                "email": "format-test@test.com",
                "password": "TestPassword123!",
            },
        )

    resp = await client.post(
        "/api/auth/login",
        json={
            "email": "format-test@test.com",
            "password": "TestPassword123!",
        },
    )

    assert resp.status_code == 429

    data = resp.json()
    assert "detail" in data or "error" in data, "429 response should contain error information"
    error_msg = str(data.get("detail", "") or data.get("error", "")).lower()
    assert "limit" in error_msg or "429" in error_msg, "Error message should mention rate limiting"


@pytest.mark.asyncio
async def test_rate_limit_isolation_per_endpoint(client: AsyncClient, db_session: AsyncSession):
    """Exhausting the login limit does not affect the feed and chat endpoints."""
    env = await setup_journey_environment(db_session, client, org_slug="isolation-org")

    run_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={
            "name": "Isolation Test Run",
            "rallye_id": str(env.rallye_id),
            "max_teams": 5,
            "start_time": None,
            "end_time": None,
        },
    )
    assert run_resp.status_code == 201
    run_id = run_resp.json()["id"]

    await activate_run(client, env.slug, run_id, env.auth_headers["coordinator"])

    for _ in range(10):
        await client.post(
            "/api/auth/login",
            json={
                "email": "nonexistent@test.com",
                "password": "WrongPassword123!",
            },
        )

    resp = await client.post(
        "/api/auth/login",
        json={
            "email": "nonexistent@test.com",
            "password": "WrongPassword123!",
        },
    )
    assert resp.status_code == 429

    resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=env.auth_headers["coordinator"],
        json={
            "event_type": "announcement",
            "content": {"de": "Test", "en": "Test"},
        },
    )
    assert resp.status_code == 201, "Feed endpoint should not be affected by login rate limit"

    team_data = await create_team_and_join(
        client,
        env.slug,
        run_id,
        env.auth_headers["participant1"],
        "Isolation Team",
        "#00FF00",
    )
    team_id = team_data["id"]

    resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=env.auth_headers["participant1"],
        json={"content": "Test message"},
    )
    assert resp.status_code == 201, "Chat endpoint should not be affected by login rate limit"


@pytest.mark.asyncio
async def test_register_rate_limited_before_the_password_is_hashed(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """POST /auth/register is limited per client address, and a refused request never reaches the Argon2 hash."""
    from app.config import get_settings
    from app.routers import auth as auth_router

    limit = int(get_settings().auth.register_rate_limit.split("/")[0])
    org = await create_test_organization(db_session, slug="register-limit-org")

    hashed: list[str] = []

    def counting_hash(password: str) -> str:
        hashed.append(password)
        return "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$ZmFrZWhhc2g"

    monkeypatch.setattr(auth_router, "hash_password", counting_hash)

    for i in range(limit):
        resp = await client.post(
            "/api/auth/register",
            json={
                "organization_slug": org.slug,
                "email": f"limit{i}@test.com",
                "password": "SecurePass123!",
                "display_name": f"Limit {i}",
            },
        )
        assert resp.status_code == 201, resp.text

    resp = await client.post(
        "/api/auth/register",
        json={
            "organization_slug": org.slug,
            "email": "one-too-many@test.com",
            "password": "SecurePass123!",
            "display_name": "One Too Many",
        },
    )
    assert resp.status_code == 429
    assert len(hashed) == limit
