"""
Command-line interface for instance administration.

Runnable as::

    python -m app.cli instance-admin create --email <e>
    python -m app.cli instance-admin list
    python -m app.cli instance-admin disable --email <e>
    python -m app.cli instance-admin enable --email <e>
    python -m app.cli instance-admin reset-totp --email <e>
    python -m app.cli instance-admin set-password --email <e>
    python -m app.cli demo-content load --org-slug <slug> [--replace] [--enable-languages]
    python -m app.cli geo import --bbox <south,west,north,east> | --file <overpass.json>
    python -m app.cli geo status
    python -m app.cli image-metadata clean [--apply]

The password comes from ``INSTANCE_ADMIN_PASSWORD`` or, if unset, from a ``getpass`` prompt. This
command is the only way to create an operator account (no HTTP route does), and the only way to
disable, enable, reset the TOTP enrollment of, or reset the password of one.

The database work of each subcommand is a plain async function that the tests call with their own
session: ``create_instance_admin``, ``list_instance_admins``, ``disable_instance_admin``,
``enable_instance_admin``, ``reset_instance_admin_totp`` and ``set_instance_admin_password`` of
:mod:`app.auth.instance`, and :func:`app.demo.loader.load_demo_content`. The demo-content command does
not create the organization. The operator console does, and the command adds only the content.

A command that records an audit event writes the API's audit log as a second process, so it first
configures logging and recovers the audit chain, like the API's lifespan. Every instance-admin
subcommand that changes an account records an ``instance.operator_*`` event, so the operator log
shows who added, disabled, enabled or reset an operator, and when. The actor is ``{"type": "cli"}``:
the command runs on the server and knows no person.

``image-metadata clean`` removes the metadata of images stored before uploads were cleaned, with its
logic in :mod:`app.services.media_metadata_cleanup`. It is a dry run unless ``--apply`` is given, and
nothing but this command starts it: an update leaves the stored content untouched, and the operator
decides.
"""

import argparse
import asyncio
import getpass
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx
from fastapi import HTTPException
from minio.error import S3Error
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.audit_handler import get_handler, init_audit_chain
from app.auth.instance import (
    InstanceAdminExistsError,
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
from app.config import get_settings
from app.demo.loader import (
    DemoContentExistsError,
    DemoLoadResult,
    DemoOrganizationArchivedError,
    DemoOrganizationNotFoundError,
    load_demo_content,
)
from app.logging import ensure_logging_configured
from app.models.instance_admin import InstanceAdmin
from app.services.audit import record_audit_event
from app.services.geo_import import build_rows, overpass_query
from app.services.geo_search import index_size, replace_index
from app.services.geo_upstream import _user_agent
from app.services.media_metadata_cleanup import clean_stored_image_metadata


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser (argparse, no extra dependency)."""
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Clavis instance administration CLI",
    )
    command = parser.add_subparsers(dest="command", required=True)

    instance_admin = command.add_parser(
        "instance-admin",
        help="Manage instance operator accounts",
    )
    instance_admin_command = instance_admin.add_subparsers(dest="subcommand", required=True)

    create = instance_admin_command.add_parser(
        "create",
        help="Create an instance operator account (password via INSTANCE_ADMIN_PASSWORD or prompt)",
    )
    create.add_argument(
        "--email",
        required=True,
        help="Email address of the new operator (unique)",
    )

    instance_admin_command.add_parser(
        "list",
        help="List every operator account (email, TOTP confirmed, disabled, last login)",
    )

    disable = instance_admin_command.add_parser(
        "disable",
        help="Disable an operator account (it cannot sign in, and an open session ends)",
    )
    disable.add_argument("--email", required=True, help="Email address of the operator to disable")

    enable = instance_admin_command.add_parser(
        "enable",
        help="Re-enable a disabled operator account",
    )
    enable.add_argument("--email", required=True, help="Email address of the operator to enable")

    reset_totp = instance_admin_command.add_parser(
        "reset-totp",
        help=(
            "Rotate an operator's TOTP secret: ends the operator's open sessions, "
            "the next login hands out a new provisioning URI"
        ),
    )
    reset_totp.add_argument("--email", required=True, help="Email address of the operator to reset")

    set_password = instance_admin_command.add_parser(
        "set-password",
        help="Set an operator's password (via INSTANCE_ADMIN_PASSWORD or prompt), no current password needed",
    )
    set_password.add_argument("--email", required=True, help="Email address of the operator")

    demo_content = command.add_parser(
        "demo-content",
        help="Load the bundled demo rallye into an existing organization",
    )
    demo_content_command = demo_content.add_subparsers(dest="subcommand", required=True)

    load = demo_content_command.add_parser(
        "load",
        help="Create the demo stations, challenges and rallye in an organization made in the operator console",
    )
    load.add_argument(
        "--org-slug",
        required=True,
        help="Slug of the target organization (must exist and not be archived)",
    )
    load.add_argument(
        "--replace",
        action="store_true",
        help="Soft-delete an earlier demo load first; existing runs keep their frozen snapshots",
    )
    load.add_argument(
        "--enable-languages",
        action="store_true",
        help="Also add the languages of the demo content (German, English) that the organization lacks",
    )

    geo = command.add_parser(
        "geo",
        help="Manage the regional place index of the address search",
    )
    geo_command = geo.add_subparsers(dest="subcommand", required=True)
    geo_import = geo_command.add_parser(
        "import",
        help="Replace the index with the named places, streets and addresses of a region from OpenStreetMap",
    )
    geo_source = geo_import.add_mutually_exclusive_group(required=True)
    geo_source.add_argument(
        "--bbox",
        help="Region as south,west,north,east in degrees. The data comes from the Overpass API",
    )
    geo_source.add_argument(
        "--file",
        help="An Overpass API answer saved as JSON, for a server without access to the internet",
    )
    geo_import.add_argument(
        "--overpass-url",
        default=None,
        help="Overpass API endpoint for this run. Default: GEOCODER_OVERPASS_URL, else the public instance of FOSSGIS e.V.",
    )
    geo_command.add_parser("status", help="Print the number of rows in the index")

    image_metadata = command.add_parser(
        "image-metadata",
        help="Clean the metadata of images stored before uploads were cleaned (EXIF, GPS position, device)",
    )
    image_metadata_command = image_metadata.add_subparsers(dest="subcommand", required=True)
    clean = image_metadata_command.add_parser(
        "clean",
        help="List the stored images that carry metadata. Changes nothing unless --apply is given",
    )
    clean.add_argument(
        "--apply",
        action="store_true",
        help="Write each listed image back without its metadata, under the same key. Default: dry run",
    )

    return parser


def _prepare_audit_log() -> None:
    """Join the API's audit log as a second writer that never rotates.

    Without this the event goes to an unconfigured logger and never reaches the file. The API owns
    rotation: if this command rotated too, one of the two would write into the archived segment.
    """
    ensure_logging_configured()
    init_audit_chain()
    handler = get_handler()
    if handler is not None:
        handler.rotation_owner = False


def _audit_operator_change(event: str, admin: InstanceAdmin) -> None:
    record_audit_event(
        event=event,
        actor={"type": "cli"},
        target={"entity": "instance_admin", "id": str(admin.id), "email": admin.email},
        organization_id=None,
    )


def _resolve_password() -> str:
    """Read the password from the environment or prompt for it."""
    env_password = os.environ.get("INSTANCE_ADMIN_PASSWORD")
    if env_password:
        return env_password
    return getpass.getpass("Password for the new instance admin: ")


async def _create_instance_admin(email: str, password: str) -> None:
    engine = create_async_engine(get_settings().database.database_url)
    try:
        session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        async with session_maker() as session:
            admin = await create_instance_admin(session, email, password)
            _audit_operator_change("instance.operator_created", admin)
            # Never print the TOTP secret: the first login hands out the provisioning URI for enrollment.
            print(f"Instance admin created: {admin.email} (id {admin.id})")
            print("The first login will return the TOTP provisioning URI to scan.")
    finally:
        await engine.dispose()


async def _list_instance_admins() -> None:
    engine = create_async_engine(get_settings().database.database_url)
    try:
        session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        async with session_maker() as session:
            admins = await list_instance_admins(session)
            if not admins:
                print("No instance admins.")
                return
            for admin in admins:
                totp = "confirmed" if admin.totp_confirmed_at is not None else "pending"
                disabled = "yes" if admin.disabled_at is not None else "no"
                last_login = admin.last_login_at.isoformat() if admin.last_login_at else "never"
                print(f"{admin.email}\ttotp={totp}\tdisabled={disabled}\tlast_login={last_login}")
    finally:
        await engine.dispose()


async def _disable_instance_admin(email: str) -> None:
    engine = create_async_engine(get_settings().database.database_url)
    try:
        session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        async with session_maker() as session:
            admin = await disable_instance_admin(session, email)
            _audit_operator_change("instance.operator_disabled", admin)
            print(f"Instance admin disabled: {admin.email}")
    finally:
        await engine.dispose()


async def _enable_instance_admin(email: str) -> None:
    engine = create_async_engine(get_settings().database.database_url)
    try:
        session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        async with session_maker() as session:
            admin = await enable_instance_admin(session, email)
            _audit_operator_change("instance.operator_enabled", admin)
            print(f"Instance admin enabled: {admin.email}")
    finally:
        await engine.dispose()


async def _reset_instance_admin_totp(email: str) -> None:
    """Rotate the operator's TOTP secret on the configured database; the open sessions end with it."""
    engine = create_async_engine(get_settings().database.database_url)
    try:
        session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        async with session_maker() as session:
            admin = await reset_instance_admin_totp(session, email)
            _audit_operator_change("instance.operator_totp_reset", admin)
            print(
                f"TOTP reset for {admin.email}: open sessions are ended, "
                "the next login will return a new provisioning URI."
            )
    finally:
        await engine.dispose()


async def _set_instance_admin_password(email: str, password: str) -> None:
    engine = create_async_engine(get_settings().database.database_url)
    try:
        session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        async with session_maker() as session:
            admin = await set_instance_admin_password(session, email, password)
            _audit_operator_change("instance.operator_password_set", admin)
            print(f"Password set for {admin.email}")
    finally:
        await engine.dispose()


async def _load_demo_content(org_slug: str, replace: bool, enable_languages: bool = False) -> DemoLoadResult:
    engine = create_async_engine(get_settings().database.database_url)
    try:
        session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        async with session_maker() as session:
            return await load_demo_content(session, org_slug, replace, enable_languages)
    finally:
        await engine.dispose()


def _parse_bbox(value: str) -> tuple[float, float, float, float]:
    """south,west,north,east as four numbers, or a ValueError that says what is wrong."""
    parts = [float(part) for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError("--bbox needs four numbers: south,west,north,east")
    south, west, north, east = parts
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise ValueError("--bbox must be south,west,north,east with south < north and west < east")
    if (north - south) * (east - west) > 1.0:
        raise ValueError("--bbox is larger than one square degree. Import a city and its surroundings, not a state")
    return south, west, north, east


# A busy Overpass instance answers with these, or runs out of time. A later try often works.
_OVERPASS_RETRY_STATUS = frozenset({429, 502, 503, 504})
_OVERPASS_ATTEMPTS = 4
_OVERPASS_FIRST_PAUSE_SECONDS = 20.0


def _fetch_overpass(
    url: str,
    bbox: tuple[float, float, float, float],
    *,
    attempts: int = _OVERPASS_ATTEMPTS,
    first_pause: float = _OVERPASS_FIRST_PAUSE_SECONDS,
    sleep: Callable[[float], None] | None = None,
) -> list[dict[str, Any]]:
    """Download the region from the Overpass API. The only outside request of the import.

    The public instance is often busy for a minute or two, so a busy answer or a timeout leads to
    another attempt after a pause of 20, 40 and 80 s, four attempts in all. Every other error ends the
    import at once. Each attempt prints one line, so the operator sees that the import waits.
    """
    wait = sleep or time.sleep
    pause = first_pause
    for attempt in range(1, attempts + 1):
        print(f"Overpass attempt {attempt} of {attempts}: {url}", flush=True)
        try:
            response = httpx.post(
                url,
                data={"data": overpass_query(*bbox)},
                headers={"User-Agent": _user_agent("geo import")},
                timeout=600,
            )
        except httpx.TimeoutException as e:
            if attempt == attempts:
                raise
            reason = f"no answer in time ({type(e).__name__})"
        else:
            if response.status_code not in _OVERPASS_RETRY_STATUS or attempt == attempts:
                response.raise_for_status()
                elements: list[dict[str, Any]] = response.json()["elements"]
                return elements
            reason = f"HTTP {response.status_code}, the instance is busy"
        print(f"Overpass attempt {attempt} of {attempts} failed: {reason}. Next attempt in {pause:.0f} s", flush=True)
        wait(pause)
        pause *= 2
    raise AssertionError("unreachable: the last attempt returns or raises")


async def _import_geo_index(elements: list[dict[str, Any]]) -> int:
    """Replace the index on the configured database in one transaction."""
    rows = build_rows(elements)
    engine = create_async_engine(get_settings().database.database_url)
    try:
        session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        async with session_maker() as session:
            count = await replace_index(session, rows)
            await session.commit()
            return count
    finally:
        await engine.dispose()


async def _geo_index_size() -> int:
    engine = create_async_engine(get_settings().database.database_url)
    try:
        session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        async with session_maker() as session:
            return await index_size(session)
    finally:
        await engine.dispose()


def _run_geo(args: argparse.Namespace) -> int:
    if args.subcommand == "status":
        print(f"Place index: {asyncio.run(_geo_index_size())} rows")
        return 0
    try:
        if args.file:
            with open(args.file, encoding="utf-8") as handle:
                elements = json.load(handle)["elements"]
        else:
            url = args.overpass_url or get_settings().geocoder.overpass_endpoint
            elements = _fetch_overpass(url, _parse_bbox(args.bbox))
    except (ValueError, KeyError, OSError, httpx.HTTPError) as e:
        print(f"Import failed, the index is unchanged: {e}", file=sys.stderr)
        return 2
    if not elements:
        print("Import failed, the index is unchanged: the region holds no data", file=sys.stderr)
        return 2
    count = asyncio.run(_import_geo_index(elements))
    print(f"Place index replaced: {count} rows from {len(elements)} OpenStreetMap objects")
    print("Data: OpenStreetMap contributors, ODbL. The API reads the new index within a minute.")
    return 0


def _run_image_metadata(args: argparse.Namespace) -> int:
    """List, or with --apply rewrite, the stored images that carry metadata."""
    try:
        result = clean_stored_image_metadata(apply=args.apply)
    except S3Error as e:
        print(f"The object store failed, the run stopped: {e}", file=sys.stderr)
        print("Images named above as cleaned stay cleaned. The command can be run again.", file=sys.stderr)
        return 2
    counts = f"{len(result.undecodable)} not decodable and left as they are, {result.unchanged} without metadata"
    if result.applied:
        print(f"Done: {len(result.changed)} images cleaned, {counts}.")
    else:
        print(f"Dry run, nothing was changed: {len(result.changed)} images would be cleaned, {counts}.")
        if result.changed:
            print("Run the command again with --apply to clean them.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch to the selected command."""
    args = build_parser().parse_args(argv)

    if args.command == "geo":
        return _run_geo(args)

    if args.command == "image-metadata":
        return _run_image_metadata(args)

    if args.command == "demo-content":
        if args.subcommand != "load":
            # Unreachable: argparse enforces the subcommand chain (required=True).
            build_parser().error("unknown subcommand")
            return 2

        # The load records demo_content.loaded in the API's audit log.
        _prepare_audit_log()
        try:
            result = asyncio.run(_load_demo_content(args.org_slug, args.replace, args.enable_languages))
        except (DemoOrganizationNotFoundError, DemoOrganizationArchivedError, DemoContentExistsError) as e:
            print(str(e), file=sys.stderr)
            return 2
        except HTTPException as e:
            # validate_scoring_rules refused the bundled file.
            print(str(e.detail), file=sys.stderr)
            return 2
        print(
            f"Demo content loaded: {result.stations} stations, {result.challenges} challenges, {result.rallyes} rallye"
        )
        if result.languages_added:
            print(f"Languages added to the organization: {', '.join(result.languages_added)}")
        return 0

    if args.command != "instance-admin":
        # Unreachable: argparse enforces the subcommand chain (required=True).
        build_parser().error("unknown command")
        return 2

    if args.subcommand != "list":
        # Every other subcommand changes an account and records it.
        _prepare_audit_log()

    if args.subcommand == "create":
        password = _resolve_password()
        try:
            asyncio.run(_create_instance_admin(args.email, password))
        except InstanceAdminPasswordPolicyError as e:
            print("Password does not meet the policy requirements:", file=sys.stderr)
            for error in e.errors:
                print(f"  - {error}", file=sys.stderr)
            return 1
        except InstanceAdminExistsError as e:
            print(str(e), file=sys.stderr)
            return 1
        return 0

    if args.subcommand == "list":
        asyncio.run(_list_instance_admins())
        return 0

    if args.subcommand == "disable":
        try:
            asyncio.run(_disable_instance_admin(args.email))
        except InstanceAdminNotFoundError as e:
            print(str(e), file=sys.stderr)
            return 1
        return 0

    if args.subcommand == "enable":
        try:
            asyncio.run(_enable_instance_admin(args.email))
        except InstanceAdminNotFoundError as e:
            print(str(e), file=sys.stderr)
            return 1
        return 0

    if args.subcommand == "reset-totp":
        try:
            asyncio.run(_reset_instance_admin_totp(args.email))
        except InstanceAdminNotFoundError as e:
            print(str(e), file=sys.stderr)
            return 1
        return 0

    if args.subcommand == "set-password":
        password = _resolve_password()
        try:
            asyncio.run(_set_instance_admin_password(args.email, password))
        except InstanceAdminPasswordPolicyError as e:
            print("Password does not meet the policy requirements:", file=sys.stderr)
            for error in e.errors:
                print(f"  - {error}", file=sys.stderr)
            return 1
        except InstanceAdminPasswordReuseError as e:
            print(str(e), file=sys.stderr)
            return 1
        except InstanceAdminNotFoundError as e:
            print(str(e), file=sys.stderr)
            return 1
        return 0

    # Unreachable: argparse enforces the subcommand chain (required=True).
    build_parser().error("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
