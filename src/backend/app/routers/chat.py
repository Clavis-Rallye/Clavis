"""
Chat endpoints (nested under teams).

Provides chat message operations within a team context with RBAC enforcement,
organization scoping via URL slug, and audit logging.

All endpoints require authentication and enforce role-based access control:
- List messages: team member OR coordinator+ can view messages
- Send message: team member OR coordinator+ can send messages

All endpoints are nested under teams: /api/orgs/{slug}/runs/{run_id}/teams/{team_id}/chat/
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_org_from_slug
from app.auth.rbac import RoleLevel, require_role
from app.client_address import client_address
from app.database import get_db
from app.exceptions import CodedHTTPException
from app.middleware.rate_limit import limiter
from app.models.feed_event import FeedEventType
from app.models.organization import Organization
from app.models.run import RunState
from app.models.user import User
from app.repositories.chat_message import ChatMessageRepository
from app.repositories.feed_event import FeedEventRepository
from app.repositories.run import RunRepository
from app.repositories.team import TeamRepository
from app.repositories.team_member import TeamMemberRepository
from app.routers.schemas import (
    ChatMessageCreateRequest,
    ChatMessageListResponse,
    ChatMessageResponse,
    CoordinatorRequestResponse,
    ErrorResponse,
)
from app.services.audit import actor_from_user, record_audit_event
from app.services.notify import (
    COORDINATOR_LEFT_MARKER,
    notify_chat_message,
    notify_feed_event,
    remember_chat_message_sent_here,
)
from app.services.ws_manager import manager as ws_manager

router = APIRouter()


async def _verify_team_access(
    db: AsyncSession,
    org_id: UUID,
    run_id: UUID,
    team_id: UUID,
    current_user: User,
) -> None:
    """
    Verify that the current user has access to this team's chat.

    Access is granted if:
    - The user is a member of the team, OR
    - The user has coordinator+ role AND the team's assist window is open
      (coordinators join on request, not by default)

    Raises 404 if the team is not in this run and 403 if the user has no access.
    A coordinator+ user without membership gets the error code CHAT_WINDOW_CLOSED
    while the window is closed. The frontend branches on it to show "the team has
    to request help again" instead of a generic retry banner.
    """
    team_repo = TeamRepository(db, organization_id=org_id)
    team = await team_repo.get_by_id(team_id)

    if team is None or team.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found in this run",
        )

    team_member_repo = TeamMemberRepository(db, organization_id=org_id)
    members = await team_member_repo.list_by_team(team_id)
    if any(member.user_id == current_user.id for member in members):
        return

    # Non-members with coordinator+ role have access only while the team's
    # assist window is open (opened by /request-coordinator, closed by
    # /leave-coordinator). This binds the whole coordinator+ range, admins too.
    user_role_level = RoleLevel[current_user.role.value.upper()]
    if user_role_level >= RoleLevel.COORDINATOR:
        if team.coordinator_chat_opened_at is not None:
            return
        raise CodedHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Only team members, or coordinators after a team's assistance request, can access this team's chat.",
            error_code="CHAT_WINDOW_CLOSED",
        )

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access denied. Only team members, or coordinators after a team's assistance request, can access this team's chat.",
    )


@router.get(
    "/",
    response_model=ChatMessageListResponse,
    responses={
        200: {"description": "List of chat messages"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {
            "description": "Access denied (not a team member, and no open assist window for a coordinator or above)",
            "model": ErrorResponse,
        },
        404: {"description": "Team not found or not in this run", "model": ErrorResponse},
    },
)
async def list_chat_messages(
    run_id: UUID,
    team_id: UUID,
    skip: int = Query(0, ge=0, description="Number of messages to skip (offset)"),
    limit: int = Query(100, ge=1, le=500, description="Maximum number of messages to return"),
    latest: bool = Query(
        False,
        description="Return the most recent messages instead of the first ones (still oldest-first)",
    ),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("guest")),
) -> ChatMessageListResponse:
    """List the chat messages of a team, oldest first.

    Open to every signed-in role, guests included, but only to members of the team.
    Coordinators and above who are not members can read the chat only while the
    team's assist window is open. While it is closed they get 403 with the error
    code `CHAT_WINDOW_CLOSED`. A team that is not in this run gives 404.

    Page through the messages with `skip` and `limit`. Pass `latest=true` to get the
    newest messages instead of the first ones. The page is still ordered oldest
    first. A chat is read at its end, so the first page of a long history shows the
    wrong part.
    """
    await _verify_team_access(db, org.id, run_id, team_id, current_user)

    chat_repo = ChatMessageRepository(db, organization_id=org.id)
    messages = await chat_repo.list_by_team(team_id, skip=skip, limit=limit, latest=latest)
    total = await chat_repo.count_by_team(team_id)

    # Resolve the author display names in one batched query (no N+1). A missing or
    # deleted user has no entry in the map, so .get() yields None.
    author_ids = {msg.author_id for msg in messages if msg.author_id is not None}
    display_names: dict[UUID, str] = {}
    if author_ids:
        result = await db.execute(
            select(User.id, User.display_name).where(
                User.organization_id == org.id,
                User.id.in_(author_ids),
            )
        )
        # Plain row iteration: dict(result.all()) fails mypy's Row typing and
        # result.tuples() raises at runtime on SQLAlchemy 2.0.51.
        display_names = {row.id: row.display_name for row in result}

    return ChatMessageListResponse(
        items=[
            ChatMessageResponse(
                id=msg.id,
                organization_id=msg.organization_id,
                team_id=msg.team_id,
                author_id=msg.author_id,
                content=msg.content,
                is_system_message=msg.is_system_message,
                author_role=msg.author_role,
                author_display_name=display_names.get(msg.author_id) if msg.author_id else None,
                created_at=msg.created_at,
            )
            for msg in messages
        ],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post(
    "/",
    response_model=ChatMessageResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Chat message created successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {
            "description": "Access denied (not a team member, and no open assist window for a coordinator or above)",
            "model": ErrorResponse,
        },
        404: {"description": "Team not found or not in this run", "model": ErrorResponse},
    },
)
@limiter.limit("30/minute")
async def send_chat_message(
    run_id: UUID,
    team_id: UUID,
    request: Request,
    body: ChatMessageCreateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("guest")),
) -> ChatMessageResponse:
    """Send a chat message to a team.

    Open to every signed-in role, guests included, but only to members of the team.
    Coordinators and above who are not members can write only while the team's
    assist window is open. Otherwise they get 403 with the error code
    `CHAT_WINDOW_CLOSED`. The run must be active. In any other state the route
    answers 422. A team that is not in this run gives 404. The route allows 30
    requests a minute.

    The author is the current user. A message sent through the API is never a system
    message. When a coordinator or above writes the first message of an open assist
    window, the route first adds a system message that says a coordinator has
    joined, and emits the audit event `chat.coordinator_joined`. The window gets one
    such message until it closes.

    Emits the audit event `chat_message.sent` and delivers the message to the
    WebSocket subscribers of the team chat.
    """
    client_ip = client_address(request)

    await _verify_team_access(db, org.id, run_id, team_id, current_user)

    # Block chat on a non-active (e.g. paused) run. Like the submission, upload and
    # join guards, this makes a paused run halt participant interaction.
    run_repo = RunRepository(db, organization_id=org.id)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )
    if run.state != RunState.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cannot send chat messages when run is in {run.state} state. Chat is only available in active runs.",
        )

    chat_repo = ChatMessageRepository(db, organization_id=org.id)

    # First coordinator+ message since the assist window opened: emit the
    # counterpart to [coordinator_left], so the team sees that a coordinator has
    # shown up and not only that one was requested. Once per window, through
    # coordinator_joined_notified_at, which is reset when the window closes.
    user_role_level = RoleLevel[current_user.role.value.upper()]
    if user_role_level >= RoleLevel.COORDINATOR:
        team_repo = TeamRepository(db, organization_id=org.id)
        team = await team_repo.get_by_id(team_id)
        if (
            team is not None
            and team.coordinator_chat_opened_at is not None
            and team.coordinator_joined_notified_at is None
        ):
            team.coordinator_joined_notified_at = datetime.now(UTC)
            joined_message = await chat_repo.create(
                organization_id=org.id,
                team_id=team_id,
                author_id=None,
                content=f"{_COORDINATOR_JOINED_MARKER} A coordinator has joined the team chat.",
                is_system_message=True,
            )
            await db.commit()
            await db.refresh(joined_message)
            # A system message has no author, so no display name.
            await notify_chat_message(db, joined_message, author_display_name=None)
            await db.commit()

            record_audit_event(
                event="chat.coordinator_joined",
                actor=actor_from_user(current_user, client_ip),
                target={"entity": "team", "id": str(team_id), "run_id": str(run_id)},
                organization_id=org.id,
            )

    message = await chat_repo.create(
        organization_id=org.id,
        team_id=team_id,
        author_id=current_user.id,
        content=body.content,
        is_system_message=False,  # API-created messages are never system messages
        author_role=current_user.role.value,
    )
    await db.commit()
    await db.refresh(message)

    # Broadcast to WebSocket subscribers. current_user is the author, so the display
    # name needs no extra query.
    await notify_chat_message(db, message, author_display_name=current_user.display_name)
    await db.commit()  # Commit the NOTIFY transaction

    record_audit_event(
        event="chat_message.sent",
        actor=actor_from_user(current_user, client_ip),
        target={
            "entity": "chat_message",
            "id": str(message.id),
            "team_id": str(team_id),
            "run_id": str(run_id),
        },
        organization_id=org.id,
    )

    return ChatMessageResponse(
        id=message.id,
        organization_id=message.organization_id,
        team_id=message.team_id,
        author_id=message.author_id,
        content=message.content,
        is_system_message=message.is_system_message,
        author_role=message.author_role,
        author_display_name=current_user.display_name,
        created_at=message.created_at,
    )


_COORDINATOR_REQUEST_MARKER = "[coordinator_request]"


def help_request_feed_content(team_id: UUID, team_name: str | None) -> dict[str, Any]:
    """Content of the SYSTEM feed entry for a help request.

    The payload carries structured fields (``message_key``, ``team_id``,
    ``team_name``) so the frontend can localize per current user. The ``de``/``en``
    strings stay populated for existing consumers. New code should prefer the
    structured fields. ``team_name`` is None for name-pending teams. The LK_C
    retention sweep rebuilds the entry with this function when it replaces the team
    name, so the sentences are written in this one place.
    """
    return {
        "message_key": "help_request",
        "team_id": str(team_id),
        "team_name": team_name,
        "de": f"Team {team_name} benötigt Hilfe." if team_name else "Ein Team benötigt Hilfe.",
        "en": f"Team {team_name} needs help." if team_name else "A team needs help.",
    }


@router.post(
    "/request-coordinator",
    response_model=CoordinatorRequestResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Coordinator assistance requested"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied — not a team member", "model": ErrorResponse},
        404: {"description": "Team not found or not in this run", "model": ErrorResponse},
    },
)
async def request_coordinator(
    run_id: UUID,
    team_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("guest")),
) -> CoordinatorRequestResponse:
    """Request coordinator assistance for a team.

    Open to members of the team only, whatever their role. A coordinator or above
    who is not a member gets 403, so that nobody can open the assist window that
    their own chat access depends on. The run must be active. In any other state
    the route answers 422. A team that is not in this run gives 404.

    The request opens the team's assist window. From then on coordinators and
    above can read the chat history and write in the chat until one of them
    leaves. A request while the window is open keeps it open.

    A team can ask for help as often as it needs. Each request adds a system chat
    message and a system feed event for the team, so that coordinators see the
    request in the activity feed.

    Emits the audit event `chat.coordinator_requested`.
    """
    client_ip = client_address(request)

    team_repo = TeamRepository(db, organization_id=org.id)
    team = await team_repo.get_by_id(team_id)

    if team is None or team.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found in this run",
        )

    # Every caller must be a member, coordinators too. An exemption would let a
    # coordinator open the assist window that gates their own chat access.
    team_member_repo = TeamMemberRepository(db, organization_id=org.id)
    members = await team_member_repo.list_by_team(team_id)
    is_member = any(m.user_id == current_user.id for m in members)
    if not is_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Only team members can request coordinator assistance.",
        )

    # Block coordinator-assistance requests on a non-active (e.g. paused) run.
    run_repo = RunRepository(db, organization_id=org.id)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )
    if run.state != RunState.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cannot request coordinator assistance when run is in {run.state} state. Requests are only available in active runs.",
        )

    # Open the coordinator assist window. An open window stays open with its
    # original timestamp. The system message and feed event below are emitted
    # anyway, because teams may ask repeatedly.
    if team.coordinator_chat_opened_at is None:
        team.coordinator_chat_opened_at = datetime.now(UTC)

    # A named team gets its name in the plain-text content. A name-pending team gets
    # a neutral text, so that frontends can localize it.
    chat_repo = ChatMessageRepository(db, organization_id=org.id)
    if team.name:
        system_content = f"{_COORDINATOR_REQUEST_MARKER} Team {team.name} has requested coordinator assistance."
    else:
        system_content = f"{_COORDINATOR_REQUEST_MARKER} A team has requested coordinator assistance."
    await chat_repo.create(
        organization_id=org.id,
        team_id=team_id,
        author_id=None,
        content=system_content,
        is_system_message=True,
    )

    feed_repo = FeedEventRepository(db, organization_id=org.id)
    team_name_display = team.name  # may be None for name-pending teams
    feed_event = await feed_repo.create(
        organization_id=org.id,
        run_id=run_id,
        event_type=FeedEventType.SYSTEM,
        content=help_request_feed_content(team_id, team_name_display),
        author_id=None,
        # The line is about one team. Without a target every team of the run
        # would read who asked for help. Staff see it regardless.
        target_team_id=team_id,
    )

    await db.commit()
    await notify_feed_event(db, feed_event)

    record_audit_event(
        event="chat.coordinator_requested",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "team", "id": str(team_id), "run_id": str(run_id)},
        organization_id=org.id,
    )

    return CoordinatorRequestResponse(
        message="Coordinator assistance requested",
    )


_COORDINATOR_LEFT_MARKER = COORDINATOR_LEFT_MARKER
_COORDINATOR_JOINED_MARKER = "[coordinator_joined]"


@router.post(
    "/leave-coordinator",
    response_model=CoordinatorRequestResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Coordinator left the team chat (or window already closed)"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied — requires coordinator+ role", "model": ErrorResponse},
        404: {"description": "Team not found or not in this run", "model": ErrorResponse},
    },
)
async def leave_coordinator(
    run_id: UUID,
    team_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> CoordinatorRequestResponse:
    """Leave a team chat as coordinator and close the team's assist window.

    Open to coordinators, creators and organization admins. The route works in
    every run state, so a coordinator can leave the chat of a paused or finished
    run too. The window is closed for every coordinator and above, so one leaving
    ends the access of all of them until the team requests assistance again.
    Coordinators and above who are not members of the team lose their WebSocket
    connection to the chat. A team that is not in this run gives 404.

    A system message tells the team that the coordinator has left. When the window
    is already closed the route does nothing and answers 200 without a second
    message.

    Emits the audit event `chat.coordinator_left`.
    """
    client_ip = client_address(request)

    team_repo = TeamRepository(db, organization_id=org.id)
    team = await team_repo.get_by_id(team_id)

    if team is None or team.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found in this run",
        )

    if team.coordinator_chat_opened_at is None:
        return CoordinatorRequestResponse(message="Coordinator chat already closed")

    # One delivery of the chat at a time, from before the end of the help is
    # committed until the recheck has closed the socket of a coordinator who is
    # no member. Every chat message of this process is delivered under the same
    # lock, by its listener, so none passes while this request awaits the commit,
    # the send or the recheck: a message the team writes after the commit waits
    # behind it and finds the coordinator's socket closed. The lock is taken before
    # the commit, not after it: the commit returns later than it takes effect, and
    # a later message could otherwise slip in. The writes below read nothing behind
    # this lock, so it costs the chat's deliveries one commit and the recheck's
    # reads.
    async with ws_manager.delivery(f"chat:{team_id}"):
        team.coordinator_chat_opened_at = None
        # Reset so the next request+join cycle emits [coordinator_joined] again.
        team.coordinator_joined_notified_at = None

        # System message so the team sees the coordinator is gone. Marker-prefixed
        # like the request message so frontends can localize.
        chat_repo = ChatMessageRepository(db, organization_id=org.id)
        left_message = await chat_repo.create(
            organization_id=org.id,
            team_id=team_id,
            author_id=None,
            content=f"{_COORDINATOR_LEFT_MARKER} The coordinator has left the team chat.",
            is_system_message=True,
        )

        # The notice goes through the database, as for every chat message: the other
        # API processes deliver it to their sockets of the chat, and each then
        # rechecks the channel after it. It goes in the transaction that ends the
        # help: a message committed after the end of the help is then noticed after
        # it in every process, never between the two. This process sends it itself
        # below, so it notes the id before the commit releases the notice: its own
        # listener skips the copy that comes back.
        remember_chat_message_sent_here(left_message.id)
        await notify_chat_message(db, left_message, author_display_name=None)
        await db.commit()

        # Broadcast the message. Without it, the message stays invisible to anyone
        # already in the chat until a reload, so the team sees the coordinator
        # arrive but never leave. The participant chat also derives its
        # coordinator-open state from this message.
        #
        # In this process the broadcast is a direct send to its sockets of the
        # chat, not the listener's delivery of the notice above: that comes at
        # some later moment that cannot be awaited here, so the recheck below
        # would close the coordinator's socket before the message came and their
        # open chat page would never learn that the help had ended. The send
        # reaches every socket of the chat once. The envelope is the listener's
        # (services/pg_notify.py) and the data notify_chat_message's. The message
        # was read back when it was written.
        created_at_value: datetime | None = left_message.created_at  # type: ignore[assignment]
        await ws_manager.broadcast(
            f"chat:{team_id}",
            {
                "type": "chat_message",
                "data": {
                    "id": str(left_message.id),
                    "team_id": str(left_message.team_id),
                    "organization_id": str(left_message.organization_id),
                    "author_id": None,
                    "content": left_message.content,
                    "is_system_message": left_message.is_system_message,
                    "author_role": left_message.author_role,
                    "author_display_name": None,
                    "created_at": created_at_value.isoformat() if created_at_value else None,
                },
                "timestamp": created_at_value.isoformat() if created_at_value else None,
            },
        )

        # The window is closed: a coordinator who is no member loses the chat's
        # socket now, after the message and before the next message of the team.
        # Each socket of the chat asks its admission rule again. Members stay.
        await ws_manager.recheck_channel(f"chat:{team_id}")

    record_audit_event(
        event="chat.coordinator_left",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "team", "id": str(team_id), "run_id": str(run_id)},
        organization_id=org.id,
    )

    return CoordinatorRequestResponse(message="Coordinator left the team chat")
