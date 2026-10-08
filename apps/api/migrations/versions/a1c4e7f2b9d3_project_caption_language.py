"""project caption language

Revision ID: a1c4e7f2b9d3
Revises: 6d789446a903
Create Date: 2026-10-08 09:30:00.000000

The language a person says the recording is in; NULL means detect it
automatically (every project before this revision). Validated in the API
against visiovox_api.languages, not by the database, so adding a language the
recognizer gains needs no migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c4e7f2b9d3"
down_revision: str | Sequence[str] | None = "6d789446a903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("projects", sa.Column("language", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("projects", "language")
