"""
Run state transitions: which moves are allowed and the timestamps they set.

``VALID_TRANSITIONS`` maps a state to the states a run may move to. It cannot tell
the two moves to ``active`` apart: start (from ``configured`` or ``scheduled``,
with the snapshot of the rallye) and resume (from ``paused``, with the snapshot it
already has). So these two moves are actions of their own, each with the states it
accepts (``START_FROM``, ``RESUME_FROM``). The start route, the automatic start and
the resume route call :meth:`RunStateService.start` and
:meth:`RunStateService.resume`, never ``transition`` to ``active``.
"""

from datetime import UTC, datetime

from app.models.run import Run, RunState

VALID_TRANSITIONS: dict[RunState, set[RunState]] = {
    RunState.CONFIGURED: {RunState.SCHEDULED, RunState.ACTIVE},
    RunState.SCHEDULED: {RunState.ACTIVE, RunState.CONFIGURED},
    RunState.ACTIVE: {RunState.PAUSED, RunState.ENDED},
    RunState.PAUSED: {RunState.ACTIVE, RunState.ENDED},
    RunState.ENDED: set(),  # terminal state
}

# The states each move to ACTIVE accepts. Together they are the sources of ACTIVE in VALID_TRANSITIONS.
START_FROM: frozenset[RunState] = frozenset({RunState.CONFIGURED, RunState.SCHEDULED})
RESUME_FROM: frozenset[RunState] = frozenset({RunState.PAUSED})


class RunStateService:
    """
    Validates run state transitions and applies them to a Run. It does no
    database operation: the caller is responsible for persisting the change.
    """

    @staticmethod
    def can_transition(current: RunState, target: RunState) -> bool:
        """Whether ``VALID_TRANSITIONS`` allows the move from ``current`` to ``target``."""
        return target in VALID_TRANSITIONS.get(current, set())

    @staticmethod
    def _refusal(current: RunState, target: RunState) -> ValueError:
        valid_targets = VALID_TRANSITIONS.get(current, set())
        valid_targets_str = ", ".join(sorted(s.value for s in valid_targets))
        return ValueError(
            f"Invalid state transition from {current.value} to {target.value}. "
            f"Valid transitions from {current.value}: {valid_targets_str or 'none (terminal state)'}"
        )

    @staticmethod
    def check_start(current: RunState) -> None:
        """
        Raise ``ValueError`` unless a run in ``current`` may start: ``configured`` or
        ``scheduled``. A paused run is resumed, not started: a second start would build
        the snapshot of the rallye again.
        """
        if current in START_FROM:
            return
        if current in RESUME_FROM:
            raise ValueError(
                f"Invalid state transition from {current.value} to active by start. "
                "A paused run is resumed, not started."
            )
        raise RunStateService._refusal(current, RunState.ACTIVE)

    @staticmethod
    def check_resume(current: RunState) -> None:
        """
        Raise ``ValueError`` unless a run in ``current`` may resume: ``paused`` only. A
        run that never started has no snapshot and is started, not resumed.
        """
        if current in RESUME_FROM:
            return
        if current in START_FROM:
            raise ValueError(
                f"Invalid state transition from {current.value} to active by resume. "
                "A run that never started is started, not resumed."
            )
        raise RunStateService._refusal(current, RunState.ACTIVE)

    @staticmethod
    def start(run: Run) -> Run:
        """Move ``run`` to ACTIVE by start (``check_start``), in place, and return it."""
        RunStateService.check_start(run.state)
        return RunStateService.transition(run, RunState.ACTIVE)

    @staticmethod
    def resume(run: Run) -> Run:
        """Move ``run`` to ACTIVE by resume (``check_resume``), in place, and return it."""
        RunStateService.check_resume(run.state)
        return RunStateService.transition(run, RunState.ACTIVE)

    @staticmethod
    def transition(run: Run, target_state: RunState) -> Run:
        """
        Move ``run`` to ``target_state`` in place and return it. Sets the timestamps:
        - started_at: on the first transition to ACTIVE
        - paused_at: on PAUSED, cleared when the run resumes to ACTIVE
        - ended_at: on ENDED

        Raises ``ValueError`` when the current state does not allow the target. A move
        to ACTIVE goes through ``start`` or ``resume``, which check the action first.
        """
        current_state = run.state

        if not RunStateService.can_transition(current_state, target_state):
            raise RunStateService._refusal(current_state, target_state)

        run.state = target_state

        now = datetime.now(UTC)

        if target_state == RunState.ACTIVE:
            if run.started_at is None:
                run.started_at = now
            if current_state == RunState.PAUSED:
                run.paused_at = None

        elif target_state == RunState.PAUSED:
            run.paused_at = now

        elif target_state == RunState.ENDED:
            run.ended_at = now

        return run
