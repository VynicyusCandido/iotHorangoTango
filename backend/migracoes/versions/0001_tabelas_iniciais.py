"""tabelas iniciais

Revision ID: 0001
Revises: 
Create Date: 2026-11-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('dispositivos',
    sa.Column('device_id', sa.String(length=64), nullable=False),
    sa.Column('firmware', sa.String(length=32), nullable=True),
    sa.Column('vagas', sa.JSON(), nullable=True),
    sa.Column('online', sa.Boolean(), nullable=False),
    sa.Column('status_em', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ultimo_contato', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('device_id')
    )
    op.create_table('mensagens_invalidas',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('topico', sa.Text(), nullable=False),
    sa.Column('payload', sa.Text(), nullable=False),
    sa.Column('motivo', sa.Text(), nullable=False),
    sa.Column('recebido_em', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('usuarios',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('nome', sa.String(length=64), nullable=False),
    sa.Column('senha_hash', sa.String(length=100), nullable=False),
    sa.Column('perfil', sa.String(length=16), nullable=False),
    sa.Column('criado_em', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('nome')
    )
    op.create_table('alertas',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('device_id', sa.String(length=64), nullable=False),
    sa.Column('vaga', sa.Integer(), nullable=True),
    sa.Column('tipo', sa.String(length=32), nullable=False),
    sa.Column('mensagem', sa.Text(), nullable=False),
    sa.Column('criado_em', sa.DateTime(timezone=True), nullable=False),
    sa.Column('resolvido_em', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['device_id'], ['dispositivos.device_id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('comandos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('command_id', sa.String(length=40), nullable=False),
    sa.Column('device_id', sa.String(length=64), nullable=False),
    sa.Column('acao', sa.String(length=32), nullable=False),
    sa.Column('vaga', sa.Integer(), nullable=True),
    sa.Column('parametros', sa.JSON(), nullable=False),
    sa.Column('origem', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('erro', sa.String(length=32), nullable=True),
    sa.Column('enviado_em', sa.DateTime(timezone=True), nullable=False),
    sa.Column('recebido_em', sa.DateTime(timezone=True), nullable=True),
    sa.Column('executado_em', sa.DateTime(timezone=True), nullable=True),
    sa.Column('confirmado_em', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['device_id'], ['dispositivos.device_id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('command_id')
    )
    op.create_table('estados_vaga',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('device_id', sa.String(length=64), nullable=False),
    sa.Column('vaga', sa.Integer(), nullable=False),
    sa.Column('estado', sa.String(length=16), nullable=False),
    sa.Column('distancia_cm', sa.Float(), nullable=True),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ts_estimado', sa.Boolean(), nullable=False),
    sa.Column('recebido_em', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['dispositivos.device_id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_estados_vaga_busca', 'estados_vaga', ['device_id', 'vaga', 'ts'], unique=False)
    op.create_index('ux_estados_vaga_evento', 'estados_vaga', ['device_id', 'vaga', 'ts'], unique=True, postgresql_where=sa.text('NOT ts_estimado'), sqlite_where=sa.text('NOT ts_estimado'))
    op.create_table('eventos_status',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('device_id', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('recebido_em', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['dispositivos.device_id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('leituras_rfid',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('device_id', sa.String(length=64), nullable=False),
    sa.Column('vaga', sa.Integer(), nullable=False),
    sa.Column('uid', sa.String(length=20), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ts_estimado', sa.Boolean(), nullable=False),
    sa.Column('recebido_em', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['dispositivos.device_id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_leituras_rfid_ts', 'leituras_rfid', ['ts'], unique=False)
    op.create_index('ux_leituras_rfid_evento', 'leituras_rfid', ['device_id', 'vaga', 'uid', 'ts'], unique=True, postgresql_where=sa.text('NOT ts_estimado'), sqlite_where=sa.text('NOT ts_estimado'))
    op.create_table('medicoes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('device_id', sa.String(length=64), nullable=False),
    sa.Column('vaga', sa.Integer(), nullable=True),
    sa.Column('grandeza', sa.String(length=32), nullable=False),
    sa.Column('valor', sa.Float(), nullable=False),
    sa.Column('unidade', sa.String(length=16), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ts_estimado', sa.Boolean(), nullable=False),
    sa.Column('recebido_em', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['dispositivos.device_id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_medicoes_busca', 'medicoes', ['device_id', 'grandeza', 'ts'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_medicoes_busca', table_name='medicoes')
    op.drop_table('medicoes')
    op.drop_index('ux_leituras_rfid_evento', table_name='leituras_rfid', postgresql_where=sa.text('NOT ts_estimado'), sqlite_where=sa.text('NOT ts_estimado'))
    op.drop_index('ix_leituras_rfid_ts', table_name='leituras_rfid')
    op.drop_table('leituras_rfid')
    op.drop_table('eventos_status')
    op.drop_index('ux_estados_vaga_evento', table_name='estados_vaga', postgresql_where=sa.text('NOT ts_estimado'), sqlite_where=sa.text('NOT ts_estimado'))
    op.drop_index('ix_estados_vaga_busca', table_name='estados_vaga')
    op.drop_table('estados_vaga')
    op.drop_table('comandos')
    op.drop_table('alertas')
    op.drop_table('usuarios')
    op.drop_table('mensagens_invalidas')
    op.drop_table('dispositivos')
