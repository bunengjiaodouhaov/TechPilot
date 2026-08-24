"""add conversation history

Revision ID: c7d2a9814b31
Revises: 79831d7f5198
Create Date: 2026-08-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c7d2a9814b31"
down_revision: Union[str, Sequence[str], None] = "79831d7f5198"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_turn",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("refused", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "citations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_turn_workspace_id", "conversation_turn", ["workspace_id"])
    op.create_index("ix_conversation_turn_created_at", "conversation_turn", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_conversation_turn_created_at", table_name="conversation_turn")
    op.drop_index("ix_conversation_turn_workspace_id", table_name="conversation_turn")
    op.drop_table("conversation_turn")
