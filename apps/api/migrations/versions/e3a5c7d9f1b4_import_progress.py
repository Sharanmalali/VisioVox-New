"""import download progress

Revision ID: e3a5c7d9f1b4
Revises: d7f1b3c5e9a2
Create Date: 2026-10-10 16:00:00.000000

Bytes, speed and time left while an imported video downloads.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3a5c7d9f1b4"
down_revision: str | Sequence[str] | None = "d7f1b3c5e9a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("media_imports", sa.Column("downloaded_bytes", sa.BigInteger(), nullable=True))
    op.add_column("media_imports", sa.Column("total_bytes", sa.BigInteger(), nullable=True))
    op.add_column("media_imports", sa.Column("speed_bps", sa.BigInteger(), nullable=True))
    op.add_column("media_imports", sa.Column("eta_seconds", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    for column in ("eta_seconds", "speed_bps", "total_bytes", "downloaded_bytes"):
        op.drop_column("media_imports", column)
