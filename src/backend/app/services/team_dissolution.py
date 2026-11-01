"""
Dissolving a team.

A dissolved team has ``is_deleted`` and ``deleted_at`` set and no member rows:
the last one goes in the same transaction. Its answers and scores stay until
the retention sweeps delete them.

Five acts dissolve a team, all through this module: a member leaves, staff
removes a member, moves a member or removes the team, and the final erasure of
an account in a run that has not ended. A team without member rows is not
dissolved by that alone: ended runs in production hold such teams, and they
stay in the results.

Nothing here commits. The caller owns the transaction, so the removal of the
last member and the dissolution happen together or not at all.

The lock order
--------------
Every path that inserts, deletes or re-points a member row, or answers from
one, takes its locks in this order and decides only after that: run, account,
team, member, departure. Several rows of one table are always locked in
ascending id order.

0. Authentication, the first lock of every authenticated path.
   ``auth.dependencies.get_current_user`` clears an expired lockout with one
   update of the caller's account row and commits it at once in its own
   transaction, so a request holds no account lock from its authentication.
   The update must not stay pending on the account object: the next flush
   would send it after the team and member locks, team then account, against
   the erasure's account then team. A retried act calls
   ``authenticate_again`` before each new attempt, at the same place.
1. The run row (``RunRepository.lock_row``), held to the commit. It is the
   first row lock of every path that can dissolve a team, build or write a
   stored result, build the snapshot of a run, or create a team under a team
   limit: leave, remove or move a member, remove a team, the final erasure, the
   publish, the anonymize sweep (it renames teams in stored results), the start
   of a run, and team creation under a limit.
   Reason: a dissolution takes its team out of the stored results
   (``_remove_from_result_snapshots``), and the publish builds a result from
   the scoreboard and writes it. Under this lock they cannot overlap, so no
   published result holds a team that dissolved beside it. A sweep takes all
   its runs in one statement (``lock_run_ids``). The erasure learns its runs
   from member rows and uses ``lock_member_runs_and_accounts``, which never
   asks for a run lock with an account lock in hand.
   The paths that write an answer or a grading take the shared lock instead
   (``RunRepository.share_row``, ``FOR SHARE``): the submission and the
   replacement of an answer, the three score routes and the automatic score.
   It excludes the lock above, and its holders do not wait for each other. So
   the publish and the end of a run read their counts and standing with no
   answer or grading open, and answers do not queue on each other. It is the
   first row lock of its path. A holder never asks for the lock above and never
   updates the run row: two holders that both did would wait for each other.
2. The account row (``lock_account``), for a path that writes for an account:
   both joins by code, the join by id, team creation, the guest join with a
   session, leave, and the final erasure.
3. The team rows (``TeamRepository.lock_rows``). A path that learns its teams
   from member rows (the erasure, the replay of a guest creation) uses
   ``lock_member_teams``, which never asks for a team lock out of order.
4. Member rows, only after their team rows.
5. Departure rows. A departure is inserted after the member row it stands for
   is deleted. ``guest_cleanup`` deletes departures with their account, after
   account, team and member.

A result link row is the last lock of every path that takes one. Staff acts on
someone else's membership (remove, move, remove the team) take step 1, skip
step 2 and go on at step 3. The joins take no run row and start at step 2.

The order covers the key-share locks of inserts. An insert takes ``FOR KEY
SHARE`` on every row its foreign keys point to. Steps 1 and 2 and every update
of a run or an account take ``FOR NO KEY UPDATE``, and PostgreSQL grants ``KEY
SHARE`` beside it. So:

- A departure points to the run and the guest's account. The path that writes
  it already holds the run, team and member rows. Its key-share lock on the
  account comes after the member lock and waits for no account lock.
- A member points to the team and the account. A join already holds both.
- A team, an answer, a feed entry, feedback and a result link point to the
  run. That key-share lock waits for no run lock, so a new team or feed entry
  goes on beside a dissolution in the same run, and no cycle can close through
  the run row. An answer still waits for a dissolution, a publish or the end of
  the run, at its shared lock of step 1, which it asks for with nothing held.

``KEY SHARE`` conflicts only with ``FOR UPDATE``, which a delete needs and
which ``lock_team_rows`` takes on team rows. An insert that points to a team
(a member, an answer, a chat message) therefore waits at step 3 like any other
path. A score points to its answer and the grading account, not to a team.

A path that deletes an account row keeps the order and deletes last: accounts
(step 2), the teams of their member rows (step 3), the member rows read again
(step 4), then the delete. With the teams held, no removal or dissolution holds
a member row of the account or is about to write its departure. That path is
``guest_cleanup.delete_run_guests``.

Read again under the locks
--------------------------
Under its locks a path reads again everything it decides on, and writes
nothing it loaded before:

- The account: whether it is there, active and not erased, and its current name.
- The team: ``is_deleted``, and the member count for a join or a move target.
- The membership: whether it exists and in which team, and for a guest the
  departure, read after the membership. A removal writes both in one commit,
  so a membership that is gone has its departure visible.

A path that will not insert takes the same locks and reads the same things,
for example a guest who sends the code of their own team again. A team that
dissolved meanwhile answers as a team that does not exist. An account that was
erased or deactivated meanwhile answers as a session that has ended.

The lock inventory
------------------
Every statement in ``app`` that locks a run row for these paths, or writes or
locks a row of ``users``, ``teams``, ``team_members`` or ``run_departures``.
"run shared" is the shared lock of step 1. Every path waits at a row another
transaction holds, unless it says that it skips.

Requests, one transaction each:

- ``get_current_user`` before every request below, ``authenticate_again``
  before every new attempt: the caller's account row when a lockout has
  expired, committed at once (step 0).
- ``teams.create_team``: run (only under a team limit), account, insert team.
  It inserts no member row.
- ``teams.join_team_by_code``, ``join_team``: account, team, read membership,
  departure and capacity, insert member.
- ``teams.leave_team``: run, account, team, delete member, insert departure,
  dissolve. Retried.
- ``teams.remove_team_member``: run, team, delete member, insert departure,
  dissolve. Retried.
- ``teams.move_team_member``: run, both teams, the member row, re-point,
  dissolve the source. Retried.
- ``teams.remove_team``: run, team, ``dissolve_team``. Retried.
- ``dissolve_team``, under the caller's run lock: team, its member rows, insert
  departures, delete members, the run's result link rows
  (``list_by_run_id_for_update``) and their snapshots, update team. It takes no
  run row itself: a caller without one would then ask for it after the team.
  ``notify_team_dissolved`` takes no row lock: ``pg_notify`` locks the
  notification queue at the commit, when the path asks for no further lock.
- ``runs.publish_run_results``: run, read the ungraded count and the
  scoreboard, write the result link row. No team, member or departure row. No
  answer or grading is open between its lock and its commit, so "grading
  complete" and the standing are one state. A dissolution that comes second
  takes its team out of the result the publish wrote. A publish that comes
  second reads a scoreboard without the team.
- ``runs.revoke_run_results``, ``end_run``: run, then the result link row or
  the run and the feed. No row of the four tables, no snapshot. They take turns
  at the run row with a dissolving act, an answer and a grading. So the end
  counts the ungraded answers with none of them open, and an answer that waited
  for the end is refused as late.
- ``runs.start_run``: run, read the run and its state again, then the snapshot
  of the rallye (insert run stations and run challenges) and update the run, in
  one commit. No row of the four tables, no stored result. The inserts take
  ``FOR KEY SHARE`` on the run (held already), the organization and the stations
  and challenges they copy. A second start waits at the run row and then reads
  ``active``. The automatic start skips the held row. It takes turns at the run
  row with a dissolving act, the publish, the end, an answer and a grading, and
  holds nothing when it asks.
- ``runs.resume_run``: no lock before its update of the run row, which waits for
  a holder of the run row. It accepts only a paused run, so it never activates a
  run without its snapshot.
- ``teams.rename_team``: team, update team. The team flags in
  ``routers/chat.py``: update of one team row. ``advance_team_stage`` writes no
  row.
- ``guest.guest_join``, create: run (only under a team limit, then for every
  such request, also a replay without a new team), account with a session, on
  a replay the team of the membership, insert team, account and member. The run
  row is asked for again before the team count, already held, so that waits for
  nobody. No dissolution, no stored result.
- ``guest.guest_join``, join by code: account with a session, then the
  in-memory lock of the limiter (``team_code_rate_limit.admission``, per client
  and run). Under it: the limiter check, the code lookup (a plain read, no row
  lock) and the record of a failure or the reset. It is released before the
  next step. Then team, read membership, departure and capacity, insert account
  and member. The in-memory lock adds no edge to a cycle of row locks: its
  holder waits for no row lock, and no other path takes it. A request may wait
  for it while it holds its own account row. That wait ends with the holder's
  lookup, because the holder asks for no account row. A team that dissolved
  between the lookup and the team lock answers as an unknown code after the
  reset and counts no failure.
- ``submissions.create_submission``: run shared, the run state read from that
  statement, the team of the caller's membership (``TeamRepository.lock_row``),
  the membership read again, the duplicate check, insert answer. A run that
  ended before the shared lock gives the late answer. A team that dissolved, or
  a member removed or moved, before the team lock answers as no team or no
  member, and no answer is written. Run then team is the order of the
  dissolving acts too. An answer in flight takes turns at the run row with a
  dissolving act, the publish, the withdrawal, the end of the run, team
  creation under a limit and a change of run state. None of them holds a team,
  member or account row then, and the answer holds nothing when it asks.
- ``submissions.replace_submission``: run shared as its only row lock, the
  state read from it, update answer.
- ``_try_auto_score_and_notify``, the automatic score of a quiz or QR answer,
  in its own transaction after the answer's commit: run shared (only for a task
  that is scored automatically), insert score, held to its commit. The feed
  entry follows in a third transaction with no lock of the order.
- ``scores.create_score``, ``update_score``, ``reopen_score``: run shared, the
  reads, then insert, update or delete of one score row. No team or member row.
  A grading taken back while a publish holds the run row waits until the result
  is stored. That result keeps its standing, and the next publish is refused
  until the grading is complete again.
- ``routers/users.py`` (update, deactivate, delete with time to restore,
  restore), ``routers/auth.py``, ``routers/two_factor.py``,
  ``repositories/user.py``: insert or update of one account row, nothing after
  it. The password paths of ``routers/auth.py`` (forgot and reset) are public
  and pass no authentication.
- ``routers/invitations.py``: create, reissue and resend write no account row.
  ``accept_invitation`` (public) inserts the new account and, for an operator
  invitation, updates the accounts it replaces (``_take_over``) in one flush.
  The unit of work writes them in ascending id order. No team or member row.
- The final erasure in ``routers/users.py`` and
  ``gdpr.sweep_expired_grace_deletions``, both through
  ``gdpr.anonymize_users``: the runs of all their member rows, then the
  accounts (``lock_member_runs_and_accounts``), the teams of the member rows
  (``lock_member_teams``), delete the member rows in runs that have not ended
  with their departures and dissolutions, update the member rows that stay.
- Inserts that only point to a team (a chat message, a feed entry for one team,
  feedback) hold no lock of the four tables before. They wait for a team row
  held ``FOR UPDATE``. Inserts that only point to an account and take nothing
  else of the four tables hold the covered key-share lock: a password reset
  token (``PasswordResetRepository.create_token``, from the forgot path and the
  reset by staff), an invitation, a result link
  (``repositories/run_result_link``), and the content rows that name their
  author (rallyes, stations and challenges: create, duplicate, import, and copy
  through ``services/content_copy``).
- ``runs.delete_run`` marks the run row and is refused once the run has left
  the states configured and scheduled. It writes none of the four tables. No
  request deletes a team or a run row. The changes of a run's state
  (``routers/runs.py``, ``services/run_start.py``, ``services/notify.py``)
  update or lock the run row and wait for its holder. ``run_start`` and
  ``notify`` touch none of the four tables. The automatic start
  (``run_start._start_one``) locks the run row ``FOR UPDATE SKIP LOCKED`` with
  nothing held, reads the state from that statement and passes a held row, so
  it never waits for a start and two starts never build two snapshots.
- ``cli.py`` (instance administrators, demo content, geo index) writes none of
  the four tables for an existing team or account.

Sweeps, run one after another by ``services/retention.py``, each in its own
transaction:

- ``retention_tasks.sweep_run_guests``, per run ``delete_run_guests``. It skips
  a held row at every step: it goes through many runs in one transaction and
  still holds the rows of the runs before, so it must not wait, and a path that
  never waits cannot close a cycle. The next sweep finds what it skipped. Its
  steps: the accounts of the guests who are members of the run or left it (a
  departure and no member row), except those kept by a member row or a
  departure in a run that is not due. The first select takes no lock for a kept
  account. A member row in a team marked as deleted keeps no account, but that
  team is held like the others, because the delete writes its row. Then the
  teams of their member rows, in this run and others (a guest who left has no
  team to hold), the member rows and departures read again, the account rows
  whole, and the delete. The delete repeats the organization and the guest
  role, writes only account rows the path holds whole, sets ``user_id`` of
  member rows in held teams to NULL and deletes the account's departures last.
  A removal that writes a departure for the account at that moment holds a
  key-share lock on it, so the row is not taken whole and the account stays.
  Then ``blank_guest_member_names``: teams, update member rows in the held
  teams.
- ``retention_tasks.sweep_run_anonymize``: the run rows of all due runs in one
  statement (``lock_run_ids``), their teams (``lock_team_ids``), then update
  member rows, team names, run markers, result snapshots and feed entries. It
  holds nothing before the runs and takes no account.
- ``retention_tasks.sweep_anonymized_member_names``: the teams of the rows it
  changes, update member rows. It holds nothing before. No account lock: the
  accounts are erased, and no path writes a member row for an erased account.
- ``sweep_run_chat``, ``sweep_run_media``, ``sweep_run_answer_text``,
  ``sweep_dead_invitations``, ``sweep_expired_result_links`` and the media
  sweeps of ``retention_tasks.py`` and ``content_media_retention.py`` (which
  only reads): they delete or update chat messages, answers, scores, feedback,
  invitations, result links and stored objects. None touches the four tables.
  Only ``sweep_expired_result_links`` locks run rows: those of the expired
  links, in one statement (``lock_run_ids``) with nothing held before, and then
  it deletes the links that are still expired and the result notices of their
  runs. ``sweep_run_answer_text`` rewrites answers and score comments without
  the shared run lock: its runs ended long ago, no answer is accepted there,
  and it changes no count of graded answers and no points.
- ``org_deletion.sweep_due_organizations``: the organization row, then delete
  member rows, teams, departures and accounts, children first. This is outside
  the order. It takes an organization only when ``archived_at`` is set and
  ``deletion_requested_at`` is older than the days of grace, read under the
  organization lock. No authenticated request or guest join can meet it:
  ``get_current_user``, ``guest_join`` and the login refuse every session of an
  archived organization, so such a request would have to stay open for all the
  days of grace. The public paths (the password paths of ``routers/auth.py``
  and ``accept_invitation``) have no such check and can run beside it. They
  write account rows and no team or member row. If one of them and the deletion
  end in a deadlock, the public request fails, and the deletion of that
  organization fails as described below and runs again at the next cycle. The
  other sweeps run in the same loop, before it, never beside it.

When the order is not enough
----------------------------
``retry_on_lock_failure`` is the backstop. When PostgreSQL ends the
transaction of one of the four acts that dissolve a team by request for a
deadlock or a serialization failure, the act runs again in a fresh transaction
that reads every check again, three attempts at most. Before each new attempt
the request is authenticated again with its token (session version, lockout,
account, organization), so a password change or a lockout that committed in
between ends the request with the answer of the authentication. After the third
attempt the answer is 409 ``TEAM_BUSY`` and nothing is written. A path this
inventory missed costs a retry, not a failure.

``lock_member_teams`` gives up after five rounds in which a committed move took
a member row out of its locked teams, ``lock_member_runs_and_accounts`` after
five rounds in which a committed join put one into an unlocked run. Both raise
``MemberTeamsBusyError`` and hold nothing. The guest join that replays a
creation and the final erasure of one account (``gdpr.anonymize_user``) answer
it with 409 ``TEAM_BUSY``. The erasure sweep lets it pass and fails as
described below.

The four acts dissolve a team only when the client sent
``confirm_dissolution``. ``require_dissolution_confirmed`` decides inside the
attempt, under the run and team locks and before the first write, with plain
reads and no lock. Without the confirmation the answer is 409
``TEAM_WOULD_DISSOLVE`` with the current numbers, and nothing is written. The
final erasure asks for no confirmation.

The audit entry ``team.dissolved`` is written after the commit. The four acts
write it in their route after the retry returned. The erasure hands it to the
session, which writes it when the caller's transaction commits and drops it on
a rollback (``gdpr._audit_after_commit``).

A sweep that loses a deadlock fails its transaction. ``retention._run_task``
does not commit, so the task leaves no partial state. It logs
``retention_task_failed``, and the task runs again at the next cycle.
``sweep_due_organizations`` does the same per organization and logs
``org_deletion_failed``.
"""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

import structlog
from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import CodedHTTPException
from app.models.run import Run, RunState
from app.models.run_result_link import RunResultLink
from app.models.score import Score
from app.models.submission import Submission
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User
from app.repositories.run import lock_run_rows
from app.repositories.run_departure import RunDepartureRepository
from app.repositories.run_result_link import RunResultLinkRepository
from app.repositories.team import LockedTeam, TeamRepository, lock_team_rows
from app.repositories.team_member import TeamMemberRepository
from app.services.notify import notify_team_dissolved
from app.services.result_snapshot import without_team

log = structlog.get_logger(__name__)

# Rounds of ``lock_member_teams`` and ``lock_member_runs_and_accounts`` before
# they give up. Each round sees the change that committed before it, so one more
# round is enough unless the same member changes again at that moment.
_MEMBER_TEAM_LOCK_ATTEMPTS = 5

# The conflict answer of a request that could not get its team locks: an act
# that lost its transaction to a lock failure three times, or a path whose
# member rows kept changing teams (``MemberTeamsBusyError``).
TEAM_BUSY = "TEAM_BUSY"


def team_busy_error() -> CodedHTTPException:
    """The single 409 of a request that gave up on its team locks. Nothing was written."""
    return CodedHTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="The team is being changed by another request. Please try again.",
        error_code=TEAM_BUSY,
    )


# The conflict answer of an act that would dissolve a team and that the client
# did not confirm as a dissolution. Nothing was written.
TEAM_WOULD_DISSOLVE = "TEAM_WOULD_DISSOLVE"


class MemberTeamsBusyError(RuntimeError):
    """``lock_member_teams`` or ``lock_member_runs_and_accounts`` gave up: the member rows changed in every round.

    A request answers it with ``team_busy_error``. A sweep lets it pass: its
    transaction fails and the task runs again at the next cycle.
    """


async def lock_account_row(db: AsyncSession, user_id: UUID) -> User | None:
    """Lock the account row and return the account as it is now, or None when the row is gone.

    Step 2 of the lock order. The row is read again under the lock, also when
    this session loaded it before, so a caller writes the name of the returned
    object, never one it read earlier. ``FOR NO KEY UPDATE``, the lock of an
    update, makes paths that write for one account wait for each other and for
    its erasure or deactivation. Rows that only point to the account (an
    answer, a chat message) can still be inserted.
    """
    stmt = (
        select(User)
        .where(User.id == user_id)
        .with_for_update(key_share=True, of=User)
        .execution_options(populate_existing=True)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def lock_account(db: AsyncSession, user_id: UUID) -> User | None:
    """Lock the account row; None unless the account may still act: there, active and not erased."""
    account = await lock_account_row(db, user_id)
    if account is None or not account.is_active or account.is_deleted:
        return None
    return account


async def lock_member_teams(
    db: AsyncSession,
    read_team_ids: Callable[[], Awaitable[set[UUID]]],
    *,
    organization_id: UUID | None,
) -> dict[UUID, LockedTeam]:
    """Lock the teams that a set of member rows is in, and return their state under the lock.

    For a path that learns its teams from member rows: ``read_team_ids`` reads
    their team ids from the database. The teams are locked in ascending id
    order, then the ids are read again: a move that committed in between may
    have put a row into an unlocked team. Then a savepoint rollback gives back
    this round's locks and the round starts over, so no team lock is ever
    asked for out of order. On return every team of the rows is locked until
    the transaction ends. Empty when there is no such row.

    Raises ``MemberTeamsBusyError``, holding no lock, when every round saw
    such a move.
    """
    for _attempt in range(_MEMBER_TEAM_LOCK_ATTEMPTS):
        team_ids = await read_team_ids()
        if not team_ids:
            return {}
        savepoint = await db.begin_nested()
        locked = await lock_team_rows(db, team_ids, organization_id=organization_id)
        if await read_team_ids() <= locked.keys():
            await savepoint.commit()
            return locked
        await savepoint.rollback()
    raise MemberTeamsBusyError("member rows kept changing teams while their teams were being locked")


async def lock_member_runs_and_accounts(db: AsyncSession, user_ids: Iterable[UUID]) -> list[User]:
    """Lock the runs that the accounts have member rows in, then the accounts, and return the accounts.

    For the erasure, which can dissolve a team in any run of its accounts and
    has no run in its route. The run row is the first lock of the order, and
    only member rows tell the runs. So the runs of the accounts' member rows
    are locked, then the accounts, both in ascending id order, and then the
    member rows are read again. From then on no new member row can appear for
    these accounts: every insert for an existing account takes the account
    lock first, and a move keeps a member row in its run. A join that
    committed before the account lock may have put a row into an unlocked run.
    Then a savepoint rollback gives back this round's runs and accounts and
    the round starts over, so no run lock is ever asked for with an account
    lock in hand.

    Returns the existing accounts as they are under the lock, in ascending id
    order. On return the run of every member row of the accounts is locked
    until the transaction ends.

    Raises ``MemberTeamsBusyError``, holding no lock, when every round saw
    such a join, with the round limit of ``lock_member_teams``.
    """
    account_ids = sorted(set(user_ids))

    async def read_run_ids() -> set[UUID]:
        stmt = (
            select(Team.run_id)
            .join(TeamMember, TeamMember.team_id == Team.id)
            .where(TeamMember.user_id.in_(account_ids))
        )
        return set((await db.execute(stmt)).scalars())

    for _attempt in range(_MEMBER_TEAM_LOCK_ATTEMPTS):
        run_ids = await read_run_ids()
        savepoint = await db.begin_nested()
        await lock_run_rows(db, run_ids)
        accounts: list[User] = []
        for account_id in account_ids:
            account = await lock_account_row(db, account_id)
            if account is not None:
                accounts.append(account)
        if await read_run_ids() <= run_ids:
            await savepoint.commit()
            return accounts
        await savepoint.rollback()
    raise MemberTeamsBusyError("member rows kept appearing in other runs while their runs were being locked")


# What PostgreSQL reports when it ends a transaction to break a cycle of lock
# waits (40P01) or because it could not be serialized (40001).
LOCK_FAILURE_SQLSTATES = frozenset({"40P01", "40001"})
LOCK_RETRY_ATTEMPTS = 3


def is_lock_failure(exc: DBAPIError) -> bool:
    """Whether the database ended the transaction for a deadlock or a serialization failure."""
    sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    return sqlstate in LOCK_FAILURE_SQLSTATES


class LockRetryExhaustedError(RuntimeError):
    """Every attempt of an act lost its transaction to a lock failure."""


async def retry_on_lock_failure[T](
    db: AsyncSession,
    attempt: Callable[[], Awaitable[T]],
    *,
    before_retry: Callable[[], Awaitable[None]],
) -> T:
    """Run ``attempt`` and run it again when the database ends its transaction for a lock failure.

    The backstop behind the lock order. ``attempt`` is one whole transaction:
    it reads everything it decides on and commits. After a lock failure the
    transaction is rolled back, ``before_retry`` reads again what the caller
    loaded before the first attempt, and ``attempt`` starts over in a fresh
    transaction. After ``LOCK_RETRY_ATTEMPTS`` attempts it raises
    ``LockRetryExhaustedError``, with nothing written. Any other error passes
    through.
    """
    for attempt_no in range(1, LOCK_RETRY_ATTEMPTS + 1):
        try:
            return await attempt()
        except DBAPIError as exc:
            if not is_lock_failure(exc):
                raise
            await db.rollback()
            log.warning("team.lock_failure", attempt=attempt_no, sqlstate=getattr(exc.orig, "sqlstate", None))
            if attempt_no == LOCK_RETRY_ATTEMPTS:
                raise LockRetryExhaustedError from exc
            await before_retry()
    raise AssertionError("unreachable")


class TeamGoneError(LookupError):
    """The team to dissolve is already gone. Callers check this under the lock first."""


class DissolutionCause(StrEnum):
    """Why a team dissolved, as the audit event ``team.dissolved`` names it."""

    LAST_MEMBER_LEFT = "last_member_left"
    LAST_MEMBER_REMOVED = "last_member_removed"
    LAST_MEMBER_MOVED = "last_member_moved"
    REMOVED_BY_STAFF = "removed_by_staff"
    LAST_MEMBER_ERASED = "last_member_erased"


@dataclass(frozen=True)
class DissolutionResult:
    """What the audit event needs: the members removed by the dissolution and the answers and scores kept.

    ``member_user_ids`` names the accounts whose member rows the dissolution
    removed, so the caller can close their connections after the commit.
    """

    member_count: int
    answer_count: int
    score_count: int
    member_user_ids: tuple[UUID, ...] = ()

    def audit_fields(self, cause: DissolutionCause) -> dict[str, str | int]:
        return {
            "cause": cause.value,
            "member_count": self.member_count,
            "answer_count": self.answer_count,
            "score_count": self.score_count,
        }


async def _answer_count(db: AsyncSession, team_id: UUID) -> int:
    """A plain read without a lock."""
    return (await db.execute(select(func.count(Submission.id)).where(Submission.team_id == team_id))).scalar_one()


async def _result_is_published(db: AsyncSession, run_id: UUID) -> bool:
    """Whether the run has a live result link: published, not revoked and not run out. A plain read."""
    stmt = select(func.count(RunResultLink.id)).where(
        RunResultLink.run_id == run_id,
        RunResultLink.revoked_at.is_(None),
        RunResultLink.expires_at > datetime.now(UTC),
    )
    return (await db.execute(stmt)).scalar_one() > 0


async def _run_has_ended(db: AsyncSession, run_id: UUID) -> bool:
    """Whether the run has ended, read from the database and not from a loaded object. No lock."""
    state = (await db.execute(select(Run.state).where(Run.id == run_id))).scalar_one_or_none()
    return state == RunState.ENDED


async def require_dissolution_confirmed(
    db: AsyncSession, team: Team, *, confirmed: bool, removes_team: bool = False
) -> None:
    """Refuse an act that would dissolve ``team`` unless the client confirmed a dissolution.

    The page that asks "dissolve this team?" read its numbers before the
    click, and another coordinator can act in between, so the server decides.
    The caller holds the team lock, has found under it the member row its act
    takes out, and has written nothing yet. The act dissolves the team when it
    removes the team (``removes_team``) or takes out the last member.

    A confirmed act passes, and so does one that does not dissolve the team,
    whatever the client sent. An unconfirmed one raises 409
    ``TEAM_WOULD_DISSOLVE`` with the current ``member_count``,
    ``answer_count``, ``result_published`` and ``run_ended`` for the
    confirmation to show. Nothing is written, and the caller's transaction
    ends without a commit.

    The four numbers are plain reads without a lock, so the lock order does
    not change. The refusal is no lock failure: ``retry_on_lock_failure`` lets
    it pass unchanged and does not repeat the attempt.
    """
    if confirmed:
        return
    member_count = await TeamMemberRepository(db, organization_id=team.organization_id).count_by_team(team.id)
    if not removes_team and member_count > 1:
        return
    raise CodedHTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="This step dissolves the team. Confirm the dissolution to go on.",
        error_code=TEAM_WOULD_DISSOLVE,
        extra={
            "member_count": member_count,
            "answer_count": await _answer_count(db, team.id),
            "result_published": await _result_is_published(db, team.run_id),
            "run_ended": await _run_has_ended(db, team.run_id),
        },
    )


async def dissolve_team(db: AsyncSession, team: Team) -> DissolutionResult:
    """Dissolve ``team`` inside the caller's transaction. Does not commit.

    The caller holds the run row lock as the first lock of its transaction
    (step 1 of the lock order), because this function rewrites the stored
    results of the run. The team row is locked first, so two requests that
    take out the last two members at once serialize here, as the joins do.
    Removes the remaining member rows, records a departure for each guest
    among them, takes the team out of every stored result of the run, and
    marks the team as deleted. With the member row gone, the departure is the
    only record that blocks the guest from joining the run again.

    Raises ``TeamGoneError`` when the team is already gone under the lock.
    The caller holds that lock and has checked it, so this means a broken
    caller.
    """
    org_id = team.organization_id
    if not await TeamRepository(db, organization_id=org_id).lock_row(team.id):
        raise TeamGoneError(team.id)

    members = list(
        (
            await db.execute(
                select(TeamMember)
                .where(TeamMember.team_id == team.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    member_user_ids = tuple(member.user_id for member in members if member.user_id is not None)
    departure_repo = RunDepartureRepository(db, organization_id=org_id)
    for member in members:
        if member.is_guest and member.user_id is not None:
            await departure_repo.record(user_id=member.user_id, run_id=team.run_id)
        await db.delete(member)

    answer_count = await _answer_count(db, team.id)
    score_count = (
        await db.execute(
            select(func.count(Score.id))
            .join(Submission, Submission.id == Score.submission_id)
            .where(Submission.team_id == team.id)
        )
    ).scalar_one()

    await _remove_from_result_snapshots(db, team)
    # Delivered with the commit of the caller, dropped with its rollback.
    await notify_team_dissolved(db, team.run_id, team.id)

    team.is_deleted = True
    team.deleted_at = datetime.now(UTC)
    await db.flush()
    return DissolutionResult(
        member_count=len(members),
        answer_count=answer_count,
        score_count=score_count,
        member_user_ids=member_user_ids,
    )


async def _remove_from_result_snapshots(db: AsyncSession, team: Team) -> None:
    """Take ``team`` out of every stored result snapshot of its run.

    Every link row of the run counts, live, revoked or expired, so a later
    sweep or an operator who reads a dead link finds no dissolved team either.
    Called under the run lock and the team lock, before the team row changes.
    An entry is found by the team id. An older snapshot without ids is matched
    by the team's current name, read again under the lock. A team whose name
    is still pending has no name to match, so its entry in such a snapshot
    stays: the entry of a team that stays is never removed. When no other team
    stays, counted after the link rows are locked, every entry without an id
    goes, also one left by an earlier nameless team
    (``result_snapshot.without_team``). Only the snapshot is written: slug,
    publish time and end of the period stay, and the live scores are not read.
    """
    name = (await db.execute(select(Team.name).where(Team.id == team.id))).scalar_one_or_none()
    links = await RunResultLinkRepository(db).list_by_run_id_for_update(team.run_id)
    if not links:
        return
    teams_staying = (
        await db.execute(
            select(func.count(Team.id)).where(
                Team.run_id == team.run_id,
                Team.id != team.id,
                Team.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one()
    for link in links:
        snapshot = without_team(link.snapshot, name, team_id=str(team.id), teams_staying=teams_staying)
        if snapshot is not link.snapshot:
            link.snapshot = snapshot


async def dissolve_team_if_empty(db: AsyncSession, team: Team) -> DissolutionResult | None:
    """Dissolve ``team`` when it has no member row left. Does not commit.

    The caller has deleted or re-pointed its member row before, under the team
    lock it took first. The lock is asked for again here (a no-op for the
    holder) before the members are counted, so two removals at once cannot
    both see one member left.
    """
    if not await TeamRepository(db, organization_id=team.organization_id).lock_row(team.id):
        raise TeamGoneError(team.id)
    await db.flush()
    remaining = await TeamMemberRepository(db, organization_id=team.organization_id).count_by_team(team.id)
    if remaining > 0:
        return None
    return await dissolve_team(db, team)
