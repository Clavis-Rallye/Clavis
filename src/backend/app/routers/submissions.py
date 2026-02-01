"""
Submission endpoints (nested under runs).

Provides submission management operations within a run context with RBAC enforcement,
organization scoping via URL slug, audit logging, and team validation.

All endpoints require authentication and enforce role-based access control:
- Create submission: participant+ can submit answers (only in active runs, one per challenge per team)
- Replace submission: team members can replace their team's unscored submission (only in active runs)
- List submissions: coordinator+ can view all submissions, participant can view own team only
- Get submission: coordinator+ can view any submission, participant can view own team only

All endpoints are nested under runs: /api/orgs/{slug}/runs/{run_id}/submissions/
"""

from typing import Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from minio.error import S3Error
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_org_from_slug
from app.auth.rbac import RoleLevel, require_role
from app.client_address import client_address
from app.config import settings
from app.database import get_db
from app.exceptions import CodedHTTPException
from app.models.challenge import ChallengeType
from app.models.feed_event import FeedEventType
from app.models.organization import Organization
from app.models.run import RunState
from app.models.run_challenge import RunChallenge
from app.models.submission import Submission
from app.models.user import User
from app.repositories.feed_event import FeedEventRepository
from app.repositories.run import RunRepository
from app.repositories.run_challenge import RunChallengeRepository
from app.repositories.score import ScoreRepository
from app.repositories.submission import SubmissionRepository
from app.repositories.team import TeamRepository
from app.repositories.team_member import TeamMemberRepository
from app.routers.schemas import (
    ErrorResponse,
    SubmissionCreateRequest,
    SubmissionListResponse,
    SubmissionResponse,
    SubmissionUpdateRequest,
)
from app.routers.teams import run_not_active_error_code
from app.services.audit import actor_from_user, record_audit_event
from app.services.auto_score import try_auto_score
from app.services.notify import notify_feed_event
from app.services.retention_tasks import submission_media_keys, unreferenced_variant_keys, variant_base_key
from app.storage import delete_file, get_minio_client

router = APIRouter()

logger = structlog.get_logger(__name__)

# The duplicate answer carries a code that the frontend maps to a translated
# message, instead of showing the raw API detail. One builder serves both guards
# of create_submission, so the pre-check and the unique-index race answer
# identically.
TEAM_ALREADY_SUBMITTED = "TEAM_ALREADY_SUBMITTED"


def team_already_submitted_error() -> CodedHTTPException:
    """The single 422 both duplicate-submission guards of create_submission raise."""
    return CodedHTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Dein Team hat diese Challenge bereits beantwortet.",
        error_code=TEAM_ALREADY_SUBMITTED,
    )


def verify_photo_url_exists(photo_url: str) -> None:
    """Raise 422 if the object key does not exist in the S3 store."""
    try:
        client = get_minio_client()
        bucket_name = settings.storage.bucket
        client.stat_object(bucket_name=bucket_name, object_name=photo_url)
    except S3Error as e:
        if e.code == "NoSuchKey":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"File not found in storage: {photo_url}",
            ) from None
        # Any other S3 error answers 500.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to verify file: {e!s}",
        ) from None


MEDIA_KEY_NOT_OWNED = "MEDIA_KEY_NOT_OWNED"


def verify_media_key_owned(object_key: str, team_upload_prefix: str) -> None:
    """
    Raise 422 with error_code MEDIA_KEY_NOT_OWNED if a media object key is outside
    the team's own upload prefix ``{org_id}/{run_id}/{team_id}/``.

    The existence checks (``verify_photo_url_exists`` / ``verify_media_url_kind``)
    only prove that *some* object exists. They do not stop a team from naming
    another team's upload, or an unrelated object such as a station image, as
    their own answer. The prefix is the one every upload of the team gets (see
    app/routers/files.py).
    """
    if not object_key.startswith(team_upload_prefix):
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Media key '{object_key}' is not under your team's upload prefix",
            error_code=MEDIA_KEY_NOT_OWNED,
        )


MEDIA_KEY_IS_VARIANT = "MEDIA_KEY_IS_VARIANT"


def verify_media_key_not_variant(object_key: str) -> None:
    """
    Raise 422 with error_code MEDIA_KEY_IS_VARIANT if a media key names a generated
    WebP variant of a photo that exists, rather than a photo of its own.

    The delete paths (the LK_B retention sweep, the replace cleanup, the orphan sweep)
    each tie a variant's fate to its original's. A submission that references a
    variant directly could therefore have a live variant object deleted. Instead
    of hardening every delete path, validation closes the root: a key shaped like
    ``<base>.thumb.webp`` or ``<base>.display.webp`` is refused when ``<base>``
    exists in storage, because it is that photo's derived variant and not an
    uploadable answer.

    A key that only *looks* like a variant but has no base object (a participant's
    own upload named ``x.thumb.webp``) stays allowed. Nothing derived it, so
    nothing ties its lifecycle to another object's.
    """
    base_key = variant_base_key(object_key)
    if base_key is None:
        return
    try:
        get_minio_client().stat_object(settings.storage.bucket, base_key)
    except S3Error:
        return  # No base object under that key, so it is not a variant.
    raise CodedHTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=f"Media key '{object_key}' is a generated variant, not an uploadable photo",
        error_code=MEDIA_KEY_IS_VARIANT,
    )


def verify_submission_media_owned(content: dict[str, Any], team_upload_prefix: str) -> None:
    """
    Verify that every media key named anywhere in a submission's content sits
    under the team's own upload prefix, and names no generated variant.

    ``content`` is an open dict, not shaped by the challenge type: a
    type-specific check that only looks at the field its own type expects
    (e.g. PHOTO checking only ``photo_url``) would miss a foreign key riding
    along in another media field, such as ``{"text": "answer", "photo_url":
    "<other team>/..."}`` on a TEXT answer or a stray ``audio_url`` on a PHOTO
    one. This runs first, over every field ``submission_media_keys`` recognizes
    (the scalar fields plus every ``media_urls`` entry), before the
    challenge-type-specific validation.

    Raises 422 MEDIA_KEY_NOT_OWNED on the first key outside the team's own upload
    prefix, or 422 MEDIA_KEY_IS_VARIANT on the first key that is a generated
    variant of a photo.
    """
    for object_key in submission_media_keys(content):
        verify_media_key_owned(object_key, team_upload_prefix)
        verify_media_key_not_variant(object_key)


def verify_media_url_kind(object_key: str, kind: Literal["audio", "video"]) -> None:
    """
    Raise 422 if the stored object is missing or its content type is not ``<kind>/*``.

    Called after ``verify_photo_url_exists``. It stats the object again to
    cross-check the stored MIME type, so an image cannot ride in under a media
    field. The storage allowlist accepts audio and video alike, which is why the
    per-type check is needed here.
    """
    try:
        client = get_minio_client()
        bucket_name = settings.storage.bucket
        stat = client.stat_object(bucket_name=bucket_name, object_name=object_key)
    except S3Error as e:
        if e.code == "NoSuchKey":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"File not found in storage: {object_key}",
            ) from None
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to verify file: {e!s}",
        ) from None

    content_type = stat.content_type or ""
    if not content_type.startswith(f"{kind}/"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"{kind.upper()} challenge submission '{kind}_url' must reference "
                f"a {kind} file, got content type '{content_type}'"
            ),
        )


async def _awarded_points(db: AsyncSession, org_id: UUID, submission_id: UUID) -> tuple[int | None, int | None]:
    """The (points, max_points) on a submission's score, or (None, None) if unscored."""
    score = await ScoreRepository(db, organization_id=org_id).get_by_submission(submission_id)
    return (score.points, score.max_points) if score else (None, None)


async def _other_submissions_media_keys(
    db: AsyncSession,
    org_id: UUID,
    run_id: UUID,
    exclude_submission_id: UUID,
) -> set[str]:
    """
    Object-storage keys the run's other submissions still reference.

    The replace cleanup may delete an old answer's object only once nothing
    points at it anymore. This is the "does any other answer still point at it"
    half of that guard. Media fields come from ``retention_tasks``, the same list
    the retention sweep uses, so the two callers cannot disagree.
    """
    stmt = select(Submission).where(
        Submission.organization_id == org_id,
        Submission.run_id == run_id,
        Submission.id != exclude_submission_id,
    )
    others = (await db.execute(stmt)).scalars().all()
    keys: set[str] = set()
    for other in others:
        keys.update(submission_media_keys(other.content or {}))
    return keys


TEXT_ANSWER_MAX_LENGTH = 5000


def _has_fallback_text(content: dict[str, Any], kind: str, media_field: str) -> bool:
    """
    Check the text that a photo, video or audio answer may carry, and say whether it
    stands in for the medium.

    A text answer is always possible as a fallback when the camera or the microphone
    does not work. A team may send the medium, the text, or both. A text that is
    present must be a non-empty string within the bound of a TEXT answer.

    Returns True when the answer has a text and no medium, so the caller skips the
    checks of the medium. With a medium present the caller checks it as before.
    """
    if "text" not in content:
        return False
    text = content["text"]
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{kind} challenge submission 'text' field must be a non-empty string",
        )
    if len(text) > TEXT_ANSWER_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{kind} challenge submission 'text' field exceeds {TEXT_ANSWER_MAX_LENGTH} character limit",
        )
    return media_field not in content


def validate_submission_content(
    challenge_type: ChallengeType,
    content: dict[str, Any],
) -> None:
    """
    Raise 422 if the content does not match the challenge type.

    Media-key ownership is checked separately, over the whole content, by
    ``verify_submission_media_owned`` before this runs (see create_submission and
    replace_submission).
    """
    if challenge_type == ChallengeType.TEXT:
        if "text" not in content:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="TEXT challenge submission requires 'text' field",
            )
        text = content["text"]
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="TEXT challenge submission requires non-empty 'text' field",
            )
        if len(text) > 5000:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="TEXT challenge submission 'text' field exceeds 5000 character limit",
            )

    elif challenge_type == ChallengeType.PHOTO:
        if _has_fallback_text(content, "PHOTO", "photo_url"):
            return
        if "photo_url" not in content:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="PHOTO challenge submission requires 'photo_url' field or a 'text' answer",
            )
        photo_url = content["photo_url"]
        if not isinstance(photo_url, str) or not photo_url.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="PHOTO challenge submission requires non-empty 'photo_url' field",
            )
        verify_photo_url_exists(photo_url)

    elif challenge_type == ChallengeType.QUIZ:
        if "selected_option" not in content:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="QUIZ challenge submission requires 'selected_option' field",
            )
        selected_option = content["selected_option"]
        if not isinstance(selected_option, str) or not selected_option.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="QUIZ challenge submission requires non-empty 'selected_option' field",
            )

    elif challenge_type == ChallengeType.QR_SCAN:
        if "scanned_value" not in content:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="QR_SCAN challenge submission requires 'scanned_value' field",
            )
        scanned_value = content["scanned_value"]
        if not isinstance(scanned_value, str) or not scanned_value.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="QR_SCAN challenge submission requires non-empty 'scanned_value' field",
            )

    elif challenge_type == ChallengeType.CREATIVE:
        has_text = "text" in content and isinstance(content["text"], str) and content["text"].strip()
        has_photo = "photo_url" in content and isinstance(content["photo_url"], str) and content["photo_url"].strip()
        has_audio = "audio_url" in content and isinstance(content["audio_url"], str) and content["audio_url"].strip()

        if not (has_text or has_photo or has_audio):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="CREATIVE challenge submission requires at least one of: 'text', 'photo_url', or 'audio_url'",
            )

        if has_photo:
            verify_photo_url_exists(content["photo_url"])

        if has_audio:
            verify_photo_url_exists(content["audio_url"])

    elif challenge_type == ChallengeType.VIDEO:
        if _has_fallback_text(content, "VIDEO", "video_url"):
            return
        if "video_url" not in content:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="VIDEO challenge submission requires 'video_url' field or a 'text' answer",
            )
        video_url = content["video_url"]
        if not isinstance(video_url, str) or not video_url.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="VIDEO challenge submission requires non-empty 'video_url' field",
            )
        verify_photo_url_exists(video_url)
        # An image can be uploaded under the same field, so check the content type too.
        verify_media_url_kind(video_url, "video")

    elif challenge_type == ChallengeType.AUDIO:
        if _has_fallback_text(content, "AUDIO", "audio_url"):
            return
        if "audio_url" not in content:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="AUDIO challenge submission requires 'audio_url' field or a 'text' answer",
            )
        audio_url = content["audio_url"]
        if not isinstance(audio_url, str) or not audio_url.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="AUDIO challenge submission requires non-empty 'audio_url' field",
            )
        verify_photo_url_exists(audio_url)
        verify_media_url_kind(audio_url, "audio")


async def _try_auto_score_and_notify(
    db: AsyncSession,
    submission: Submission,
    run_challenge: RunChallenge,
    org: Organization,
) -> bool:
    """
    Attempt auto-scoring for a submission, emit the feed event and the WebSocket
    notification, and return True if an auto-score was created.

    Shared by create_submission and replace_submission so both paths behave identically.
    """
    # An automatic score is a grading: it takes the shared lock on the run row
    # as the three score routes do (``RunRepository.share_row``). The answer is
    # committed, so this is a new transaction and the lock is its first row lock,
    # held to the commit of
    # the score below. Without it the score could commit between the count of
    # the ungraded answers and the commit of an end of the run, and the notice
    # of the end would say "grading is still under way" beside a complete
    # grading. Only asked for when a score may be written. The state is not
    # looked at: a score for a stored answer is written in every state, as a
    # grading by hand is.
    if (run_challenge.scoring_rules or {}).get("auto_approve", False):
        await RunRepository(db, organization_id=org.id).share_row(submission.run_id)
    auto_score = await try_auto_score(db, submission, run_challenge, org.id)
    if auto_score is None:
        return False

    await db.commit()
    await db.refresh(auto_score)

    # Only QUIZ and QR_SCAN reach an auto-score, and the line names the check that ran:
    # a quiz answer is scored, a QR scan is checked.
    if run_challenge.challenge_type == ChallengeType.QR_SCAN:
        feed_content = {
            "de": f"QR-Code automatisch geprüft: {auto_score.points}/{auto_score.max_points} Punkte",
            "en": f"QR code checked automatically: {auto_score.points}/{auto_score.max_points} points",
        }
    else:
        feed_content = {
            "de": f"Quiz automatisch bewertet: {auto_score.points}/{auto_score.max_points} Punkte",
            "en": f"Quiz auto-scored: {auto_score.points}/{auto_score.max_points} points",
        }
    feed_event_repo = FeedEventRepository(db, organization_id=org.id)
    feed_event = await feed_event_repo.create(
        run_id=submission.run_id,
        event_type=FeedEventType.SYSTEM,
        content=feed_content,
        author_id=None,  # System-generated event
        # A score is the business of the team that answered, not of the
        # other teams in the run. Staff see it regardless.
        target_team_id=submission.team_id,
    )
    await db.commit()
    await db.refresh(feed_event)

    await notify_feed_event(db, feed_event)

    return True


async def _refuse_unless_active_under_shared_lock(run_repo: RunRepository, run_id: UUID, act: str, done: str) -> None:
    """
    Take the shared lock on the run row and decide on the state it has under
    the lock. Refuses with the answer the unlocked check of the route gives:
    404 for a run that is gone, 422 with the run-state error code for a run
    that is not active.

    The first row lock of the transaction, held to its commit. See
    ``RunRepository.share_row``.
    """
    state = await run_repo.share_row(run_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )
    if state != RunState.ACTIVE:
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cannot {act} when run is in {state} state. Submissions can only be {done} in active runs.",
            error_code=run_not_active_error_code(state),
        )


@router.post(
    "/",
    response_model=SubmissionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Submission created successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Run not found, or the caller is in no team of this run", "model": ErrorResponse},
        422: {
            "description": (
                "Run not in active state, challenge not in this run, duplicate submission "
                "(error_code TEAM_ALREADY_SUBMITTED), or invalid content for challenge type"
            ),
            "model": ErrorResponse,
        },
    },
)
async def create_submission(
    run_id: UUID,
    request: Request,
    body: SubmissionCreateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("guest")),
) -> SubmissionResponse:
    """
    Create the answer of the caller's team to one challenge of an active run.

    Open to every signed-in role, guests included. The caller must be a member
    of a team in the run. A team has one answer per challenge.

    Content depends on the challenge type:
    - TEXT: 'text', a non-empty string of at most 5000 characters.
    - PHOTO: 'photo_url', an object that exists in storage, or 'text' instead.
    - QUIZ: 'selected_option', a non-empty string.
    - QR_SCAN: 'scanned_value', a non-empty string.
    - CREATIVE (only in older runs): at least one of 'text', 'photo_url' and
      'audio_url'. The photo and audio objects must exist in storage.
    - VIDEO: 'video_url', an object of content type video/*, or 'text' instead.
    - AUDIO: 'audio_url', an object of content type audio/*, or 'text' instead.

    'text' is the fallback of a PHOTO, VIDEO or AUDIO challenge, for example
    when the camera or microphone does not work. It follows the rule of TEXT.
    A 'text' without the medium field is the whole answer. With both, the
    medium is checked as well.

    Every media key in every media field of the content must lie under the
    team's own upload prefix and must not be a generated WebP variant of a
    photo. Otherwise the route answers 422 with the error code
    MEDIA_KEY_NOT_OWNED or MEDIA_KEY_IS_VARIANT. A storage error that is not
    "object missing" answers 500.

    The route refuses with:
    - 404 when the run does not exist, or when the caller is in no team of the
      run (error code NOT_TEAM_MEMBER), or when the team dissolved in the meantime.
    - 422 when the run is not active (with the error code of the run state),
      when the challenge does not belong to the run, when the content is
      invalid, or when the team already answered the challenge (error code
      TEAM_ALREADY_SUBMITTED).

    A QUIZ or QR_SCAN challenge with auto_approve is scored at once. The
    response then carries is_scored, points and max_points, and a system feed
    event goes to the team and to the staff.

    An unscored answer can be replaced with PUT /{submission_id}.

    Emits audit event: submission.created
    """
    client_ip = client_address(request)
    run_repo = RunRepository(db, organization_id=org.id)

    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    if run.state != RunState.ACTIVE:
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cannot create submission when run is in {run.state} state. Submissions can only be created in active runs.",
            error_code=run_not_active_error_code(run.state),
        )

    team_member_repo = TeamMemberRepository(db, organization_id=org.id)
    team_membership = await team_member_repo.get_by_user_and_run(
        user_id=current_user.id,
        run_id=run_id,
    )

    if team_membership is None:
        raise CodedHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User is not a member of any team in this run",
            # The app sends a removed member out of the run with a notice.
            error_code="NOT_TEAM_MEMBER",
        )

    team_id = team_membership.team_id

    run_challenge_repo = RunChallengeRepository(db, organization_id=org.id)
    run_challenge = await run_challenge_repo.get_by_id(body.run_challenge_id)

    if run_challenge is None or run_challenge.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Challenge not found in this run",
        )

    # Every media key anywhere in the content must sit under the team's own
    # upload prefix. This is checked before the challenge-type-specific
    # validation, over the whole content, not just the field that type expects.
    team_upload_prefix = f"{org.id}/{run_id}/{team_id}/"
    verify_submission_media_owned(body.content, team_upload_prefix)

    validate_submission_content(run_challenge.challenge_type, body.content)

    # The live team row is locked before anything is written for the team, and
    # the membership is read again under that lock (lock order in
    # ``services/team_dissolution``: the team, then the answer that points to
    # it). The membership above was read without a lock: a dissolution that
    # commits after it would otherwise let this answer commit for a dissolved
    # team, because the foreign key only asks whether the team row exists.
    # With the lock one of the two is first. The dissolution: the team is gone
    # here, with the answer the join gives for a dissolved team. This answer:
    # the dissolution waits for the commit below and keeps the answer, as it
    # keeps every answer of the team. The lock comes after the checks of the
    # content, which may ask the object store, so it is held only for the
    # duplicate check, the insert and the commit.
    #
    # Before the team row comes the shared lock on the run row, the first row
    # lock of this path (``RunRepository.share_row``, lock order run, account,
    # team, member). The state above was read without a
    # lock: an end of the run that commits after it would otherwise let this
    # answer commit for an ended run, after the end has counted the ungraded
    # answers for its notice. With the lock one of the two is first. The end:
    # this request waits here and then reads ``ended``, and answers as every
    # late answer does. This answer: the end waits for the commit below and
    # counts it. Answers of other teams hold the same shared lock and do not
    # wait for each other.
    await _refuse_unless_active_under_shared_lock(run_repo, run_id, "create submission", "created")
    if not await TeamRepository(db, organization_id=org.id).lock_row(team_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found in this run",
        )
    # A column-only read: it never answers from the member row loaded above.
    if await team_member_repo.team_id_by_user_and_run(user_id=current_user.id, run_id=run_id) != team_id:
        # Removed from the team, or moved to another one, since the first read.
        raise CodedHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User is not a member of any team in this run",
            error_code="NOT_TEAM_MEMBER",
        )

    submission_repo = SubmissionRepository(db, organization_id=org.id)
    existing_submission = await submission_repo.get_by_team_and_challenge(
        team_id=team_id,
        run_challenge_id=body.run_challenge_id,
    )

    if existing_submission is not None:
        raise team_already_submitted_error()

    # The check above is not enough on its own: two members of the same team can
    # pass it in the same instant, before either has written. The database catches
    # that with ix_submissions_team_challenge_unique. Without the handler below the
    # loser of the race gets a 500. With it the loser gets the same answer as the
    # pre-check, so a client cannot tell which guard caught it.
    try:
        submission = await submission_repo.create(
            organization_id=org.id,
            run_id=run_id,
            team_id=team_id,
            run_challenge_id=body.run_challenge_id,
            content=body.content,
        )
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise team_already_submitted_error() from e
    await db.refresh(submission)

    record_audit_event(
        event="submission.created",
        actor=actor_from_user(current_user, client_ip),
        target={
            "entity": "submission",
            "id": str(submission.id),
            "team_id": str(team_id),
            "run_challenge_id": str(body.run_challenge_id),
            "run_id": str(run_id),
        },
        organization_id=org.id,
    )

    is_scored = await _try_auto_score_and_notify(db, submission, run_challenge, org)
    points, max_points = await _awarded_points(db, org.id, submission.id) if is_scored else (None, None)

    return SubmissionResponse(
        id=submission.id,
        organization_id=submission.organization_id,
        run_id=submission.run_id,
        team_id=submission.team_id,
        run_challenge_id=submission.run_challenge_id,
        content=submission.content,
        submitted_at=submission.submitted_at,
        is_scored=is_scored,
        points=points,
        max_points=max_points,
        created_at=submission.created_at,
        updated_at=submission.updated_at,
    )


@router.get(
    "/",
    response_model=SubmissionListResponse,
    responses={
        200: {"description": "List of submissions in run"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (wrong organization)", "model": ErrorResponse},
        404: {"description": "Run not found or user not in a team", "model": ErrorResponse},
    },
)
async def list_submissions(
    run_id: UUID,
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of records to return"),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("guest")),
) -> SubmissionListResponse:
    """
    List the submissions of a run, newest first.

    Open to every signed-in role, guests included. Coordinators and higher roles
    see every submission of the run. Other roles see only the submissions of their
    own team, and get 404 when they are in no team of the run (error code
    NOT_TEAM_MEMBER). For them, total is the number of items on the page.

    Each item says whether it is scored and carries the awarded points. The list
    is ordered by submitted_at and paged with skip and limit.
    """
    run_repo = RunRepository(db, organization_id=org.id)

    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    submission_repo = SubmissionRepository(db, organization_id=org.id)

    user_role_level = RoleLevel[current_user.role.value.upper()]
    if user_role_level >= RoleLevel.COORDINATOR:
        submissions = await submission_repo.list_by_run(run_id, skip=skip, limit=limit)
        total = await submission_repo.count_by_run(run_id)
    else:
        team_member_repo = TeamMemberRepository(db, organization_id=org.id)
        team_membership = await team_member_repo.get_by_user_and_run(
            user_id=current_user.id,
            run_id=run_id,
        )

        if team_membership is None:
            raise CodedHTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User is not a member of any team in this run",
                # The app sends a removed member out of the run with a notice.
                error_code="NOT_TEAM_MEMBER",
            )

        submissions = await submission_repo.list_by_team(team_membership.team_id, skip=skip, limit=limit)
        total = len(submissions)  # Team submissions are bounded (one per challenge)

    # Fetch the run's scores once and key them by submission (avoids a
    # per-submission score query). Keyed rather than a bare ID set because the
    # response carries the awarded points, not just the fact of a score.
    score_repo = ScoreRepository(db, organization_id=org.id)
    total_scores = await score_repo.count_by_run(run_id)
    run_scores = await score_repo.list_by_run(run_id, skip=0, limit=total_scores) if total_scores else []
    scores_by_submission = {score.submission_id: score for score in run_scores}

    submission_responses = [
        SubmissionResponse(
            id=submission.id,
            organization_id=submission.organization_id,
            run_id=submission.run_id,
            team_id=submission.team_id,
            run_challenge_id=submission.run_challenge_id,
            content=submission.content,
            submitted_at=submission.submitted_at,
            is_scored=submission.id in scores_by_submission,
            points=score.points if (score := scores_by_submission.get(submission.id)) else None,
            max_points=score.max_points if score else None,
            created_at=submission.created_at,
            updated_at=submission.updated_at,
        )
        for submission in submissions
    ]

    return SubmissionListResponse(
        items=submission_responses,
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/{submission_id}",
    response_model=SubmissionResponse,
    responses={
        200: {"description": "Submission details"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (wrong organization or not your team)", "model": ErrorResponse},
        404: {"description": "Submission not found or not in this run", "model": ErrorResponse},
    },
)
async def get_submission(
    run_id: UUID,
    submission_id: UUID,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("guest")),
) -> SubmissionResponse:
    """
    Return one submission of a run, with its awarded points if it is scored.

    Open to every signed-in role, guests included. Coordinators and higher roles
    can read any submission of the run. Other roles can read only the submissions
    of their own team and get 403 for any other.

    Answers 404 when the run does not exist, when the submission does not belong
    to the run, or when the team of the submission dissolved.
    """
    run_repo = RunRepository(db, organization_id=org.id)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    submission_repo = SubmissionRepository(db, organization_id=org.id)
    submission = await submission_repo.get_active_by_id(submission_id)

    if submission is None or submission.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Submission not found in this run",
        )

    user_role_level = RoleLevel[current_user.role.value.upper()]
    if user_role_level >= RoleLevel.COORDINATOR:
        pass  # Coordinator+ can see any submission
    else:
        team_member_repo = TeamMemberRepository(db, organization_id=org.id)
        team_membership = await team_member_repo.get_by_user_and_run(
            user_id=current_user.id,
            run_id=run_id,
        )

        if team_membership is None or team_membership.team_id != submission.team_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: you can only view submissions from your own team",
            )

    score_repo = ScoreRepository(db, organization_id=org.id)
    existing_score = await score_repo.get_by_submission(submission.id)

    return SubmissionResponse(
        id=submission.id,
        organization_id=submission.organization_id,
        run_id=submission.run_id,
        team_id=submission.team_id,
        run_challenge_id=submission.run_challenge_id,
        content=submission.content,
        submitted_at=submission.submitted_at,
        is_scored=existing_score is not None,
        points=existing_score.points if existing_score else None,
        max_points=existing_score.max_points if existing_score else None,
        created_at=submission.created_at,
        updated_at=submission.updated_at,
    )


@router.put(
    "/{submission_id}",
    response_model=SubmissionResponse,
    responses={
        200: {"description": "Submission replaced successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions, wrong organization, or not your team", "model": ErrorResponse},
        404: {"description": "Run not found, submission not found, or user not in a team", "model": ErrorResponse},
        422: {
            "description": "Run not in active state, submission already scored, or invalid content for challenge type",
            "model": ErrorResponse,
        },
    },
)
async def replace_submission(
    run_id: UUID,
    submission_id: UUID,
    request: Request,
    body: SubmissionUpdateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("guest")),
) -> SubmissionResponse:
    """
    Replace the content of an unscored submission of the caller's team.

    Open to every signed-in role, guests included. The caller must be a member of
    the team that made the submission, and the run must be active. The new
    content follows the rules of the challenge type, as on create.

    The route refuses with:
    - 404 when the run, the submission or its challenge does not exist, when the
      team of the submission dissolved, or when the caller is in no team of the
      run (error code NOT_TEAM_MEMBER).
    - 403 when the submission belongs to another team.
    - 422 when the run is not active (with the error code of the run state),
      when the submission is already scored (a coordinator must re-open it first
      with DELETE on the score), or when the content is invalid.

    The route stores the new content and sets submitted_at to now. After the
    commit it deletes the objects of the old answer that the new answer no longer
    references, together with the WebP variants of a photo (``<key>.thumb.webp``
    and ``<key>.display.webp``). It deletes only keys under the team's own upload
    prefix that no other submission of the run references. A skipped or failed
    delete is logged and never raised. A QUIZ or QR_SCAN challenge with
    auto_approve is scored at once, as on create.

    Emits audit event: submission.replaced
    """
    client_ip = client_address(request)
    run_repo = RunRepository(db, organization_id=org.id)

    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    # Look the answer up before the state check: an answer of a dissolved team is
    # 404 in every run state, so the state check must not answer first.
    submission_repo = SubmissionRepository(db, organization_id=org.id)
    submission = await submission_repo.get_active_by_id(submission_id)

    if submission is None or submission.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Submission not found in this run",
        )

    if run.state != RunState.ACTIVE:
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cannot replace submission when run is in {run.state} state. Submissions can only be replaced in active runs.",
            error_code=run_not_active_error_code(run.state),
        )

    team_member_repo = TeamMemberRepository(db, organization_id=org.id)
    team_membership = await team_member_repo.get_by_user_and_run(
        user_id=current_user.id,
        run_id=run_id,
    )

    if team_membership is None:
        raise CodedHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User is not a member of any team in this run",
            # The app sends a removed member out of the run with a notice.
            error_code="NOT_TEAM_MEMBER",
        )

    if team_membership.team_id != submission.team_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: you can only replace submissions from your own team",
        )

    score_repo = ScoreRepository(db, organization_id=org.id)
    existing_score = await score_repo.get_by_submission(submission.id)

    if existing_score is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Submission has already been scored — ask your coordinator to re-open it",
        )

    # The frozen run challenge drives the content validation and the auto-scoring.
    run_challenge_repo = RunChallengeRepository(db, organization_id=org.id)
    run_challenge = await run_challenge_repo.get_by_id(submission.run_challenge_id)

    if run_challenge is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Challenge not found for this submission",
        )

    # Every media key anywhere in the new content must sit under the team's own
    # upload prefix. This is checked before the challenge-type-specific
    # validation, over the whole content, not just the field that type expects.
    team_upload_prefix = f"{org.id}/{submission.run_id}/{submission.team_id}/"
    verify_submission_media_owned(body.content, team_upload_prefix)

    validate_submission_content(run_challenge.challenge_type, body.content)

    # Media keys of the outgoing answer, read before the update overwrites the content.
    old_media_keys = set(submission_media_keys(submission.content or {}))

    # The shared lock on the run row, as in ``create_submission`` and for the
    # same reason: the state above was read without a lock, and a replacement is
    # an answer too. It is the first and only row lock this path asks for, and the
    # update of the answer row follows it. It comes after the checks of the
    # content, which may ask the object store.
    await _refuse_unless_active_under_shared_lock(run_repo, run_id, "replace submission", "replaced")

    updated_submission = await submission_repo.update_content(submission.id, body.content)
    assert updated_submission is not None, "Submission should exist"
    submission = updated_submission
    await db.commit()
    await db.refresh(submission)

    # Delete the replaced answer's objects only after the new content is
    # committed, so a delete failure never loses the current answer. Keys the
    # new answer still references are kept. An old key is deleted only when it
    # sits under the team's own upload prefix ({org}/{run}/{team}/, the shape
    # app/routers/files.py writes) and no other submission of the run still
    # references it: submit-time validation only proves an object exists, so an
    # answer can carry any known key (a station image, another team's upload),
    # and the cleanup must not be tricked into deleting that object. Skips are
    # logged at info with the reason, delete failures at warning. Neither is
    # raised: a leftover object is an orphan that the retention sweep collects.
    kept_keys = set(submission_media_keys(submission.content or {}))
    stale_keys = old_media_keys - kept_keys
    still_referenced: set[str] = set()
    if stale_keys:
        still_referenced = await _other_submissions_media_keys(db, org.id, submission.run_id, submission.id)
    # A second line of defence beside verify_media_key_not_variant, which already
    # refuses a replacement that names a variant of a live key. kept_keys (what
    # the just-committed replacement references) joins still_referenced here, so a
    # variant that this cleanup derives from a stale old_key can never be a key
    # that the current answer names.
    protected_keys = still_referenced | kept_keys
    for old_key in stale_keys:
        if not old_key.startswith(team_upload_prefix):
            logger.info(
                "submission_replace_media_skip",
                object_key=old_key,
                submission_id=str(submission.id),
                reason="outside_team_upload_prefix",
            )
            continue
        if old_key in still_referenced:
            logger.info(
                "submission_replace_media_skip",
                object_key=old_key,
                submission_id=str(submission.id),
                reason="referenced_by_other_submission",
            )
            continue
        try:
            delete_file(old_key)
        except Exception:
            logger.warning(
                "submission_replace_media_orphan",
                object_key=old_key,
                submission_id=str(submission.id),
            )
            continue
        # A replaced photo's WebP variants go with it, except one that is itself
        # referenced elsewhere: validation only requires a key to sit under the
        # team's upload prefix, so another answer may reference a derived variant
        # key directly instead of the photo beside it. A failed variant delete only
        # leaves a run-scoped orphan for the retention sweep to collect.
        for variant_key in unreferenced_variant_keys(old_key, protected_keys):
            try:
                delete_file(variant_key)
            except Exception:
                logger.warning(
                    "submission_replace_media_orphan",
                    object_key=variant_key,
                    submission_id=str(submission.id),
                )

    record_audit_event(
        event="submission.replaced",
        actor=actor_from_user(current_user, client_ip),
        target={
            "entity": "submission",
            "id": str(submission.id),
            "team_id": str(submission.team_id),
            "run_challenge_id": str(submission.run_challenge_id),
            "run_id": str(run_id),
        },
        organization_id=org.id,
    )

    is_scored = await _try_auto_score_and_notify(db, submission, run_challenge, org)
    points, max_points = await _awarded_points(db, org.id, submission.id) if is_scored else (None, None)

    return SubmissionResponse(
        id=submission.id,
        organization_id=submission.organization_id,
        run_id=submission.run_id,
        team_id=submission.team_id,
        run_challenge_id=submission.run_challenge_id,
        content=submission.content,
        submitted_at=submission.submitted_at,
        is_scored=is_scored,
        points=points,
        max_points=max_points,
        created_at=submission.created_at,
        updated_at=submission.updated_at,
    )
