"""
Scoring-rule shaping and validation, shared by both challenge-creation paths.

The organization-wide library (`routers/challenges.py`) and creation directly
onto a rallye station (`routers/rallyes.py`) both import from here, so a rule
cannot be enforced on one path and not the other.

- **Pruning** silently removes keys that mean nothing for the challenge's type.
  A type switch is a legitimate edit, and the caller cannot know which keys
  survive it, so a rejection would be an error the editor could not explain.
- **Validation** rejects an auto-scored challenge that misses what it scores
  with. That is a real contradiction, not leftovers.

Pruning matters because `auto_approve` is only honoured for QUIZ and QR_SCAN
(`services/auto_score.py`). On any other type the flag is inert: the admin UI
renders the challenge as self-scoring while it waits for a coordinator who was
never told.
"""

from typing import Any

from fastapi import HTTPException, status

from app.models.challenge import ChallengeType

# Keys that mean something regardless of challenge type.
UNIVERSAL_KEYS: frozenset[str] = frozenset({"max_points", "time_bonus"})

# Keys that only mean something for a specific type.
TYPE_KEYS: dict[ChallengeType, frozenset[str]] = {
    ChallengeType.QUIZ: frozenset({"auto_approve", "options", "correct_option"}),
    ChallengeType.QR_SCAN: frozenset({"auto_approve", "expected_value"}),
}


def prune_scoring_rules(
    challenge_type: ChallengeType,
    scoring_rules: dict[str, Any],
) -> dict[str, Any]:
    """Drop scoring keys that carry no meaning for ``challenge_type``."""
    if not scoring_rules:
        return scoring_rules
    allowed = UNIVERSAL_KEYS | TYPE_KEYS.get(challenge_type, frozenset())
    return {k: v for k, v in scoring_rules.items() if k in allowed}


def prune_scoring_rules_for_type_change(
    old_type: ChallengeType,
    new_type: ChallengeType,
    scoring_rules: dict[str, Any],
) -> dict[str, Any]:
    """
    Drop the old type's configuration when a challenge really changes type.

    Use this instead of :func:`prune_scoring_rules` when the request changes the
    type without ``scoring_rules`` of its own, so the stored rules are all there
    is. Only ``max_points`` and ``time_bonus`` survive.

    ``auto_approve`` goes too, although QUIZ and QR_SCAN both honour it. It is
    one half of a pair: the key it scores with (``correct_option`` for QUIZ,
    ``expected_value`` for QR_SCAN) belongs to the old type and goes. The flag
    alone would turn quiz to qr_scan into a 422 from `validate_scoring_rules`,
    over a value the creator never sent. The editor's confirmation names
    automatic approval among what the new type discards.
    """
    if new_type == old_type:
        return prune_scoring_rules(new_type, scoring_rules)
    if not scoring_rules:
        return scoring_rules
    return {k: v for k, v in scoring_rules.items() if k in UNIVERSAL_KEYS}


def validate_scoring_rules(
    challenge_type: ChallengeType,
    scoring_rules: dict[str, Any] | None,
) -> None:
    """
    Reject an auto-scored challenge that cannot actually be scored.

    QUIZ + auto_approve needs a ``correct_option`` (and, when options are set,
    one that is among them). QR_SCAN + auto_approve needs a non-empty
    ``expected_value``.
    """
    if not scoring_rules or not scoring_rules.get("auto_approve"):
        return

    if challenge_type == ChallengeType.QUIZ:
        if "correct_option" not in scoring_rules:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="QUIZ with auto_approve requires correct_option in scoring_rules",
            )
        options = scoring_rules.get("options")
        correct_option = scoring_rules.get("correct_option")
        if isinstance(options, list) and options and correct_option not in options:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="QUIZ correct_option must be one of the configured options",
            )
    elif challenge_type == ChallengeType.QR_SCAN:
        expected = scoring_rules.get("expected_value")
        if not isinstance(expected, str) or not expected.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="QR_SCAN with auto_approve requires non-empty expected_value in scoring_rules",
            )
