"""
The ranked entries of a published result and what a dissolution does to them.

A result link stores a frozen snapshot in ``run_result_links.snapshot``. Its
``entries`` carry ``team_name``, ``points``, ``rank``, ``is_winner`` and the
``team_id`` of their team, which the public answer never shows. A snapshot
stored by an older version has entries without ``team_id``. ``rank_entries``
is the one copy of the ranking rule, for a new publish (``routers/runs.py``)
and for the removal of a dissolved team.
"""

from collections.abc import Iterable
from typing import Any


def rank_entries(teams: Iterable[tuple[Any, ...]]) -> list[dict[str, Any]]:
    """Rank ``(team_name, points)`` or ``(team_name, points, team_id)`` tuples with the rule of the scoreboard.

    Most points first. Teams with equal points keep the order they came in and
    share a rank, and the next rank counts the teams before it (1, 2, 2, 4).
    Every team on rank 1 is a winner, but nobody wins with 0 points: a run
    without answers would name every team "Gewinner". An entry gets ``team_id``
    only when its tuple carries one, so an entry of an old snapshot stays as
    stored.
    """
    entries: list[dict[str, Any]] = []
    previous_points: int | None = None
    rank = 0
    for idx, (team_name, points, *rest) in enumerate(sorted(teams, key=lambda team: -team[1])):
        if points != previous_points:
            rank = idx + 1
            previous_points = points
        entry: dict[str, Any] = {
            "team_name": team_name,
            "points": points,
            "rank": rank,
            "is_winner": rank == 1 and points > 0,
        }
        if rest and rest[0] is not None:
            entry["team_id"] = rest[0]
        entries.append(entry)
    return entries


def without_team(
    snapshot: dict[str, Any],
    team_name: str | None,
    *,
    team_id: str | None = None,
    teams_staying: int | None = None,
) -> dict[str, Any]:
    """The stored snapshot without the entry of one team, the rest ranked again.

    The entry is found in this order:

    1. By ``team_id``, when an entry carries it. That holds for every snapshot
       with ids, also for a team whose name is still pending.
    2. By ``team_name``, among the entries without an id only. A name is unique
       among the teams of a run that are not dissolved, and the anonymize sweep
       gives the team row and the snapshot the same neutral name. At most one
       entry goes, and never one that carries the id of another team.
    3. A team without a name in a snapshot without ids: nothing says which
       entry without a name is whose, so none goes. The entry of a team that
       stays is never removed.

    ``teams_staying`` is the number of other teams of the run that are not
    dissolved. At 0 the last team of the run goes, and no entry without an id
    can belong to a team that stays, so every entry without an id goes, named
    or not. That also clears an entry that step 3 left for a team dissolved
    earlier. When ``teams_staying`` is not given, it counts as "a team stays".

    The ranks come from the points in the snapshot, never from the live scores:
    an open regrading must not reach the public page. Everything else in the
    snapshot stays.

    Returns ``snapshot`` itself when no entry goes and a new dict otherwise, so
    the caller sees whether there is something to store.
    """
    entries = snapshot.get("entries") or []
    gone: set[int] = set()
    if team_id is not None:
        gone = {i for i, entry in enumerate(entries) if entry.get("team_id") == team_id}
    if not gone:
        by_name = [
            i for i, entry in enumerate(entries) if entry.get("team_id") is None and entry.get("team_name") == team_name
        ]
        if team_name is not None:
            gone = set(by_name[:1])
    if teams_staying == 0:
        gone |= {i for i, entry in enumerate(entries) if entry.get("team_id") is None}
    if not gone:
        return snapshot
    remaining = (entry for i, entry in enumerate(entries) if i not in gone)
    return {
        **snapshot,
        "entries": rank_entries((entry.get("team_name"), entry["points"], entry.get("team_id")) for entry in remaining),
    }
