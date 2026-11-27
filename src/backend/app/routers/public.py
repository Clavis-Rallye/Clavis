"""Public, unauthenticated read endpoints.

Endpoints:
    GET /api/public/runs/{code}             → {run_id, rallye_name, organization_slug, state, scheduled_for}
    GET /api/public/runs/{code}/my-team     → {run_id, team_id, team_name, team_share_code}
    GET /api/public/organizations           → {organizations: [{slug, name}]}
    POST /api/public/results/lookup         → published run result snapshot, by slug
    GET /api/public/config                  → {brand_name, bootstrap_open, single_org_slug, qa_feedback}
    GET /api/public/manifest.webmanifest    → web manifest, name and short_name from the brand name
    GET /api/public/legal/{kind}            → {kind, source, text}

``/legal/{kind}`` answers the organization's own text (optional ``org`` query,
only when that organization is active and has one), else the instance's own
text (``routers/instance.py`` writes it), else neither — never a 404 for a
known ``kind``, since "no text configured" is a normal state, not an error.

The `/runs/{code}` and `/organizations` endpoints expose only minimal projections
to prevent cross-tenant leakage via brute-forced join codes or via
probing the org list. The `/my-team` endpoint requires an authenticated caller
(via the `access_token` cookie) and returns 404 when the run is unknown so
that anonymous probing can't distinguish "no team" from "wrong code".

`/results/lookup` takes its slug in the POST body rather than the URL: a path
or query value lands in the request line that Uvicorn's access log and any
reverse proxy log record on every request, which would defeat the point of
hashing the slug at rest.
"""

from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from jwt import PyJWTError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.tokens import decode_token, is_session_that_may_act
from app.config import get_settings
from app.database import get_db
from app.exceptions import CodedHTTPException
from app.middleware.rate_limit import limiter
from app.models.organization import Organization
from app.models.run import Run
from app.repositories.instance_setting import InstanceSettingRepository
from app.repositories.organization import OrganizationRepository
from app.repositories.run import RunRepository
from app.repositories.run_result_link import RunResultLinkRepository
from app.repositories.submission import SubmissionRepository
from app.repositories.team import TeamRepository
from app.repositories.team_member import TeamMemberRepository
from app.repositories.user import UserRepository
from app.routers.schemas import ErrorResponse, PublicLegalResponse
from app.schemas.public_config import PublicConfig
from app.schemas.public_organizations import (
    PublicOrganizationListItem,
    PublicOrganizationListResponse,
)
from app.schemas.public_results import (
    PublicResultsEntry,
    PublicResultsLookupRequest,
    PublicResultsResponse,
)
from app.schemas.public_run_state import PublicRunStateResponse
from app.schemas.public_runs import PublicRunResponse
from app.schemas.public_runs_my_team import PublicRunMyTeamResponse

router = APIRouter()


@router.get(
    "/runs/{code}",
    response_model=PublicRunResponse,
    responses={
        200: {"description": "Public run metadata for the given join code"},
        404: {"description": "Run not found, or its organization is archived (error_code ORG_ARCHIVED)"},
    },
)
# 120 per minute per address. A group that waits for the start sits behind one
# address, the school's or the city's Wi-Fi, and each waiting phone asks this
# about twice a minute. At 30 per minute, five waiting phones used up the limit
# and a new arrival got 429 on its first lookup. Guessing a join code stays
# hopeless at this rate: six characters give about two billion codes.
@limiter.limit("120/minute")
async def get_public_run(
    code: str,
    request: Request,  # noqa: ARG001 — required by slowapi limiter
    db: AsyncSession = Depends(get_db),
) -> PublicRunResponse:
    """Look up a run by its join code and return the minimum a joiner needs.

    Open without sign-in, up to 120 requests per minute. The response holds the
    run id, the name, the organization slug, the state and the scheduled start,
    and nothing else. The code is not case sensitive. An unknown code answers 404.
    When the run belongs to an archived organization the 404 carries the error
    code `ORG_ARCHIVED`.
    """
    # Eager-load the organisation so we can return its slug without a second
    # round-trip. Case-insensitive match mirrors RunRepository.get_by_join_code_unscoped.
    # Archived organizations are invisible to this lookup. The active run is
    # found first. Only when there is none, a second query asks whether a run of
    # an archived organization holds the code, and the 404 then carries the
    # error_code ORG_ARCHIVED: the join says the same at its last step (403 in
    # guest.py), so the first page can say "no longer available" at once. A join
    # code is unique per organization only, so the second query is an existence
    # check that cannot raise for several rows.
    stmt = (
        select(Run)
        .where(
            func.upper(Run.join_code) == code.upper(),
            Run.is_deleted == False,  # noqa: E712
        )
        .join(Run.organization)
        .where(Organization.archived_at.is_(None))
        .options(selectinload(Run.organization))
    )
    result = await db.execute(stmt)
    run = result.scalar_one_or_none()
    if run is None:
        archived_exists = await db.scalar(
            select(
                select(Run.id)
                .join(Run.organization)
                .where(
                    func.upper(Run.join_code) == code.upper(),
                    Run.is_deleted == False,  # noqa: E712
                    Organization.archived_at.is_not(None),
                )
                .exists()
            )
        )
        if archived_exists:
            raise CodedHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Run not found",
                error_code="ORG_ARCHIVED",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )
    return PublicRunResponse(
        run_id=run.id,
        rallye_name=run.name,
        organization_slug=run.organization.slug,
        state=run.state.value,
        scheduled_for=run.scheduled_for,
    )


@router.get(
    "/runs/{code}/my-team",
    response_model=PublicRunMyTeamResponse,
    responses={
        200: {"description": "Caller's team in the run, or team_id=null if none"},
        401: {"description": "Authentication required"},
        404: {"description": "Run not found"},
    },
)
@limiter.limit("60/minute")
async def get_public_run_my_team(
    code: str,
    request: Request,  # noqa: ARG001 — required by slowapi limiter
    db: AsyncSession = Depends(get_db),
    access_token: str | None = Cookie(default=None),
) -> PublicRunMyTeamResponse:
    """Return the team of the signed-in caller in the run with the given join code.

    Up to 60 requests per minute. The onboarding uses it to skip the team search
    when the caller is in a team already. A caller without a session cookie, or
    with an invalid one, gets 401. An unknown code answers 404. A caller who has
    no team in the run gets a response with `team_id` null. So does a caller of
    another organization, an inactive account, and a token that is no current
    session, so that the route does not show who exists. The response also says
    whether the caller created the team, which is the member who joined first.
    """
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    try:
        payload = decode_token(access_token)
        user_id_raw = payload.get("sub")
        if not user_id_raw:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token.",
            )
        user_id = UUID(str(user_id_raw))
    except (PyJWTError, ValueError, KeyError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token.",
        ) from e

    run = await RunRepository.get_by_join_code_unscoped(db, code)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    user = await UserRepository.get_by_id_unscoped(db, user_id)
    # Treat cross-org users like "no team": they can't have a membership in
    # a run that belongs to a different organization, and revealing that
    # would leak org existence. A token that is not a full, current session of
    # the account (older session version, restricted purpose, locked account,
    # instance scope) is no session at all: it gets the same answer.
    # So does the kept cookie of a signed-out session.
    if (
        user is None
        or not user.is_active
        or user.organization_id != run.organization_id
        or not is_session_that_may_act(payload, access_token, user)
    ):
        return PublicRunMyTeamResponse(run_id=run.id)

    org_id = run.organization_id
    team_member_repo = TeamMemberRepository(db, org_id)
    membership = await team_member_repo.get_by_user_and_run(user.id, run.id)
    if membership is None:
        return PublicRunMyTeamResponse(run_id=run.id)

    team_repo = TeamRepository(db, org_id)
    team = await team_repo.get_by_id(membership.team_id)
    if team is None:
        # Membership row references a deleted team — treat as no team.
        return PublicRunMyTeamResponse(run_id=run.id)

    # Creator = member with the earliest joined_at (same rule routers/teams.py
    # uses for rename RBAC, since the schema does not carry created_by_user_id).
    members = await team_member_repo.list_by_team(team.id)
    creator = min(members, key=lambda m: m.joined_at)
    is_creator = creator.user_id == user.id

    return PublicRunMyTeamResponse(
        run_id=run.id,
        team_id=team.id,
        team_name=team.name,
        team_share_code=team.share_code,
        is_creator=is_creator,
    )


@router.get(
    "/runs/{run_id}/state",
    response_model=PublicRunStateResponse,
    responses={
        200: {"description": "The run's lifecycle state, for a team member"},
        401: {"description": "Authentication required"},
        404: {"description": "Run not found, or caller is not a member of a team in it"},
    },
)
@limiter.limit("60/minute")
async def get_public_run_state(
    run_id: UUID,
    request: Request,  # noqa: ARG001 — required by slowapi limiter
    db: AsyncSession = Depends(get_db),
    access_token: str | None = Cookie(default=None),
) -> PublicRunStateResponse:
    """Return the name, the state and the time stamps of a run to a member of a team in it.

    Up to 60 requests per minute. The team membership decides, not the role, so a
    guest reads the state of their run here without the organization routes,
    which refuse guests. The route only reads. It does not change the run. The
    response also says whether the grading of an ended run is complete.

    A caller without a session cookie, or with an invalid token, gets 401. Every
    other caller who is no member of a team in the run gets 404, whether the
    caller belongs to another organization or to another run, so that the route
    does not show whether the run exists. The same holds for a run of an archived
    organization.
    """
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    try:
        payload = decode_token(access_token)
        user_id_raw = payload.get("sub")
        if not user_id_raw:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token.",
            )
        user_id = UUID(str(user_id_raw))
    except (PyJWTError, ValueError, KeyError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token.",
        ) from e

    run = await RunRepository.get_by_id_unscoped(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    # An archived organization is as good as missing here too: even a team
    # member learns nothing about a run behind the flag.
    if await OrganizationRepository.is_archived(db, run.organization_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    user = await UserRepository.get_by_id_unscoped(db, user_id)
    # A cross-org or inactive caller is treated as a non-member: the run's
    # state is never disclosed (and we don't leak that the run exists). A token
    # that is not a full, current session of the account is treated the same
    # way, and so is the kept cookie of a signed-out session.
    if (
        user is None
        or not user.is_active
        or user.organization_id != run.organization_id
        or not is_session_that_may_act(payload, access_token, user)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    team_member_repo = TeamMemberRepository(db, run.organization_id)
    membership = await team_member_repo.get_by_user_and_run(user.id, run.id)
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    return PublicRunStateResponse(
        run_id=run.id,
        name=run.name,
        state=run.state,
        scheduled_for=run.scheduled_for,
        started_at=run.started_at,
        paused_at=run.paused_at,
        ended_at=run.ended_at,
        grading_complete=(
            run.state.value == "ended"
            and await SubmissionRepository(db, run.organization_id).count_unscored_by_run(run.id) == 0
        ),
    )


@router.get(
    "/organizations",
    response_model=PublicOrganizationListResponse,
    responses={
        200: {"description": "All active organizations (slug + name only)"},
    },
)
@limiter.limit("30/minute")
async def list_public_organizations(
    request: Request,  # noqa: ARG001 — required by slowapi limiter
    db: AsyncSession = Depends(get_db),
) -> PublicOrganizationListResponse:
    """List the organizations that are open for signup, as pairs of slug and name.

    Open without sign-in, up to 30 requests per minute, sorted by name. The
    registration page uses the list as the organization picker when the visitor
    arrived without a run code. Deleted organizations and archived ones are not
    listed, because an archived organization is closed for signup.
    """
    stmt = (
        select(Organization)
        .where(Organization.is_deleted == False)  # noqa: E712
        .where(Organization.archived_at.is_(None))
        .order_by(Organization.name)
    )
    result = await db.execute(stmt)
    orgs = result.scalars().all()
    return PublicOrganizationListResponse(
        organizations=[PublicOrganizationListItem(slug=o.slug, name=o.name) for o in orgs],
    )


@router.post(
    "/results/lookup",
    response_model=PublicResultsResponse,
    responses={
        200: {"description": "Published final standing for the slug"},
        404: {"description": "Unknown slug (error_code result_link_not_found)"},
        410: {"description": "Link expired or revoked (error_code result_link_expired/result_link_revoked)"},
    },
)
@limiter.limit("30/minute")
async def lookup_public_results(
    request: Request,  # noqa: ARG001 — required by slowapi limiter
    body: PublicResultsLookupRequest,
    db: AsyncSession = Depends(get_db),
) -> PublicResultsResponse:
    """Return the final standing of a run that was published with a result link.

    Open without sign-in, up to 30 requests per minute. The slug travels in the
    body and not in the URL, so it stays out of access logs. The response holds
    the run name, the end time, the language and the standing, frozen at the time
    of publishing. An unknown slug answers 404 with the error code
    `result_link_not_found`. So does a link of a run in an archived organization.
    A revoked link answers 410 `result_link_revoked`, and an expired one 410
    `result_link_expired`. The response reveals nothing else about the run, the
    organization or the participants.
    """
    link = await RunResultLinkRepository(db).get_by_slug(body.slug)
    if link is None:
        raise CodedHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Result link not found.",
            error_code="result_link_not_found",
        )

    # A link of a run in an archived organization resolves as if it never
    # existed: not-found, never revoked or expired, so the archive hides the
    # link as thoroughly as an unknown slug.
    run_org = await db.execute(select(Run.organization_id).where(Run.id == link.run_id))
    run_organization_id = run_org.scalar_one_or_none()
    if run_organization_id is not None and await OrganizationRepository.is_archived(db, run_organization_id):
        raise CodedHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Result link not found.",
            error_code="result_link_not_found",
        )
    if link.revoked_at is not None:
        raise CodedHTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This result link has been revoked.",
            error_code="result_link_revoked",
        )
    if link.expires_at <= datetime.now(UTC):
        raise CodedHTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This result link has expired.",
            error_code="result_link_expired",
        )

    snapshot = link.snapshot
    return PublicResultsResponse(
        run_name=snapshot["run_name"],
        ended_at=snapshot.get("ended_at"),
        # Older links still hold organization_name in their snapshot.
        # The answer never carries it.
        language=snapshot["language"],
        entries=[PublicResultsEntry(**entry) for entry in snapshot["entries"]],
    )


@router.get(
    "/config",
    response_model=PublicConfig,
    responses={
        200: {"description": "Instance-wide configuration readable before authentication"},
    },
)
@limiter.limit("60/minute")
async def get_public_config(
    request: Request,  # noqa: ARG001 — required by slowapi limiter
    db: AsyncSession = Depends(get_db),
) -> PublicConfig:
    """Return the instance settings that the frontend needs before anyone signs in.

    Open without sign-in, up to 60 requests per minute. The response holds:
    - `brand_name`: the product name of the instance.
    - `bootstrap_open`: true while no organization exists, or when the operator
      allows unlimited organization registrations. It is the condition that lets
      `POST /api/auth/register` create an organization. Deleted organizations
      do not count, archived ones do.
    - `single_org_slug`: the slug when exactly one organization is not archived,
      so that onboarding can skip the organization picker. Otherwise null.
    - `qa_feedback`: whether the feedback module for test sessions is on.

    The response gives no organization name, no count and no operational state.
    """
    stmt = select(Organization.slug, Organization.archived_at).where(
        Organization.is_deleted == False  # noqa: E712
    )
    result = await db.execute(stmt)
    org_rows = result.all()

    # bootstrap_open counts archived organizations too: it stays as it is,
    # mirroring the server-side bootstrap gate, and an archived organization
    # keeps that gate closed. After the first organization only the operator
    # creates organizations.
    settings = get_settings()
    active_slugs = [slug for slug, archived_at in org_rows if archived_at is None]
    return PublicConfig(
        brand_name=settings.brand_name,
        bootstrap_open=len(org_rows) == 0 or settings.auth.allow_unlimited_org_registrations,
        single_org_slug=active_slugs[0] if len(active_slugs) == 1 else None,
        qa_feedback=settings.qa_feedback.enabled,
    )


# The web manifest without its name. The frontend links this manifest, so an
# installed app carries the brand name of the instance. The fields equal the
# manifest the 0.9.2 build wrote. `id` stays out: browsers then derive the
# identity from start_url as before, and an installed app keeps its identity.
_WEB_MANIFEST_FIELDS: dict[str, object] = {
    "description": "Mobile-first city rallye platform for immigrants and newcomers",
    "theme_color": "#1D47A4",
    "background_color": "#FEF7FF",
    "display": "standalone",
    "start_url": "/",
    "scope": "/",
    "orientation": "portrait-primary",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
    ],
}


@router.get(
    "/manifest.webmanifest",
    response_class=JSONResponse,
    responses={
        200: {
            "description": "The web manifest, named after the brand",
            "content": {"application/manifest+json": {}},
        },
    },
)
# 120 per minute per address. A group behind one address, the school's or the
# city's Wi-Fi, opens the app at the same time, and each phone fetches the
# manifest.
@limiter.limit("120/minute")
async def get_public_manifest(
    request: Request,  # noqa: ARG001 — required by slowapi limiter
) -> JSONResponse:
    """Return the web manifest with the brand name as `name` and `short_name`.

    Open without sign-in, up to 120 requests per minute. It reads no database
    and takes no input. Every other field is fixed and has no `id`. The media
    type is `application/manifest+json`, and browsers may cache the answer for
    five minutes.
    """
    brand_name = get_settings().brand_name
    manifest = {"name": brand_name, "short_name": brand_name, **_WEB_MANIFEST_FIELDS}
    return JSONResponse(
        content=manifest,
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=300"},
    )


_LEGAL_KIND_TO_FIELD = {
    "imprint": "legal_imprint",
    "privacy": "legal_privacy",
    "accessibility": "legal_accessibility",
}


@router.get(
    "/legal/{kind}",
    response_model=PublicLegalResponse,
    responses={
        200: {"description": "The best-known text for this kind, with its source"},
        404: {"description": "Unknown kind (error_code LEGAL_KIND_UNKNOWN)", "model": ErrorResponse},
    },
)
@limiter.limit("60/minute")
async def get_public_legal(
    kind: str,
    request: Request,  # noqa: ARG001 — required by slowapi limiter
    org: str | None = Query(None, description="Organization slug to prefer, if it has its own text"),
    db: AsyncSession = Depends(get_db),
) -> PublicLegalResponse:
    """Return the legal text of one kind: imprint, privacy or accessibility.

    Open without sign-in, up to 60 requests per minute. The text of the
    organization named in `org` wins when that organization exists, is not
    archived and has a text of this kind. Otherwise the text of the instance
    answers, which `PUT /api/instance/legal` sets. When neither exists, `source`
    is `none` and `text` is null, which is a normal state and not an error. Only
    an unknown `kind` answers 404 with the error code `LEGAL_KIND_UNKNOWN`.
    """
    field = _LEGAL_KIND_TO_FIELD.get(kind)
    if field is None:
        raise CodedHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown legal text kind '{kind}'",
            error_code="LEGAL_KIND_UNKNOWN",
        )
    validated_kind = cast(Literal["imprint", "privacy", "accessibility"], kind)

    if org is not None:
        organization = await OrganizationRepository(db).get_by_slug(org)
        if organization is not None and organization.archived_at is None:
            org_text = getattr(organization, field)
            if org_text:
                return PublicLegalResponse(kind=validated_kind, source="organization", text=org_text)

    setting = await InstanceSettingRepository(db).get()
    instance_text = getattr(setting, field) if setting is not None else None
    if instance_text:
        return PublicLegalResponse(kind=validated_kind, source="instance", text=instance_text)

    return PublicLegalResponse(kind=validated_kind, source="none", text=None)
