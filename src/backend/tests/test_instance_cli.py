"""
The operator account commands of the CLI.

Mirrors the CLI-wiring tests of test_instance_auth.py. The CLI subcommands
are thin argparse-plus-asyncio.run shells (app/cli.py). These tests call the plain
async functions in app.auth.instance directly with the test session, and test the
argparse wiring of each subcommand.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.auth.instance import (
    InstanceAdminNotFoundError,
    InstanceAdminPasswordPolicyError,
    InstanceAdminPasswordReuseError,
    create_instance_admin,
    disable_instance_admin,
    enable_instance_admin,
    list_instance_admins,
    reset_instance_admin_totp,
    set_instance_admin_password,
)
from app.auth.passwords import verify_password

OPERATOR_EMAIL = "operator@example.com"
OPERATOR_PASSWORD = "OperatorPass123!"


@pytest.fixture(autouse=True)
def _no_process_audit_setup(monkeypatch):
    """main() joins the shared audit log. Keep the test process's own."""
    from app import cli

    monkeypatch.setattr(cli, "_prepare_audit_log", lambda: None)


# ============================================================================
# instance-admin list
# ============================================================================


@pytest.mark.asyncio
async def test_list_instance_admins_reports_totp_disabled_last_login(db_session: AsyncSession):
    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)

    admins = await list_instance_admins(db_session)

    assert [a.id for a in admins] == [admin.id]
    assert admins[0].email == OPERATOR_EMAIL
    assert admins[0].totp_confirmed_at is None
    assert admins[0].disabled_at is None
    assert admins[0].last_login_at is None


@pytest.mark.asyncio
async def test_list_instance_admins_ordered_by_email(db_session: AsyncSession):
    await create_instance_admin(db_session, "zoe@example.com", OPERATOR_PASSWORD)
    await create_instance_admin(db_session, "amy@example.com", OPERATOR_PASSWORD)

    admins = await list_instance_admins(db_session)

    assert [a.email for a in admins] == ["amy@example.com", "zoe@example.com"]


@pytest.mark.asyncio
async def test_list_instance_admins_empty(db_session: AsyncSession):
    assert await list_instance_admins(db_session) == []


# ============================================================================
# instance-admin disable / enable
# ============================================================================


@pytest.mark.asyncio
async def test_disable_then_enable_instance_admin(db_session: AsyncSession):
    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    assert admin.disabled_at is None

    disabled = await disable_instance_admin(db_session, OPERATOR_EMAIL)
    assert disabled.id == admin.id
    assert disabled.disabled_at is not None

    enabled = await enable_instance_admin(db_session, OPERATOR_EMAIL)
    assert enabled.disabled_at is None


@pytest.mark.asyncio
async def test_disable_normalizes_email_case(db_session: AsyncSession):
    await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)

    disabled = await disable_instance_admin(db_session, "Operator@Example.COM")
    assert disabled.disabled_at is not None


@pytest.mark.asyncio
async def test_disable_unknown_email_raises(db_session: AsyncSession):
    with pytest.raises(InstanceAdminNotFoundError):
        await disable_instance_admin(db_session, "ghost@example.com")


@pytest.mark.asyncio
async def test_enable_unknown_email_raises(db_session: AsyncSession):
    with pytest.raises(InstanceAdminNotFoundError):
        await enable_instance_admin(db_session, "ghost@example.com")


# ============================================================================
# instance-admin reset-totp
# ============================================================================


@pytest.mark.asyncio
async def test_reset_totp_issues_new_secret_and_clears_confirmation(db_session: AsyncSession):
    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    old_secret = admin.totp_secret
    old_session_version = admin.session_version

    # Simulate a completed enrollment before the reset.
    admin.totp_confirmed_at = datetime.now(UTC)
    admin.last_login_at = datetime.now(UTC)
    await db_session.commit()

    reset = await reset_instance_admin_totp(db_session, OPERATOR_EMAIL)

    assert reset.totp_secret is not None
    assert reset.totp_secret != old_secret
    assert reset.totp_confirmed_at is None
    # last_login_at is also cleared: the login handler's one-time provisioning
    # handout is gated on it being null, so the next login shows the URI again.
    assert reset.last_login_at is None
    # The reset ends the operator's open sessions: the session version rises in
    # the same update as the new secret.
    assert reset.session_version == old_session_version + 1


@pytest.mark.asyncio
async def test_reset_totp_unknown_email_raises(db_session: AsyncSession):
    with pytest.raises(InstanceAdminNotFoundError):
        await reset_instance_admin_totp(db_session, "ghost@example.com")


# ============================================================================
# instance-admin set-password
# ============================================================================


@pytest.mark.asyncio
async def test_set_password_hashes_new_password(db_session: AsyncSession):
    await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    new_password = "BrandNewPass456!"

    updated = await set_instance_admin_password(db_session, OPERATOR_EMAIL, new_password)

    assert verify_password(new_password, updated.password_hash)
    assert not verify_password(OPERATOR_PASSWORD, updated.password_hash)


@pytest.mark.asyncio
async def test_set_password_enforces_policy(db_session: AsyncSession):
    await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)

    with pytest.raises(InstanceAdminPasswordPolicyError) as exc_info:
        await set_instance_admin_password(db_session, OPERATOR_EMAIL, "weak")
    assert exc_info.value.errors


@pytest.mark.asyncio
async def test_set_password_unknown_email_raises(db_session: AsyncSession):
    with pytest.raises(InstanceAdminNotFoundError):
        await set_instance_admin_password(db_session, "ghost@example.com", "BrandNewPass456!")


@pytest.mark.asyncio
async def test_set_password_refuses_reuse_of_current_and_last_password(db_session: AsyncSession):
    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    second_password = "SecondPass456!"

    with pytest.raises(InstanceAdminPasswordReuseError):
        await set_instance_admin_password(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    await db_session.refresh(admin)
    assert verify_password(OPERATOR_PASSWORD, admin.password_hash)

    updated = await set_instance_admin_password(db_session, OPERATOR_EMAIL, second_password)
    assert verify_password(second_password, updated.password_hash)
    assert verify_password(OPERATOR_PASSWORD, updated.last_password_hash)

    with pytest.raises(InstanceAdminPasswordReuseError):
        await set_instance_admin_password(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)

    third_password = "ThirdPass789!"
    final = await set_instance_admin_password(db_session, OPERATOR_EMAIL, third_password)
    assert verify_password(third_password, final.password_hash)
    assert verify_password(second_password, final.last_password_hash)


# ============================================================================
# CLI wiring
# ============================================================================


def test_cli_parses_instance_admin_list():
    from app.cli import build_parser

    args = build_parser().parse_args(["instance-admin", "list"])
    assert args.command == "instance-admin"
    assert args.subcommand == "list"


def test_cli_parses_instance_admin_disable():
    from app.cli import build_parser

    args = build_parser().parse_args(["instance-admin", "disable", "--email", OPERATOR_EMAIL])
    assert args.subcommand == "disable"
    assert args.email == OPERATOR_EMAIL


def test_cli_parses_instance_admin_enable():
    from app.cli import build_parser

    args = build_parser().parse_args(["instance-admin", "enable", "--email", OPERATOR_EMAIL])
    assert args.subcommand == "enable"
    assert args.email == OPERATOR_EMAIL


def test_cli_parses_instance_admin_reset_totp():
    from app.cli import build_parser

    args = build_parser().parse_args(["instance-admin", "reset-totp", "--email", OPERATOR_EMAIL])
    assert args.subcommand == "reset-totp"
    assert args.email == OPERATOR_EMAIL


def test_cli_parses_instance_admin_set_password():
    from app.cli import build_parser

    args = build_parser().parse_args(["instance-admin", "set-password", "--email", OPERATOR_EMAIL])
    assert args.subcommand == "set-password"
    assert args.email == OPERATOR_EMAIL


def test_cli_disable_reports_not_found(monkeypatch, capsys):
    from app import cli

    async def fake_disable(email: str) -> None:
        raise cli.InstanceAdminNotFoundError(f"No instance admin with email '{email}'")

    monkeypatch.setattr(cli, "_disable_instance_admin", fake_disable)

    exit_code = cli.main(["instance-admin", "disable", "--email", OPERATOR_EMAIL])

    assert exit_code == 1
    assert OPERATOR_EMAIL in capsys.readouterr().err


def test_cli_reads_password_from_environment_for_set_password(monkeypatch):
    from app import cli

    monkeypatch.setenv("INSTANCE_ADMIN_PASSWORD", "FromEnvironment123!")

    captured: dict[str, str] = {}

    async def fake_set_password(email: str, password: str) -> None:
        captured["email"] = email
        captured["password"] = password

    monkeypatch.setattr(cli, "_set_instance_admin_password", fake_set_password)

    exit_code = cli.main(["instance-admin", "set-password", "--email", OPERATOR_EMAIL])

    assert exit_code == 0
    assert captured == {"email": OPERATOR_EMAIL, "password": "FromEnvironment123!"}


def test_cli_set_password_reports_policy_errors(monkeypatch, capsys):
    from app import cli

    monkeypatch.setenv("INSTANCE_ADMIN_PASSWORD", "weak")

    async def fake_set_password(email: str, password: str) -> None:
        raise cli.InstanceAdminPasswordPolicyError(["Password must be at least 8 characters long"])

    monkeypatch.setattr(cli, "_set_instance_admin_password", fake_set_password)

    exit_code = cli.main(["instance-admin", "set-password", "--email", OPERATOR_EMAIL])

    assert exit_code == 1
    assert "8 characters" in capsys.readouterr().err


def test_cli_set_password_reports_reuse_refusal(monkeypatch, capsys):
    from app import cli

    monkeypatch.setenv("INSTANCE_ADMIN_PASSWORD", OPERATOR_PASSWORD)

    async def fake_set_password(email: str, password: str) -> None:
        raise cli.InstanceAdminPasswordReuseError

    monkeypatch.setattr(cli, "_set_instance_admin_password", fake_set_password)

    exit_code = cli.main(["instance-admin", "set-password", "--email", OPERATOR_EMAIL])

    assert exit_code == 1
    assert "reuse" in capsys.readouterr().err.lower()


# ============================================================================
# Audit events of the account commands
# ============================================================================


@pytest.mark.asyncio
async def test_every_account_change_by_the_command_is_audited(test_engine: AsyncEngine, monkeypatch):
    """Create, disable, enable, TOTP reset and password each leave an operator log event."""
    from app import cli
    from app.routers.instance import _to_audit_log_response

    url = test_engine.url.render_as_string(hide_password=False)
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(database=SimpleNamespace(database_url=url)))
    events: list[dict] = []
    monkeypatch.setattr(cli, "record_audit_event", lambda event, **kwargs: events.append({"event": event, **kwargs}))

    email = "cli-audit@example.com"
    await cli._create_instance_admin(email, OPERATOR_PASSWORD)
    await cli._list_instance_admins()
    await cli._disable_instance_admin(email)
    await cli._enable_instance_admin(email)
    await cli._reset_instance_admin_totp(email)
    await cli._set_instance_admin_password(email, "AnotherOperatorPass456!")

    # Listing changes nothing and records nothing.
    assert [e["event"] for e in events] == [
        "instance.operator_created",
        "instance.operator_disabled",
        "instance.operator_enabled",
        "instance.operator_totp_reset",
        "instance.operator_password_set",
    ]
    for event in events:
        assert event["actor"] == {"type": "cli"}
        assert event["target"]["entity"] == "instance_admin"
        assert event["target"]["email"] == email
        assert event["organization_id"] is None

    # The operator log names the account and the server command.
    row = _to_audit_log_response({**events[0], "timestamp": "2026-11-18T10:00:00Z"})
    assert row.event == "instance.operator_created"
    assert row.target_email == email
    assert row.actor_type == "cli"
    assert row.operator_email is None


def test_only_the_changing_subcommands_join_the_audit_log(monkeypatch):
    """main() prepares the shared audit log for a change, never for the read-only list."""
    from app import cli

    prepared: list[str] = []
    monkeypatch.setattr(cli, "_prepare_audit_log", lambda: prepared.append("audit"))

    async def nothing(*_args: object) -> None:
        return None

    monkeypatch.setattr(cli, "_list_instance_admins", nothing)
    monkeypatch.setattr(cli, "_disable_instance_admin", nothing)
    assert cli.main(["instance-admin", "list"]) == 0
    assert prepared == []
    assert cli.main(["instance-admin", "disable", "--email", OPERATOR_EMAIL]) == 0
    assert prepared == ["audit"]


# ============================================================================
# image-metadata clean
# ============================================================================


def test_cli_image_metadata_clean_exists_and_is_a_dry_run_by_default(monkeypatch, capsys):
    """Only an explicit --apply makes the command write."""
    from app import cli
    from app.services.media_metadata_cleanup import MetadataCleanupResult

    assert cli.build_parser().parse_args(["image-metadata", "clean"]).apply is False

    calls: list[bool] = []

    def fake_cleanup(*, apply: bool) -> MetadataCleanupResult:
        calls.append(apply)
        return MetadataCleanupResult(applied=apply, changed=["a", "b"], undecodable=["c"], unchanged=4)

    monkeypatch.setattr(cli, "clean_stored_image_metadata", fake_cleanup)

    assert cli.main(["image-metadata", "clean"]) == 0
    dry_run = capsys.readouterr().out
    assert "Dry run, nothing was changed: 2 images would be cleaned, 1 not decodable" in dry_run
    assert "4 without metadata" in dry_run and "--apply" in dry_run

    assert cli.main(["image-metadata", "clean", "--apply"]) == 0
    assert "Done: 2 images cleaned, 1 not decodable" in capsys.readouterr().out
    assert calls == [False, True]
