"""Adiciona perfil e protege tabelas PostgreSQL contra acesso direto pela API."""

from alembic import op
import sqlalchemy as sa

revision = '78088e32219a'
down_revision = '2c684e9accae'
branch_labels = None
depends_on = None

TABLES = (
    'user', 'ministry', 'membership', 'event', 'shift', 'song',
    'repertoire', 'equipment', 'equipment_log', 'notice', 'notice_read',
    'password_reset', 'audit', 'rate_window', 'alembic_version',
)


def upgrade():
    op.add_column('user', sa.Column('avatar_key', sa.String(length=96)))
    op.add_column('user', sa.Column('show_notice_highlights', sa.Boolean(), server_default=sa.true(), nullable=False))
    op.create_index('ix_user_avatar_key', 'user', ['avatar_key'], unique=True)
    if op.get_context().dialect.name == 'postgresql':
        for table in TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade():
    op.drop_index('ix_user_avatar_key', table_name='user')
    op.drop_column('user', 'show_notice_highlights')
    op.drop_column('user', 'avatar_key')
    # Keep RLS enabled for tables that remain.
