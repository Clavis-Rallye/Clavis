"""Factories for test data (users, organizations, library content) and helpers for authentication."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password
from app.auth.tokens import create_access_token
from app.models.challenge import Challenge, ChallengeType
from app.models.organization import Organization
from app.models.rallye import Rallye
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.station import Station
from app.models.user import User, UserRole
from app.repositories.challenge import ChallengeRepository
from app.repositories.organization import OrganizationRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.rallye_station import RallyeStationRepository
from app.repositories.rallye_station_challenge import RallyeStationChallengeRepository
from app.repositories.station import StationRepository
from app.repositories.user import UserRepository


async def create_test_organization(
    session: AsyncSession,
    name: str = "Test Organization",
    slug: str = "test-org",
    languages: list[str] | None = None,
    settings: dict | None = None,
) -> Organization:
    """Create a test organization. Languages default to ["de"], settings to {}."""
    repo = OrganizationRepository(session)

    organization = await repo.create(
        name=name,
        slug=slug,
        languages=languages if languages is not None else ["de"],
        settings=settings if settings is not None else {},
    )

    await session.commit()
    await session.refresh(organization)

    return organization


async def create_test_user(
    session: AsyncSession,
    email: str = "test@example.com",
    password: str = "SecurePassword123!",
    display_name: str = "Test User",
    role: UserRole = UserRole.PARTICIPANT,
    organization: Organization | None = None,
    organization_id: UUID | None = None,
    totp_enabled: bool = False,
    totp_secret: str | None = None,
    totp_recovery_codes: dict | None = None,
    webauthn_credentials: dict | None = None,
    is_active: bool = True,
) -> User:
    """
    Create a test user.

    Without organization and organization_id, it creates a new test organization.
    The password is hashed.
    """
    if organization is None and organization_id is None:
        organization = await create_test_organization(
            session,
            name=f"Org for {display_name}",
            slug=f"org-{email.split('@')[0]}",
        )
        organization_id = organization.id
    elif organization is not None:
        organization_id = organization.id

    password_hash = hash_password(password)

    repo = UserRepository(session, organization_id)  # type: ignore
    user = await repo.create(
        email=email,
        display_name=display_name,
        password_hash=password_hash,
        role=role,
        totp_enabled=totp_enabled,
        totp_secret=totp_secret,
        totp_recovery_codes=totp_recovery_codes,
        webauthn_credentials=webauthn_credentials,
        is_active=is_active,
    )

    await session.commit()
    await session.refresh(user)

    return user


async def create_test_user_with_org(
    session: AsyncSession,
    email: str = "test@example.com",
    password: str = "SecurePassword123!",
    display_name: str = "Test User",
    role: UserRole = UserRole.PARTICIPANT,
    org_name: str = "Test Organization",
    org_slug: str = "test-org",
) -> tuple[User, Organization]:
    """Create a test user and its organization, and return both."""
    organization = await create_test_organization(
        session,
        name=org_name,
        slug=org_slug,
    )

    user = await create_test_user(
        session,
        email=email,
        password=password,
        display_name=display_name,
        role=role,
        organization=organization,
    )

    return user, organization


def get_auth_headers(user: User, token: str | None = None) -> dict[str, str]:
    """Bearer headers for the user. Without a token, it creates a JWT."""
    if token is None:
        token = create_access_token(
            user_id=user.id,
            role=user.role.value,
            organization_id=user.organization_id,
        )

    return {
        "Authorization": f"Bearer {token}",
    }


def get_auth_cookies(user: User, token: str | None = None) -> dict[str, str]:
    """Cookies with the access_token of the user. Without a token, it creates a JWT."""
    if token is None:
        token = create_access_token(
            user_id=user.id,
            role=user.role.value,
            organization_id=user.organization_id,
        )

    return {
        "access_token": token,
    }


async def create_multiple_users(
    session: AsyncSession,
    organization: Organization,
    count: int = 5,
    role: UserRole = UserRole.PARTICIPANT,
    email_prefix: str = "user",
) -> list[User]:
    """Create `count` users in one organization, with emails <email_prefix>1@example.com and so on."""
    users = []

    for i in range(1, count + 1):
        user = await create_test_user(
            session,
            email=f"{email_prefix}{i}@example.com",
            display_name=f"User {i}",
            role=role,
            organization=organization,
        )
        users.append(user)

    return users


async def create_user_with_2fa(
    session: AsyncSession,
    email: str = "2fa@example.com",
    password: str = "SecurePassword123!",
    display_name: str = "2FA User",
    role: UserRole = UserRole.PARTICIPANT,
    organization: Organization | None = None,
) -> tuple[User, str]:
    """Create a user with TOTP enabled and return it with the TOTP secret."""
    import pyotp

    totp_secret = pyotp.random_base32()

    recovery_codes = [pyotp.random_base32()[:8] for _ in range(8)]
    hashed_codes = [hash_password(code) for code in recovery_codes]

    user = await create_test_user(
        session,
        email=email,
        password=password,
        display_name=display_name,
        role=role,
        organization=organization,
        totp_enabled=True,
        totp_secret=totp_secret,
        totp_recovery_codes={"codes": hashed_codes},
    )

    return user, totp_secret


# ============================================================================
# Library: stations, challenges, and composition links
# ============================================================================


async def create_test_rallye(
    session: AsyncSession,
    organization: Organization,
    title: dict[str, str] | None = None,
    description: dict[str, str] | None = None,
    is_published: bool = True,
) -> Rallye:
    """Create a rallye in the given organization (committed)."""
    repo = RallyeRepository(session, organization.id)
    rallye = await repo.create(
        title=title or {"de": "Testrallye", "en": "Test Rallye"},
        description=description or {"de": "Beschreibung", "en": "Description"},
        is_published=is_published,
    )
    await session.commit()
    await session.refresh(rallye)
    return rallye


async def create_test_station(
    session: AsyncSession,
    organization: Organization,
    title: dict[str, str] | None = None,
    description: dict[str, str] | None = None,
    address: str | None = None,
    location_lat: float | None = 48.3705,
    location_lng: float | None = 10.8978,
    created_by_user_id: UUID | None = None,
) -> Station:
    """
    Create a library Station (org-scoped, NOT linked to any rallye).

    Use link_station_to_rallye() to compose it into a rallye.
    """
    repo = StationRepository(session, organization.id)
    station = await repo.create(
        title=title or {"de": "Station", "en": "Station"},
        description=description or {"de": "Beschreibung", "en": "Description"},
        address=address,
        location_lat=location_lat,
        location_lng=location_lng,
        created_by_user_id=created_by_user_id,
    )
    await session.commit()
    await session.refresh(station)
    return station


async def create_test_challenge(
    session: AsyncSession,
    organization: Organization,
    challenge_type: ChallengeType = ChallengeType.TEXT,
    title: dict[str, str] | None = None,
    instructions: dict[str, str] | None = None,
    scoring_rules: dict | None = None,
    time_estimate_minutes: int | None = None,
    suggested_solution: str | None = None,
    created_by_user_id: UUID | None = None,
) -> Challenge:
    """
    Create a library Challenge (org-scoped, NOT linked to any rallye-station).

    Use link_challenge_to_rallye_station() to compose it into a rallye's station.
    """
    repo = ChallengeRepository(session, organization.id)
    challenge = await repo.create(
        challenge_type=challenge_type,
        title=title or {"de": "Aufgabe", "en": "Challenge"},
        instructions=instructions or {"de": "Anleitung", "en": "Instructions"},
        scoring_rules=scoring_rules if scoring_rules is not None else {"max_points": 10},
        time_estimate_minutes=time_estimate_minutes,
        suggested_solution=suggested_solution,
        created_by_user_id=created_by_user_id,
    )
    await session.commit()
    await session.refresh(challenge)
    return challenge


async def link_station_to_rallye(
    session: AsyncSession,
    organization: Organization,
    rallye: Rallye,
    station: Station,
    sort_order: int = 0,
) -> RallyeStation:
    """Link a library station into a rallye at the given position (committed)."""
    repo = RallyeStationRepository(session, organization.id)
    link = await repo.create(rallye_id=rallye.id, station_id=station.id, sort_order=sort_order)
    await session.commit()
    await session.refresh(link)
    return link


async def link_challenge_to_rallye_station(
    session: AsyncSession,
    organization: Organization,
    rallye_station: RallyeStation,
    challenge: Challenge,
    sort_order: int = 0,
) -> RallyeStationChallenge:
    """Match a library challenge to a rallye's station at the given position (committed)."""
    repo = RallyeStationChallengeRepository(session, organization.id)
    link = await repo.create(rallye_station_id=rallye_station.id, challenge_id=challenge.id, sort_order=sort_order)
    await session.commit()
    await session.refresh(link)
    return link


async def create_org_admin_user(
    session: AsyncSession,
    email: str = "admin@example.com",
    password: str = "AdminPassword123!",
    display_name: str = "Admin User",
    org_name: str = "Admin Organization",
    org_slug: str = "admin-org",
) -> tuple[User, Organization]:
    """Create an org_admin user with a new organization, and return both."""
    return await create_test_user_with_org(
        session,
        email=email,
        password=password,
        display_name=display_name,
        role=UserRole.ORG_ADMIN,
        org_name=org_name,
        org_slug=org_slug,
    )
