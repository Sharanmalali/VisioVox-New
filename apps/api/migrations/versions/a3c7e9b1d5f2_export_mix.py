"""export a listener's own mix

Revision ID: a3c7e9b1d5f2
Revises: f2b8d6e4a1c3
Create Date: 2026-10-10 10:00:00.000000

An export can be the voices at the levels a listener set in the mixer
(ADR-0023); the mix is part of the export's identity.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c7e9b1d5f2"
down_revision: str | Sequence[str] | None = "f2b8d6e4a1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ["project_id", "speaker_ordinal", "kind", "quality", "start_ms", "end_ms", "captions"]


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("exports", sa.Column("mix", sa.Text(), nullable=True))
    op.drop_index("uq_export_project_variant", table_name="exports")
    op.create_index(
        "uq_export_project_variant",
        "exports",
        [*COLUMNS, "mix"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_export_project_variant", table_name="exports")
    op.create_index(
        "uq_export_project_variant",
        "exports",
        COLUMNS,
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.drop_column("exports", "mix")
