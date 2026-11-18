"""alertas, tags e duracao da reserva

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('tags',
    sa.Column('uid', sa.String(length=20), nullable=False),
    sa.Column('apelido', sa.String(length=64), nullable=False),
    sa.Column('dono', sa.String(length=64), nullable=True),
    sa.Column('criada_em', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('uid')
    )
    op.add_column('alertas', sa.Column('chave', sa.String(length=128), server_default='', nullable=False))
    op.add_column('alertas', sa.Column('notificado_em', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_alertas_chave', 'alertas', ['chave'], unique=False)
    op.add_column('dispositivos', sa.Column('duracao_reserva_min', sa.Integer(), server_default='15', nullable=False))
    # Índice de duplicatas com o estado: duas mudanças no mesmo segundo não
    # são a mesma mensagem
    op.drop_index('ux_estados_vaga_evento', table_name='estados_vaga')
    op.create_index('ux_estados_vaga_evento', 'estados_vaga', ['device_id', 'vaga', 'ts', 'estado'], unique=True,
                    postgresql_where=sa.text('NOT ts_estimado'), sqlite_where=sa.text('NOT ts_estimado'))


def downgrade() -> None:
    op.drop_index('ux_estados_vaga_evento', table_name='estados_vaga')
    op.create_index('ux_estados_vaga_evento', 'estados_vaga', ['device_id', 'vaga', 'ts'], unique=True,
                    postgresql_where=sa.text('NOT ts_estimado'), sqlite_where=sa.text('NOT ts_estimado'))
    op.drop_column('dispositivos', 'duracao_reserva_min')
    op.drop_index('ix_alertas_chave', table_name='alertas')
    op.drop_column('alertas', 'notificado_em')
    op.drop_column('alertas', 'chave')
    op.drop_table('tags')
