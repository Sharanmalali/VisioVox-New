"""project speaker names

Revision ID: c3d9a1e5f7b2
Revises: a1c4e7f2b9d3
Create Date: 2026-10-09 10:00:00.000000

Names a person gives the separated speakers, keyed by ordinal. Every existing
project starts with none, which reads as "Speaker N" exactly as before.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c3d9a1e5f7b2"
down_revision: str | Sequence[str] | None = "a1c4e7f2b9d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "projects",
        sa.Column(
            "speaker_names",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("projects", "speaker_names")
