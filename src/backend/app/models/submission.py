"""The submission of a team to a challenge of a run."""

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.run import Run
    from app.models.run_challenge import RunChallenge
    from app.models.score import Score
    from app.models.team import Team


class Submission(UUIDMixin, TimestampMixin, Base):
    """
    The response of a team to a challenge.

    A team has exactly one submission per challenge, enforced by a unique index on
    (``team_id``, ``run_challenge_id``). While the submission is unscored, the team can replace
    it: ``content`` and ``submitted_at`` are updated in place. Once it is scored, a coordinator
    must re-open it first by deleting the score.

    ``content`` is a JSONB object whose shape depends on the challenge type:
        - TEXT: {"text": "Answer text"}
        - PHOTO: {"photo_url": "https://..."}
        - QR_SCAN: {"code": "ABC123"}
        - QUIZ: {"selected_option": 2}
        - CREATIVE: {"text": "...", "media_urls": [...]}

    There is no ``SoftDeleteMixin``. A submission is never deleted on its own. It is removed
    only by the cascade of a hard delete of its run, team or challenge.
    """

    __tablename__ = "submissions"

    # Mirrors migration 004.
    __table_args__ = (Index("ix_submissions_team_challenge_unique", "team_id", "run_challenge_id", unique=True),)

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to parent organization",
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to the run",
    )

    team_id: Mapped[UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to the team that made the submission",
    )

    run_challenge_id: Mapped[UUID] = mapped_column(
        ForeignKey("run_challenges.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to the challenge being answered",
    )

    content: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default={},
        server_default="{}",
        comment="Flexible submission data (type-specific): {'text': '...', 'photo_url': '...', etc.}",
    )

    submitted_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Timestamp when the submission was created (server-set)",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        lazy="selectin",
    )

    run: Mapped["Run"] = relationship(
        "Run",
        lazy="selectin",
    )

    team: Mapped["Team"] = relationship(
        "Team",
        back_populates="submissions",
        lazy="selectin",
    )

    run_challenge: Mapped["RunChallenge"] = relationship(
        "RunChallenge",
        lazy="selectin",
    )

    score: Mapped["Score | None"] = relationship(
        "Score",
        back_populates="submission",
        uselist=False,
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Submission(id={self.id}, team_id={self.team_id}, run_challenge_id={self.run_challenge_id}, submitted_at={self.submitted_at})>"
