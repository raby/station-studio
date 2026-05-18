"""add rule table (§14.29)

Per-station configuration overrides. See engine.models.Rule + the
RULE_DEFAULTS dict in engine.rules for the full set of valid keys
and their defaults. Stations with no rows in this table fall back
to defaults; only divergence is persisted.

Station-scoped (composite UNIQUE on station_id + key); index on
station_id to make the load-all-rules query a single scan per
station. Value stored as TEXT to keep the column rule-shape-agnostic
for future non-int rules.

Trimmed manually: alembic autogen also flagged adding indexes to
cursor_state / playlist / playlist_entry / playlist_trace /
source_playlist_entry — same baseline-style mismatch artefact as the
§14.25/§14.26 migrations. Those indexes already exist; reapplying
them would be no-op-or-error.

Revision ID: 54570900ed97
Revises: 6db55f2a13af
Create Date: 2026-05-18 17:24:56.645647

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '54570900ed97'
down_revision: Union[str, Sequence[str], None] = '6db55f2a13af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'rule',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('station_id', sa.Integer(), nullable=False),
        sa.Column('key', sa.Text(), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['station_id'], ['station.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('station_id', 'key'),
    )
    with op.batch_alter_table('rule', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_rule_station_id'), ['station_id'], unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('rule', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_rule_station_id'))
    op.drop_table('rule')
