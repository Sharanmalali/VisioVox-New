"""media imports and trimming

Revision ID: d7f1b3c5e9a2
Revises: c4e6a8b0d2f3
Create Date: 2026-10-10 11:00:00.000000

Recordings imported from a link, and the part of a source to process.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7f1b3c5e9a2"
down_revision: str | Sequence[str] | None = "c4e6a8b0d2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("projects", sa.Column("trim_start_ms", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("trim_end_ms", sa.Integer(), nullable=True))
    op.create_table(
        "media_imports",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="queued", nullable=False),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("source_title", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'downloading', 'ready', 'failed')",
            name="media_imports_status_check",
        ),
        sa.CheckConstraint("progress BETWEEN 0 AND 100", name="media_imports_progress_check"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("media_imports")
    op.drop_column("projects", "trim_end_ms")
    op.drop_column("projects", "trim_start_ms")
