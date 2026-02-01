"""
Automatic scoring of QUIZ and QR_SCAN challenges. A system score has scored_by=None.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.run_challenge import RunChallenge
from app.models.score import Score
from app.models.submission import Submission
from app.repositories.score import ScoreRepository


async def try_auto_score(
    db: AsyncSession,
    submission: Submission,
    run_challenge: RunChallenge,
    organization_id: UUID,
) -> Score | None:
    """
    Score a QUIZ or QR_SCAN submission automatically when scoring_rules has
    "auto_approve": true.

    A QUIZ compares submission.content["selected_option"] with
    scoring_rules["correct_option"], a QR_SCAN compares
    submission.content["scanned_value"] with scoring_rules["expected_value"]. A
    match scores max_points, a miss 0.

    ``run_challenge`` is the frozen snapshot being answered. Returns the
    created Score, or None when a coordinator must score the submission by hand.
    """
    scoring_rules = run_challenge.scoring_rules or {}

    if not scoring_rules.get("auto_approve", False):
        return None

    max_points = scoring_rules.get("max_points", 0)

    content = submission.content or {}

    if run_challenge.challenge_type == ChallengeType.QUIZ:
        correct_option = scoring_rules.get("correct_option")
        if correct_option is None:
            return None
        selected_option = content.get("selected_option")
        points = max_points if selected_option == correct_option else 0

    elif run_challenge.challenge_type == ChallengeType.QR_SCAN:
        expected_value = scoring_rules.get("expected_value")
        if expected_value is None:
            return None
        scanned_value = content.get("scanned_value")
        points = max_points if scanned_value == expected_value else 0

    else:
        return None

    score_repo = ScoreRepository(db, organization_id)
    score = await score_repo.create(
        submission_id=submission.id,
        points=points,
        max_points=max_points,
        comment=None,
        scored_by=None,  # system score, no human scorer
    )

    return score
