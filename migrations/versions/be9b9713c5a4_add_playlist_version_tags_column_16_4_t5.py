"""add playlist_version.tags column (§16.4.t5 version labels/tags)

Adds a JSON column to `playlist_version` for operator-applied tags
("sent-to-iTunes", "jon-approved", etc.). Linear history is
preserved — tags are annotations, NOT branches.

Defaults to an empty list; backfills existing rows so the column
stays NOT NULL post-migration. SQLite's batch mode handles the
"add NOT NULL column with default" pattern.

Revision ID: be9b9713c5a4
Revises: 18b360df9e02
Create Date: 2026-06-22 15:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'be9b9713c5a4'
down_revision: Union[str, Sequence[str], None] = '18b360df9e02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('playlist_version', schema=None) as batch_op:
        # server_default='[]' so SQLite fills existing rows on table
        # rebuild; the application layer reads the column as a list
        # via the JSON type. NOT NULL after the backfill.
        batch_op.add_column(
            sa.Column(
                'tags',
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )


def downgrade() -> None:
    """Downgrade schema — drop the column."""
    with op.batch_alter_table('playlist_version', schema=None) as batch_op:
        batch_op.drop_column('tags')
