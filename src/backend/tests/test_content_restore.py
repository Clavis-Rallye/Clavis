"""
Org-admin restore and permanent delete for archived content.

Covers rallyes, stations and challenges:
- archive (the soft delete): the org_admin sees the row via include_deleted
- restore brings it back (org_admin only, 409 when not archived)
- permanent delete removes the row (org_admin only, requires the archived state)
- the creator role is refused on restore, permanent delete and the include_deleted listing
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.user import UserRole
from app.repositories.challenge import ChallengeRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.station import StationRepository
from tests.factories import create_test_user_with_org, get_auth_headers


async def _setup_org(db_session: AsyncSession, slug: str):
    """Org with an org_admin and a creator; returns (org, admin_headers, creator_headers)."""
    admin, org = await create_test_user_with_org(
        db_session, email=f"admin@{slug}.test", role=UserRole.ORG_ADMIN, org_slug=slug
    )
    from tests.factories import create_test_user

    creator = await create_test_user(
        db_session, email=f"creator@{slug}.test", role=UserRole.CREATOR, organization_id=org.id
    )
    await db_session.commit()
    return org, get_auth_headers(admin), get_auth_headers(creator)


async def _make_rallye(db_session: AsyncSession, org_id) -> str:
    repo = RallyeRepository(db_session, org_id)
    rallye = await repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=False)
    await db_session.commit()
    return str(rallye.id)


@pytest.mark.asyncio
async def test_rallye_archive_restore_roundtrip(client: AsyncClient, db_session: AsyncSession):
    """An archived rallye leaves the normal list and shows in the archive view that a creator may not use.

    The org_admin restores it, and a second restore gives 409.
    """
    org, admin_h, creator_h = await _setup_org(db_session, "restore-r1")
    rallye_id = await _make_rallye(db_session, org.id)

    r = await client.delete(f"/api/orgs/{org.slug}/rallyes/{rallye_id}", headers=creator_h)
    assert r.status_code == 204

    normal = await client.get(f"/api/orgs/{org.slug}/rallyes/", headers=admin_h)
    assert rallye_id not in [i["id"] for i in normal.json()["items"]]
    archived = await client.get(f"/api/orgs/{org.slug}/rallyes/?include_deleted=true", headers=admin_h)
    row = next(i for i in archived.json()["items"] if i["id"] == rallye_id)
    assert row["is_deleted"] is True
    assert row["deleted_at"] is not None

    assert (
        await client.get(f"/api/orgs/{org.slug}/rallyes/?include_deleted=true", headers=creator_h)
    ).status_code == 403
    assert (
        await client.post(f"/api/orgs/{org.slug}/rallyes/{rallye_id}/restore", headers=creator_h)
    ).status_code == 403

    r = await client.post(f"/api/orgs/{org.slug}/rallyes/{rallye_id}/restore", headers=admin_h)
    assert r.status_code == 200
    assert r.json()["is_deleted"] is False
    normal = await client.get(f"/api/orgs/{org.slug}/rallyes/", headers=admin_h)
    assert rallye_id in [i["id"] for i in normal.json()["items"]]

    assert (await client.post(f"/api/orgs/{org.slug}/rallyes/{rallye_id}/restore", headers=admin_h)).status_code == 409


@pytest.mark.asyncio
async def test_rallye_permanent_delete(client: AsyncClient, db_session: AsyncSession):
    """Permanent delete is a second step after the archive and needs the org_admin.

    It removes the rallye even from the archive view, and a second delete gives 404.
    """
    org, admin_h, creator_h = await _setup_org(db_session, "restore-r2")
    rallye_id = await _make_rallye(db_session, org.id)

    assert (
        await client.delete(f"/api/orgs/{org.slug}/rallyes/{rallye_id}/permanent", headers=admin_h)
    ).status_code == 409

    await client.delete(f"/api/orgs/{org.slug}/rallyes/{rallye_id}", headers=creator_h)

    assert (
        await client.delete(f"/api/orgs/{org.slug}/rallyes/{rallye_id}/permanent", headers=creator_h)
    ).status_code == 403

    r = await client.delete(f"/api/orgs/{org.slug}/rallyes/{rallye_id}/permanent", headers=admin_h)
    assert r.status_code == 204
    archived = await client.get(f"/api/orgs/{org.slug}/rallyes/?include_deleted=true", headers=admin_h)
    assert rallye_id not in [i["id"] for i in archived.json()["items"]]
    assert (
        await client.delete(f"/api/orgs/{org.slug}/rallyes/{rallye_id}/permanent", headers=admin_h)
    ).status_code == 404


@pytest.mark.asyncio
async def test_station_archive_restore_permanent(client: AsyncClient, db_session: AsyncSession):
    """A station is archived, restored, archived again and then permanently deleted."""
    org, admin_h, creator_h = await _setup_org(db_session, "restore-s1")
    repo = StationRepository(db_session, org.id)
    station = await repo.create(title={"de": "Station"}, description={"de": "Test"})
    await db_session.commit()
    sid = str(station.id)

    assert (await client.delete(f"/api/orgs/{org.slug}/stations/{sid}", headers=creator_h)).status_code == 200

    archived = await client.get(f"/api/orgs/{org.slug}/stations/?include_deleted=true", headers=admin_h)
    assert sid in [i["id"] for i in archived.json()["items"]]

    r = await client.post(f"/api/orgs/{org.slug}/stations/{sid}/restore", headers=admin_h)
    assert r.status_code == 200 and r.json()["is_deleted"] is False

    await client.delete(f"/api/orgs/{org.slug}/stations/{sid}", headers=creator_h)
    assert (await client.delete(f"/api/orgs/{org.slug}/stations/{sid}/permanent", headers=admin_h)).status_code == 204
    archived = await client.get(f"/api/orgs/{org.slug}/stations/?include_deleted=true", headers=admin_h)
    assert sid not in [i["id"] for i in archived.json()["items"]]


@pytest.mark.asyncio
async def test_challenge_archive_restore_permanent(client: AsyncClient, db_session: AsyncSession):
    """A challenge is archived, restored, archived again and then permanently deleted."""
    org, admin_h, creator_h = await _setup_org(db_session, "restore-c1")
    repo = ChallengeRepository(db_session, org.id)
    challenge = await repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge"},
        instructions={"de": "Test"},
        scoring_rules={"max_points": 10},
    )
    await db_session.commit()
    cid = str(challenge.id)

    assert (await client.delete(f"/api/orgs/{org.slug}/challenges/{cid}", headers=creator_h)).status_code == 200

    archived = await client.get(f"/api/orgs/{org.slug}/challenges/?include_deleted=true", headers=admin_h)
    assert cid in [i["id"] for i in archived.json()["items"]]

    r = await client.post(f"/api/orgs/{org.slug}/challenges/{cid}/restore", headers=admin_h)
    assert r.status_code == 200 and r.json()["is_deleted"] is False

    await client.delete(f"/api/orgs/{org.slug}/challenges/{cid}", headers=creator_h)
    assert (await client.delete(f"/api/orgs/{org.slug}/challenges/{cid}/permanent", headers=admin_h)).status_code == 204
    archived = await client.get(f"/api/orgs/{org.slug}/challenges/?include_deleted=true", headers=admin_h)
    assert cid not in [i["id"] for i in archived.json()["items"]]
