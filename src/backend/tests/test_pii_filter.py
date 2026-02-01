"""
Tests for PII field-level filtering.

These tests verify the PII visibility matrix is correctly enforced across all roles.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.auth.pii_filter import (
    UserCoordinatorProfile,
    UserFullProfile,
    UserPublicProfile,
    filter_dict_fields,
    filter_user_list,
    filter_user_profile,
)
from app.models.user import User, UserRole


@pytest.fixture
def org_id():
    """Shared organization ID for all test users."""
    return uuid4()


@pytest.fixture
def guest_user(org_id):
    return User(
        id=uuid4(),
        email="guest@example.com",
        display_name="Guest User",
        password_hash="hashed_password",
        role=UserRole.GUEST,
        organization_id=org_id,
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def participant_user(org_id):
    return User(
        id=uuid4(),
        email="participant@example.com",
        display_name="Participant User",
        password_hash="hashed_password",
        role=UserRole.PARTICIPANT,
        organization_id=org_id,
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def coordinator_user(org_id):
    return User(
        id=uuid4(),
        email="coordinator@example.com",
        display_name="Coordinator User",
        password_hash="hashed_password",
        role=UserRole.COORDINATOR,
        organization_id=org_id,
        is_active=True,
        last_login=datetime.now(UTC),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def creator_user(org_id):
    return User(
        id=uuid4(),
        email="creator@example.com",
        display_name="Creator User",
        password_hash="hashed_password",
        role=UserRole.CREATOR,
        organization_id=org_id,
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def org_admin_user(org_id):
    return User(
        id=uuid4(),
        email="admin@example.com",
        display_name="Admin User",
        password_hash="hashed_password",
        role=UserRole.ORG_ADMIN,
        organization_id=org_id,
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


# ============================================================================
# Test filter_user_profile
# ============================================================================


def test_guest_views_another_user_sees_public_profile_only(guest_user, participant_user):
    """Guest viewing another user should see only public profile (no email)."""
    profile = filter_user_profile(participant_user, guest_user)

    assert isinstance(profile, UserPublicProfile)
    assert profile.id == participant_user.id
    assert profile.display_name == participant_user.display_name
    assert profile.role == participant_user.role.value
    assert not hasattr(profile, "email")


def test_participant_views_another_user_sees_public_profile_only(participant_user, coordinator_user):
    """Participant viewing another user should see only public profile."""
    profile = filter_user_profile(coordinator_user, participant_user)

    assert isinstance(profile, UserPublicProfile)
    assert profile.id == coordinator_user.id
    assert profile.display_name == coordinator_user.display_name
    assert profile.role == coordinator_user.role.value
    assert not hasattr(profile, "email")


def test_coordinator_views_participant_sees_coordinator_profile(coordinator_user, participant_user):
    """Coordinator viewing participant should see coordinator profile (no email)."""
    profile = filter_user_profile(participant_user, coordinator_user)

    assert isinstance(profile, UserCoordinatorProfile)
    assert profile.id == participant_user.id
    assert profile.display_name == participant_user.display_name
    assert profile.role == participant_user.role.value
    assert not hasattr(profile, "email")


def test_creator_views_participant_sees_coordinator_profile(creator_user, participant_user):
    """Creator viewing participant should see coordinator profile (no email)."""
    profile = filter_user_profile(participant_user, creator_user)

    assert isinstance(profile, UserCoordinatorProfile)
    assert profile.id == participant_user.id
    assert profile.display_name == participant_user.display_name
    assert not hasattr(profile, "email")


def test_org_admin_views_any_user_sees_full_profile(org_admin_user, participant_user):
    """Org admin viewing any user should see full profile including email."""
    profile = filter_user_profile(participant_user, org_admin_user)

    assert isinstance(profile, UserFullProfile)
    assert profile.id == participant_user.id
    assert profile.display_name == participant_user.display_name
    assert profile.role == participant_user.role.value
    assert profile.email == participant_user.email
    assert profile.is_active == participant_user.is_active
    assert profile.organization_id == participant_user.organization_id
    assert profile.created_at is not None


def test_user_views_own_profile_always_sees_full_profile(guest_user):
    """Any user viewing their own profile should see full profile regardless of role."""
    profile = filter_user_profile(guest_user, guest_user)

    assert isinstance(profile, UserFullProfile)
    assert profile.id == guest_user.id
    assert profile.email == guest_user.email
    assert profile.is_active is True


def test_participant_views_own_profile_sees_full_profile(participant_user):
    """Participant viewing own profile should see full profile."""
    profile = filter_user_profile(participant_user, participant_user)

    assert isinstance(profile, UserFullProfile)
    assert profile.email == participant_user.email


def test_coordinator_views_own_profile_sees_full_profile(coordinator_user):
    """Coordinator viewing own profile should see full profile."""
    profile = filter_user_profile(coordinator_user, coordinator_user)

    assert isinstance(profile, UserFullProfile)
    assert profile.email == coordinator_user.email
    assert profile.last_login is not None  # Coordinator has last_login set


def test_full_profile_includes_all_fields(org_admin_user, coordinator_user):
    """UserFullProfile should include all expected fields."""
    profile = filter_user_profile(coordinator_user, org_admin_user)

    assert isinstance(profile, UserFullProfile)
    assert profile.id == coordinator_user.id
    assert profile.display_name == coordinator_user.display_name
    assert profile.role == coordinator_user.role.value
    assert profile.email == coordinator_user.email
    assert profile.is_active == coordinator_user.is_active
    assert profile.organization_id == coordinator_user.organization_id
    assert profile.created_at is not None
    assert profile.last_login is not None  # coordinator_user has it set


def test_full_profile_handles_null_last_login(org_admin_user, participant_user):
    """UserFullProfile should handle null last_login correctly."""
    # participant_user doesn't have last_login set
    profile = filter_user_profile(participant_user, org_admin_user)

    assert isinstance(profile, UserFullProfile)
    assert profile.last_login is None


# ============================================================================
# Test filter_user_list
# ============================================================================


def test_filter_user_list_with_guest_viewer(guest_user, participant_user, coordinator_user, creator_user):
    """Filter list of users as guest should return all public profiles."""
    users = [participant_user, coordinator_user, creator_user]
    profiles = filter_user_list(users, guest_user)

    assert len(profiles) == 3
    assert all(isinstance(p, UserPublicProfile) for p in profiles)
    assert all(not hasattr(p, "email") for p in profiles)


def test_filter_user_list_with_coordinator_viewer(coordinator_user, guest_user, participant_user, creator_user):
    """Filter list of users as coordinator should return coordinator profiles."""
    users = [guest_user, participant_user, creator_user]
    profiles = filter_user_list(users, coordinator_user)

    assert len(profiles) == 3
    assert all(isinstance(p, UserCoordinatorProfile) for p in profiles)
    assert all(not hasattr(p, "email") for p in profiles)


def test_filter_user_list_with_org_admin_viewer(org_admin_user, guest_user, participant_user, coordinator_user):
    """Filter list of users as org admin should return full profiles."""
    users = [guest_user, participant_user, coordinator_user]
    profiles = filter_user_list(users, org_admin_user)

    assert len(profiles) == 3
    assert all(isinstance(p, UserFullProfile) for p in profiles)
    assert all(hasattr(p, "email") for p in profiles)


def test_filter_user_list_includes_viewer_with_full_profile(participant_user, coordinator_user, creator_user):
    """When viewer is in the list, they should see their own full profile."""
    users = [participant_user, coordinator_user, creator_user]
    profiles = filter_user_list(users, participant_user)

    # participant_user is first in list and should see own full profile
    assert isinstance(profiles[0], UserFullProfile)
    assert profiles[0].id == participant_user.id
    assert profiles[0].email == participant_user.email

    assert isinstance(profiles[1], UserPublicProfile)
    assert isinstance(profiles[2], UserPublicProfile)


def test_filter_user_list_empty_list(guest_user):
    """Filter empty user list should return empty list."""
    profiles = filter_user_list([], guest_user)
    assert profiles == []


# ============================================================================
# Test filter_dict_fields
# ============================================================================


def test_filter_dict_fields_org_admin_sees_everything(org_admin_user):
    """Org admin should see all fields including sensitive ones."""
    data = {
        "user_id": str(uuid4()),
        "email": "user@example.com",
        "display_name": "User Name",
        "password_hash": "hashed",
        "totp_secret": "secret",
    }

    filtered = filter_dict_fields(data, org_admin_user)

    assert filtered["email"] == "user@example.com"
    assert filtered["password_hash"] == "hashed"
    assert filtered["totp_secret"] == "secret"


def test_filter_dict_fields_participant_sensitive_removed(participant_user):
    """Participant should not see sensitive fields."""
    data = {
        "user_id": str(uuid4()),
        "email": "user@example.com",
        "display_name": "User Name",
        "password_hash": "hashed",
        "totp_secret": "secret",
    }

    filtered = filter_dict_fields(data, participant_user)

    assert "email" not in filtered
    assert "password_hash" not in filtered
    assert "totp_secret" not in filtered
    assert filtered["user_id"] == data["user_id"]
    assert filtered["display_name"] == "User Name"


def test_filter_dict_fields_coordinator_sensitive_removed(coordinator_user):
    """Coordinator should not see sensitive fields (only org_admin can)."""
    data = {
        "user_id": str(uuid4()),
        "email": "user@example.com",
        "display_name": "User Name",
        "webauthn_credentials": [{"credential_id": "abc"}],
    }

    filtered = filter_dict_fields(data, coordinator_user)

    assert "email" not in filtered
    assert "webauthn_credentials" not in filtered
    assert filtered["display_name"] == "User Name"


def test_filter_dict_fields_nested_dicts_filtered_recursively(participant_user):
    """Nested dictionaries should be filtered recursively."""
    data = {
        "event": "user_login",
        "actor": {
            "user_id": str(uuid4()),
            "email": "actor@example.com",
            "display_name": "Actor Name",
        },
        "target": {
            "user_id": str(uuid4()),
            "email": "target@example.com",
            "display_name": "Target Name",
        },
    }

    filtered = filter_dict_fields(data, participant_user)

    assert filtered["event"] == "user_login"
    assert "email" not in filtered["actor"]
    assert "email" not in filtered["target"]
    assert filtered["actor"]["display_name"] == "Actor Name"
    assert filtered["target"]["display_name"] == "Target Name"


def test_filter_dict_fields_lists_with_dicts_filtered_recursively(participant_user):
    """Lists containing dictionaries should be filtered recursively."""
    data = {
        "users": [
            {"user_id": str(uuid4()), "email": "user1@example.com", "display_name": "User 1"},
            {"user_id": str(uuid4()), "email": "user2@example.com", "display_name": "User 2"},
        ]
    }

    filtered = filter_dict_fields(data, participant_user)

    assert len(filtered["users"]) == 2
    assert "email" not in filtered["users"][0]
    assert "email" not in filtered["users"][1]
    assert filtered["users"][0]["display_name"] == "User 1"
    assert filtered["users"][1]["display_name"] == "User 2"


def test_filter_dict_fields_custom_sensitive_fields(participant_user):
    """Custom sensitive fields list should be respected."""
    data = {
        "public_field": "public",
        "custom_sensitive": "secret",
        "email": "user@example.com",  # Normally sensitive, but not in custom list
    }

    filtered = filter_dict_fields(data, participant_user, sensitive_fields=["custom_sensitive"])

    assert "custom_sensitive" not in filtered
    assert filtered["email"] == "user@example.com"
    assert filtered["public_field"] == "public"


def test_filter_dict_fields_all_default_sensitive_fields_removed(participant_user):
    """All default sensitive fields should be removed for non-admins."""
    data = {
        "email": "user@example.com",
        "password_hash": "hashed",
        "totp_secret": "secret",
        "webauthn_credentials": [{"id": "cred"}],
        "totp_recovery_codes": ["code1", "code2"],
        "last_password_hash": "old_hash",
        "failed_login_attempts": 3,
        "locked_until": "2026-02-12T12:00:00Z",
        "display_name": "User Name",  # Not sensitive
    }

    filtered = filter_dict_fields(data, participant_user)

    assert "email" not in filtered
    assert "password_hash" not in filtered
    assert "totp_secret" not in filtered
    assert "webauthn_credentials" not in filtered
    assert "totp_recovery_codes" not in filtered
    assert "last_password_hash" not in filtered
    assert "failed_login_attempts" not in filtered
    assert "locked_until" not in filtered
    assert filtered["display_name"] == "User Name"


def test_filter_dict_fields_does_not_mutate_original(participant_user):
    """Filtering should not mutate the original dictionary."""
    data = {
        "email": "user@example.com",
        "display_name": "User Name",
    }
    original_keys = set(data.keys())

    filtered = filter_dict_fields(data, participant_user)

    assert set(data.keys()) == original_keys
    assert data["email"] == "user@example.com"
    assert "email" not in filtered


# ============================================================================
# Test Role Hierarchy Edge Cases
# ============================================================================


def test_creator_has_same_pii_access_as_coordinator(creator_user, participant_user):
    """Creator and coordinator should have same PII visibility (no email)."""
    creator_profile = filter_user_profile(participant_user, creator_user)
    coordinator_user_temp = User(
        id=uuid4(),
        email="temp_coordinator@example.com",
        display_name="Temp Coordinator",
        password_hash="hashed",
        role=UserRole.COORDINATOR,
        organization_id=creator_user.organization_id,
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    coordinator_profile = filter_user_profile(participant_user, coordinator_user_temp)

    assert type(creator_profile) is type(coordinator_profile)
    assert not hasattr(creator_profile, "email")
    assert not hasattr(coordinator_profile, "email")


def test_guest_has_same_pii_access_as_participant(guest_user, participant_user, coordinator_user):
    """Guest and participant should have same PII visibility (public only)."""
    guest_profile = filter_user_profile(coordinator_user, guest_user)
    participant_profile = filter_user_profile(coordinator_user, participant_user)

    assert type(guest_profile) is type(participant_profile)
    assert isinstance(guest_profile, UserPublicProfile)
    assert not hasattr(guest_profile, "email")
    assert not hasattr(participant_profile, "email")
