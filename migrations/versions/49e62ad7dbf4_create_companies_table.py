"""create_companies_table

Revision ID: 49e62ad7dbf4
Revises: a11bd4639334
Create Date: 2026-11-18 14:55:32.006095

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '49e62ad7dbf4'
down_revision: Union[str, None] = 'a11bd4639334'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'companies',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('name_normalized', sa.String(length=255), nullable=False),
        sa.Column('website', sa.String(length=2048), nullable=True),
        sa.Column('industry', sa.String(length=100), nullable=True),
        sa.Column('location', sa.String(length=255), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('is_archived', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_companies_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_companies')),
        sa.UniqueConstraint('user_id', 'name_normalized', name='uq_companies_user_name_normalized'),
    )
    op.create_index('idx_companies_user_id_is_archived_name', 'companies', ['user_id', 'is_archived', 'name'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_companies_user_id_is_archived_name', table_name='companies')
    op.drop_table('companies')
