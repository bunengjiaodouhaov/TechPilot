"""add multi conversation model

Revision ID: d91a4e62bf10
Revises: c7d2a9814b31
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "d91a4e62bf10"
down_revision: Union[str, Sequence[str], None] = "c7d2a9814b31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), server_default="新对话", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_workspace_id", "conversation", ["workspace_id"])
    op.create_index("ix_conversation_updated_at", "conversation", ["updated_at"])
    op.add_column("conversation_turn", sa.Column("conversation_id", sa.Integer(), nullable=True))

    op.execute("""
        INSERT INTO conversation (workspace_id, title, created_at, updated_at)
        SELECT workspace_id, '历史对话', MIN(created_at), MAX(created_at)
        FROM conversation_turn
        GROUP BY workspace_id
    """)
    op.execute("""
        UPDATE conversation_turn AS t
        SET conversation_id = c.id
        FROM conversation AS c
        WHERE c.workspace_id = t.workspace_id
          AND c.title = '历史对话'
          AND t.conversation_id IS NULL
    """)

    op.alter_column("conversation_turn", "conversation_id", nullable=False)
    op.create_foreign_key(
        "fk_conversation_turn_conversation_id",
        "conversation_turn", "conversation",
        ["conversation_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_conversation_turn_conversation_id",
        "conversation_turn",
        ["conversation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_turn_conversation_id", table_name="conversation_turn")
    op.drop_constraint(
        "fk_conversation_turn_conversation_id",
        "conversation_turn",
        type_="foreignkey",
    )
    op.drop_column("conversation_turn", "conversation_id")
    op.drop_index("ix_conversation_updated_at", table_name="conversation")
    op.drop_index("ix_conversation_workspace_id", table_name="conversation")
    op.drop_table("conversation")
