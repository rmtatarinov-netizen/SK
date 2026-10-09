"""dashboard_slice_fields: sections, work_types, overdue_reason_types + fields

Revision ID: a7c31f9d2b54
Revises: 101e795e8896
Create Date: 2026-10-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a7c31f9d2b54'
down_revision: Union[str, None] = '101e795e8896'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('work_types',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('risk_weight', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('work_types', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_work_types_name'), ['name'], unique=True)

    op.create_table('sections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('object_id', sa.Integer(), nullable=False),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.ForeignKeyConstraint(['object_id'], ['objects.id'], name='fk_sections_object'),
        sa.ForeignKeyConstraint(['parent_id'], ['sections.id'], name='fk_sections_parent'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('object_id', 'name', 'parent_id', name='uq_section_name'),
    )
    with op.batch_alter_table('sections', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sections_name'), ['name'], unique=False)
        batch_op.create_index(batch_op.f('ix_sections_object_id'), ['object_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_sections_parent_id'), ['parent_id'], unique=False)

    op.create_table('overdue_reason_types',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('overdue_reason_types', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_overdue_reason_types_name'), ['name'], unique=True
        )

    with op.batch_alter_table('prescriptions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('section_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('work_type_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('recurrence_of_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('overdue_reason_type_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('overdue_reason_text', sa.Text(), nullable=True))
        batch_op.create_index(batch_op.f('ix_prescriptions_section_id'), ['section_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_prescriptions_work_type_id'), ['work_type_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_prescriptions_recurrence_of_id'), ['recurrence_of_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_prescriptions_overdue_reason_type_id'), ['overdue_reason_type_id'], unique=False)
        batch_op.create_foreign_key('fk_prescriptions_section', 'sections', ['section_id'], ['id'])
        batch_op.create_foreign_key('fk_prescriptions_work_type', 'work_types', ['work_type_id'], ['id'])
        batch_op.create_foreign_key('fk_prescriptions_recurrence_of', 'prescriptions', ['recurrence_of_id'], ['id'])
        batch_op.create_foreign_key(
            'fk_prescriptions_overdue_reason_type', 'overdue_reason_types',
            ['overdue_reason_type_id'], ['id'],
        )

    # M10 составные индексы — отдельно от batch (надёжно для SQLite).
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_prescriptions_object_deadline '
        'ON prescriptions (object_id, deadline)'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_prescriptions_contractor_status '
        'ON prescriptions (contractor_id, status)'
    )


def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS ix_prescriptions_object_deadline')
    op.execute('DROP INDEX IF EXISTS ix_prescriptions_contractor_status')
    # Индексы удаляемых колонок сбрасываются в том же batch, иначе перестройка
    # таблицы попытается пересоздать индекс на уже удалённой колонке.
    with op.batch_alter_table('prescriptions', schema=None) as batch_op:
        batch_op.drop_index('ix_prescriptions_overdue_reason_type_id')
        batch_op.drop_index('ix_prescriptions_recurrence_of_id')
        batch_op.drop_index('ix_prescriptions_work_type_id')
        batch_op.drop_index('ix_prescriptions_section_id')
        batch_op.drop_column('overdue_reason_text')
        batch_op.drop_column('overdue_reason_type_id')
        batch_op.drop_column('recurrence_of_id')
        batch_op.drop_column('work_type_id')
        batch_op.drop_column('section_id')

    op.drop_table('overdue_reason_types')
    op.drop_table('sections')
    op.drop_table('work_types')
