"""
Password policy and account lockout integration tests.

Tests password validation, password change, password reuse prevention,
account lockout triggers, lockout cooldown, and password forgot/reset flows.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.repositories.password_reset import PasswordResetRepository
from app.repositories.user import UserRepository
from tests.factories import create_test_user, get_auth_cookies


class TestPasswordPolicy:
    """Test password policy validation."""

    @pytest.mark.asyncio
    async def test_password_too_short_rejected(self, client: AsyncClient):
        """A password shorter than the minimum length is rejected."""
        settings = get_settings()

        response = await client.post(
            "/api/auth/register",
            json={
                "organization_name": "Test Org",
                "organization_slug": "test-org-short",
                "email": "short@example.com",
                "password": "Abc1!",  # Only 5 characters
                "display_name": "Short Password User",
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "errors" in data["detail"]
        errors = data["detail"]["errors"]
        assert any(f"at least {settings.auth.password_min_length} characters" in err for err in errors)

    @pytest.mark.asyncio
    async def test_password_missing_criteria_rejected(self, client: AsyncClient):
        """A password that meets too few character criteria is rejected."""
        settings = get_settings()

        response = await client.post(
            "/api/auth/register",
            json={
                "organization_name": "Test Org",
                "organization_slug": "test-org-criteria",
                "email": "criteria@example.com",
                "password": "alllowercase",  # Only lowercase, no upper/digit/special
                "display_name": "Missing Criteria User",
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "errors" in data["detail"]
        errors = data["detail"]["errors"]
        assert any(f"at least {settings.auth.password_criteria_count}" in err for err in errors)

    @pytest.mark.asyncio
    async def test_password_valid_accepted(self, client: AsyncClient):
        """A password that meets the policy is accepted."""
        response = await client.post(
            "/api/auth/register",
            json={
                "organization_name": "Valid Org",
                "organization_slug": "valid-org",
                "email": "valid@example.com",
                "password": "ValidPassword123!",  # Meets all criteria
                "display_name": "Valid User",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["email"] == "valid@example.com"
        assert data["message"] == "Registration successful"


class TestPasswordChange:
    """Test password change functionality."""

    @pytest.mark.asyncio
    async def test_password_change_success(self, client: AsyncClient, db_session: AsyncSession):
        """A correct old password changes the password, and the new one logs in."""
        user = await create_test_user(
            db_session,
            email="change@example.com",
            password="OldPassword123!",
        )

        cookies = get_auth_cookies(user)

        response = await client.post(
            "/api/auth/password-change",
            cookies=cookies,
            json={
                "old_password": "OldPassword123!",
                "new_password": "NewPassword456!",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Password changed successfully"

        response = await client.post(
            "/api/auth/login",
            json={
                "email": "change@example.com",
                "password": "NewPassword456!",
            },
        )

        assert response.status_code == 200
        assert response.json()["status"] == "success"

    @pytest.mark.asyncio
    async def test_password_change_wrong_old_password(self, client: AsyncClient, db_session: AsyncSession):
        """A wrong old password is refused with 401."""
        user = await create_test_user(
            db_session,
            email="wrongold@example.com",
            password="CurrentPassword123!",
        )

        cookies = get_auth_cookies(user)

        response = await client.post(
            "/api/auth/password-change",
            cookies=cookies,
            json={
                "old_password": "WrongOldPassword123!",
                "new_password": "NewPassword456!",
            },
        )

        assert response.status_code == 401
        data = response.json()
        assert "incorrect" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_password_change_policy_violation(self, client: AsyncClient, db_session: AsyncSession):
        """A new password that violates the policy is refused with 400."""
        user = await create_test_user(
            db_session,
            email="policy@example.com",
            password="CurrentPassword123!",
        )

        cookies = get_auth_cookies(user)

        response = await client.post(
            "/api/auth/password-change",
            cookies=cookies,
            json={
                "old_password": "CurrentPassword123!",
                "new_password": "weak",  # Too short
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "errors" in data["detail"]


class TestPasswordReuse:
    """Test password reuse prevention."""

    @pytest.mark.asyncio
    async def test_password_reuse_rejected(self, client: AsyncClient, db_session: AsyncSession):
        """The last password cannot be reused."""
        user = await create_test_user(
            db_session,
            email="reuse@example.com",
            password="Password123!",
        )

        cookies = get_auth_cookies(user)

        response = await client.post(
            "/api/auth/password-change",
            cookies=cookies,
            json={
                "old_password": "Password123!",
                "new_password": "Password123!",
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "errors" in data["detail"]
        errors = data["detail"]["errors"]
        assert any("reuse" in err.lower() for err in errors)

    @pytest.mark.asyncio
    async def test_password_reuse_after_change(self, client: AsyncClient, db_session: AsyncSession):
        """A previous password cannot be reused after a password change."""
        user = await create_test_user(
            db_session,
            email="reuse2@example.com",
            password="FirstPassword123!",
        )

        cookies = get_auth_cookies(user)

        # The change ends sessions minted before it, so the follow-up requests below
        # must use the fresh cookie from the change response.
        response = await client.post(
            "/api/auth/password-change",
            cookies=cookies,
            json={
                "old_password": "FirstPassword123!",
                "new_password": "SecondPassword456!",
            },
        )

        assert response.status_code == 200
        cookies = response.cookies

        response = await client.post(
            "/api/auth/password-change",
            cookies=cookies,
            json={
                "old_password": "SecondPassword456!",
                "new_password": "FirstPassword123!",
            },
        )

        assert response.status_code == 400
        data = response.json()
        errors = data["detail"]["errors"]
        assert any("reuse" in err.lower() for err in errors)


class TestAccountLockout:
    """Test account lockout on failed login attempts."""

    @pytest.mark.asyncio
    async def test_lockout_after_max_failures(self, client: AsyncClient, db_session: AsyncSession):
        """The account locks with 423 after the maximum number of failed logins."""
        settings = get_settings()

        await create_test_user(
            db_session,
            email="lockout@example.com",
            password="CorrectPassword123!",
        )

        for i in range(settings.auth.max_login_attempts):
            response = await client.post(
                "/api/auth/login",
                json={
                    "email": "lockout@example.com",
                    "password": "WrongPassword123!",
                },
            )
            if i < settings.auth.max_login_attempts - 1:
                assert response.status_code == 401
            else:
                # The Nth failed attempt (where N = max_login_attempts) should lock and return 423
                assert response.status_code == 423
                data = response.json()
                assert "locked" in data["detail"].lower()

        response = await client.post(
            "/api/auth/login",
            json={
                "email": "lockout@example.com",
                "password": "CorrectPassword123!",
            },
        )

        assert response.status_code == 423  # HTTP 423 Locked
        data = response.json()
        assert "locked" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_lockout_prevents_login_with_correct_password(self, client: AsyncClient, db_session: AsyncSession):
        """A locked account cannot log in even with the correct password."""
        settings = get_settings()

        await create_test_user(
            db_session,
            email="locked@example.com",
            password="CorrectPassword123!",
        )

        for _i in range(settings.auth.max_login_attempts):
            await client.post(
                "/api/auth/login",
                json={
                    "email": "locked@example.com",
                    "password": "WrongPassword123!",
                },
            )

        response = await client.post(
            "/api/auth/login",
            json={
                "email": "locked@example.com",
                "password": "CorrectPassword123!",
            },
        )

        assert response.status_code == 423
        data = response.json()
        assert "locked" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_failed_attempts_reset_on_success(self, client: AsyncClient, db_session: AsyncSession):
        """A successful login resets the failed login counter."""
        settings = get_settings()

        user = await create_test_user(
            db_session,
            email="reset@example.com",
            password="CorrectPassword123!",
        )

        for _i in range(settings.auth.max_login_attempts - 1):
            await client.post(
                "/api/auth/login",
                json={
                    "email": "reset@example.com",
                    "password": "WrongPassword123!",
                },
            )

        await db_session.refresh(user)
        assert user.failed_login_attempts == settings.auth.max_login_attempts - 1

        response = await client.post(
            "/api/auth/login",
            json={
                "email": "reset@example.com",
                "password": "CorrectPassword123!",
            },
        )

        assert response.status_code == 200

        await db_session.refresh(user)
        assert user.failed_login_attempts == 0
        assert user.locked_until is None


class TestLockoutCooldown:
    """Test account lockout cooldown and automatic unlock."""

    @pytest.mark.asyncio
    async def test_lockout_unlocks_after_cooldown(self, client: AsyncClient, db_session: AsyncSession):
        """A locked account unlocks automatically after the cooldown."""
        settings = get_settings()

        user = await create_test_user(
            db_session,
            email="cooldown@example.com",
            password="CorrectPassword123!",
        )

        for _i in range(settings.auth.max_login_attempts + 1):
            await client.post(
                "/api/auth/login",
                json={
                    "email": "cooldown@example.com",
                    "password": "WrongPassword123!",
                },
            )

        response = await client.post(
            "/api/auth/login",
            json={
                "email": "cooldown@example.com",
                "password": "CorrectPassword123!",
            },
        )
        assert response.status_code == 423

        # Expire the lockout as if the cooldown had passed.
        repo = UserRepository(db_session, user.organization_id)
        await db_session.refresh(user)
        await repo.update(
            user.id,
            locked_until=datetime.now(UTC) - timedelta(minutes=1),
        )
        await db_session.commit()

        response = await client.post(
            "/api/auth/login",
            json={
                "email": "cooldown@example.com",
                "password": "CorrectPassword123!",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

        await db_session.refresh(user)
        assert user.locked_until is None
        assert user.failed_login_attempts == 0

    @pytest.mark.asyncio
    async def test_lockout_still_active_during_cooldown(self, client: AsyncClient, db_session: AsyncSession):
        """A locked account stays locked during the cooldown, and the response names the expiry time."""
        settings = get_settings()

        user = await create_test_user(
            db_session,
            email="during@example.com",
            password="CorrectPassword123!",
        )

        for _i in range(settings.auth.max_login_attempts + 1):
            await client.post(
                "/api/auth/login",
                json={
                    "email": "during@example.com",
                    "password": "WrongPassword123!",
                },
            )

        # Set locked_until to future time (still within cooldown)
        repo = UserRepository(db_session, user.organization_id)
        await db_session.refresh(user)
        future_lockout = datetime.now(UTC) + timedelta(minutes=10)
        await repo.update(user.id, locked_until=future_lockout)
        await db_session.commit()

        response = await client.post(
            "/api/auth/login",
            json={
                "email": "during@example.com",
                "password": "CorrectPassword123!",
            },
        )

        assert response.status_code == 423
        data = response.json()
        assert "locked" in data["detail"].lower()
        assert future_lockout.isoformat() in data["detail"]


class TestPasswordForgot:
    """Test password forgot/reset flow with database-backed token storage."""

    @pytest.mark.asyncio
    async def test_password_forgot_generates_token_in_db(self, client: AsyncClient, db_session: AsyncSession):
        """A password forgot request stores a reset token in the database."""
        user = await create_test_user(
            db_session,
            email="forgot@example.com",
            password="OldPassword123!",
        )

        reset_repo = PasswordResetRepository(db_session)
        await reset_repo.invalidate_for_user(user.id)
        await db_session.commit()

        response = await client.post(
            "/api/auth/password-forgot",
            json={"email": "forgot@example.com"},
        )

        assert response.status_code == 200
        data = response.json()
        # Generic message (doesn't reveal if email exists)
        assert "email exists" in data["message"].lower()

        active_tokens = await reset_repo.get_active_for_user(user.id)
        assert len(active_tokens) == 1
        assert active_tokens[0].user_id == user.id
        assert active_tokens[0].used_at is None

    @pytest.mark.asyncio
    async def test_password_forgot_nonexistent_email_no_token(self, client: AsyncClient, db_session: AsyncSession):
        """A password forgot request for an unknown email creates no token."""
        from sqlalchemy import select

        from app.models.password_reset_token import PasswordResetToken

        result = await db_session.execute(select(PasswordResetToken))
        tokens_before = len(result.scalars().all())

        response = await client.post(
            "/api/auth/password-forgot",
            json={"email": "nonexistent@example.com"},
        )

        # The same message as for a known address, so the response does not reveal whether the email exists.
        assert response.status_code == 200
        data = response.json()
        assert "email exists" in data["message"].lower()

        result = await db_session.execute(select(PasswordResetToken))
        tokens_after = len(result.scalars().all())
        assert tokens_after == tokens_before

    @pytest.mark.asyncio
    async def test_password_reset_with_valid_token(self, client: AsyncClient, db_session: AsyncSession):
        """A valid reset token resets the password."""
        user = await create_test_user(
            db_session,
            email="reset@example.com",
            password="OldPassword123!",
        )

        reset_repo = PasswordResetRepository(db_session)
        raw_token = await reset_repo.create_token(user.id)
        await db_session.commit()

        response = await client.post(
            "/api/auth/password-reset",
            json={
                "token": raw_token,
                "new_password": "NewPassword456!",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "successfully" in data["message"].lower()

        response = await client.post(
            "/api/auth/login",
            json={
                "email": "reset@example.com",
                "password": "NewPassword456!",
            },
        )

        assert response.status_code == 200
        assert response.json()["status"] == "success"

        response = await client.post(
            "/api/auth/login",
            json={
                "email": "reset@example.com",
                "password": "OldPassword123!",
            },
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_password_reset_invalid_token(self, client: AsyncClient):
        """An invalid reset token is rejected."""
        response = await client.post(
            "/api/auth/password-reset",
            json={
                "token": "invalid-token-12345",
                "new_password": "NewPassword456!",
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "invalid" in data["detail"]["detail"].lower()

    @pytest.mark.asyncio
    async def test_password_reset_expired_token_rejected(self, client: AsyncClient, db_session: AsyncSession):
        """An expired reset token is rejected."""
        user = await create_test_user(
            db_session,
            email="expired@example.com",
            password="OldPassword123!",
        )

        reset_repo = PasswordResetRepository(db_session)
        # expiry_hours=0 sets expires_at to now, so the token is expired by the time it is used.
        raw_token = await reset_repo.create_token(user.id, expiry_hours=0)
        await db_session.commit()

        response = await client.post(
            "/api/auth/password-reset",
            json={
                "token": raw_token,
                "new_password": "NewPassword456!",
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "invalid" in data["detail"]["detail"].lower() or "expired" in data["detail"]["detail"].lower()

    @pytest.mark.asyncio
    async def test_password_reset_used_token_rejected(self, client: AsyncClient, db_session: AsyncSession):
        """A reset token cannot be used twice."""
        user = await create_test_user(
            db_session,
            email="singleuse@example.com",
            password="OldPassword123!",
        )

        reset_repo = PasswordResetRepository(db_session)
        raw_token = await reset_repo.create_token(user.id)
        await db_session.commit()

        response = await client.post(
            "/api/auth/password-reset",
            json={
                "token": raw_token,
                "new_password": "NewPassword456!",
            },
        )

        assert response.status_code == 200

        response = await client.post(
            "/api/auth/password-reset",
            json={
                "token": raw_token,
                "new_password": "AnotherPassword789!",
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert "invalid" in data["detail"]["detail"].lower()

    @pytest.mark.asyncio
    async def test_password_reset_clears_lockout(self, client: AsyncClient, db_session: AsyncSession):
        """A password reset clears the account lockout."""
        settings = get_settings()

        user = await create_test_user(
            db_session,
            email="resetlock@example.com",
            password="OldPassword123!",
        )

        for _i in range(settings.auth.max_login_attempts + 1):
            await client.post(
                "/api/auth/login",
                json={
                    "email": "resetlock@example.com",
                    "password": "WrongPassword123!",
                },
            )

        await db_session.refresh(user)
        assert user.locked_until is not None
        assert user.failed_login_attempts >= settings.auth.max_login_attempts

        reset_repo = PasswordResetRepository(db_session)
        raw_token = await reset_repo.create_token(user.id)
        await db_session.commit()

        response = await client.post(
            "/api/auth/password-reset",
            json={
                "token": raw_token,
                "new_password": "NewPassword456!",
            },
        )

        assert response.status_code == 200

        await db_session.refresh(user)
        assert user.locked_until is None
        assert user.failed_login_attempts == 0

        response = await client.post(
            "/api/auth/login",
            json={
                "email": "resetlock@example.com",
                "password": "NewPassword456!",
            },
        )

        assert response.status_code == 200
        assert response.json()["status"] == "success"

    @pytest.mark.asyncio
    async def test_password_reset_policy_validation(self, client: AsyncClient, db_session: AsyncSession):
        """A password reset enforces the password policy."""
        user = await create_test_user(
            db_session,
            email="policy@example.com",
            password="OldPassword123!",
        )

        reset_repo = PasswordResetRepository(db_session)
        raw_token = await reset_repo.create_token(user.id)
        await db_session.commit()

        response = await client.post(
            "/api/auth/password-reset",
            json={
                "token": raw_token,
                "new_password": "weak",  # Too short
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "errors" in data["detail"]
        assert any("characters" in err.lower() for err in data["detail"]["errors"])

    @pytest.mark.asyncio
    async def test_password_reset_token_stored_in_db(self, client: AsyncClient, db_session: AsyncSession):
        """A password forgot request stores a hashed token in the database."""
        from sqlalchemy import select

        from app.models.password_reset_token import PasswordResetToken

        user = await create_test_user(
            db_session,
            email="dbtest@example.com",
            password="OldPassword123!",
        )

        response = await client.post(
            "/api/auth/password-forgot",
            json={"email": "dbtest@example.com"},
        )

        assert response.status_code == 200

        stmt = select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
        result = await db_session.execute(stmt)
        tokens = result.scalars().all()

        assert len(tokens) >= 1
        token = tokens[-1]
        assert token.token_hash is not None
        assert len(token.token_hash) == 64  # SHA-256 hex is 64 chars
        assert token.expires_at is not None
        assert token.used_at is None
