"""add source_playlist.role column (§14.35)

Distinguishes content pools (engine samples from them) from reference
pools (declared so the iTunes XML import has a place to land, but
the generator never picks from them — weekly's MASTER is the
canonical example). Default 'content' so existing rows + the
common case need no extra wiring.

The template's `sources[].role` field carries the same information
at layout-validation time; the DB column is the source-of-truth at
generation time. A consistency check raises TemplateError when the
two disagree in the dangerous direction (layout=content,
DB=reference) — see engine.persistence.load_pools.

Trimmed manually: alembic autogen also flagged adding indexes to
cursor_state / playlist / playlist_entry / playlist_trace /
source_playlist_entry — same baseline-style mismatch artefact as
prior §14.x migrations. Those indexes already exist on real DBs;
adding them again would fail. Only the `role` column is new.

Revision ID: c7bb27708788
Revises: 1cafcc971152
Create Date: 2026-05-20 21:17:30.850687

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7bb27708788'
down_revision: Union[str, Sequence[str], None] = '1cafcc971152'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('source_playlist', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('role', sa.Text(), server_default='content', nullable=False)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('source_playlist', schema=None) as batch_op:
        batch_op.drop_column('role')
