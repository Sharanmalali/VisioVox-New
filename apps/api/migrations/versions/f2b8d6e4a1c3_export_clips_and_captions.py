"""export clips and burned-in captions

Revision ID: f2b8d6e4a1c3
Revises: e5a9c3d1f0b4
Create Date: 2026-10-09 20:00:00.000000

An export can be one stretch of the recording and can carry captions in the
picture (ADR-0022). The variant index grows to cover both, and treats NULLs
as equal so "the whole recording, no captions" is still one row.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2b8d6e4a1c3"
down_revision: str | Sequence[str] | None = "e5a9c3d1f0b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("exports", sa.Column("start_ms", sa.Integer(), nullable=True))
    op.add_column("exports", sa.Column("end_ms", sa.Integer(), nullable=True))
    op.add_column("exports", sa.Column("captions", sa.Text(), nullable=True))
    op.drop_index("uq_export_project_variant", table_name="exports")
    op.create_index(
        "uq_export_project_variant",
        "exports",
        ["project_id", "speaker_ordinal", "kind", "quality", "start_ms", "end_ms", "captions"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_export_project_variant", table_name="exports")
    op.create_index(
        "uq_export_project_variant",
        "exports",
        ["project_id", "speaker_ordinal", "kind", "quality"],
        unique=True,
    )
    op.drop_column("exports", "captions")
    op.drop_column("exports", "end_ms")
    op.drop_column("exports", "start_ms")
