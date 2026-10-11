"""import source

Revision ID: f4b6d8e0a2c5
Revises: e3a5c7d9f1b4
Create Date: 2026-10-11 10:00:00.000000

Where an imported video came from: a link, Google Drive or Google Photos.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4b6d8e0a2c5"
down_revision: str | Sequence[str] | None = "e3a5c7d9f1b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "media_imports",
        sa.Column("source", sa.Text(), server_default="link", nullable=False),
    )
    op.create_check_constraint(
        "media_imports_source_check", "media_imports", "source IN ('link', 'drive', 'photos')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("media_imports_source_check", "media_imports", type_="check")
    op.drop_column("media_imports", "source")
