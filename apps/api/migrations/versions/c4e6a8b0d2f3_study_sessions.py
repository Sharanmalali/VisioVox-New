"""study sessions

Revision ID: c4e6a8b0d2f3
Revises: b8d2f4a6c9e1
Create Date: 2026-10-10 18:00:00.000000

Anonymous user-study sessions (docs/user-study).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c4e6a8b0d2f3"
down_revision: str | Sequence[str] | None = "b8d2f4a6c9e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "study_sessions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("participant", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_study_participant", "study_sessions", ["user_id", "participant"], unique=True
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_study_participant", table_name="study_sessions")
    op.drop_table("study_sessions")
