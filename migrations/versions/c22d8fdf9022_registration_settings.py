"""Permite à administração controlar quando o cadastro está aberto."""

from alembic import op
import sqlalchemy as sa

revision = 'c22d8fdf9022'
down_revision = '78088e32219a'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'site_settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('registration_enabled', sa.Boolean(), nullable=False),
        sa.CheckConstraint('id = 1', name='ck_site_settings_singleton'),
    )
    if op.get_context().dialect.name == 'postgresql':
        op.execute('ALTER TABLE site_settings ENABLE ROW LEVEL SECURITY')


def downgrade():
    op.drop_table('site_settings')
