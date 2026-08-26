"""add p6 auth, workspace membership, idempotency, and conversation version

Revision ID: f6a1c9d2e310
Revises: d91a4e62bf10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f6a1c9d2e310"
down_revision: Union[str, Sequence[str], None] = "d91a4e62bf10"
branch_labels = None
depends_on = None

_DEMO_PASSWORD_HASH = (
    "pbkdf2_sha256$310000$dGVjaHBpbG90LWRlbW8tYXV0aC12MQ$"
    "bDg_G_Nf5YXaEitgtwtmCoALoQbwZFEXocAknZk2KSM"
)


def upgrade() -> None:
    op.create_table(
        "user_account",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_demo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_user_account_email"),
    )
    op.create_index("ix_user_account_email", "user_account", ["email"])

    op.create_table(
        "workspace_member",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), server_default="MEMBER", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('OWNER', 'MEMBER')", name="ck_workspace_member_role"),
        sa.ForeignKeyConstraint(["user_id"], ["user_account.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "workspace_id"),
    )
    op.create_index("ix_workspace_member_workspace_id", "workspace_member", ["workspace_id"])

    op.create_table(
        "idempotency_record",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="PROCESSING", nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "state IN ('PROCESSING', 'COMPLETED', 'FAILED')",
            name="ck_idempotency_state",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user_account.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "workspace_id",
            "operation",
            "key",
            name="uq_idempotency_scope_key",
        ),
    )
    op.create_index("ix_idempotency_record_user_id", "idempotency_record", ["user_id"])
    op.create_index("ix_idempotency_record_workspace_id", "idempotency_record", ["workspace_id"])

    op.add_column(
        "conversation",
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
    )

    op.execute(
        sa.text(
            """
            INSERT INTO user_account (email, password_hash, is_active, is_demo)
            VALUES (:email, :password_hash, true, true)
            ON CONFLICT (email) DO NOTHING
            """
        ).bindparams(
            email="demo@techpilot.local",
            password_hash=_DEMO_PASSWORD_HASH,
        )
    )
    op.execute(
        """
        INSERT INTO workspace_member (user_id, workspace_id, role)
        SELECT u.id, w.id, 'OWNER'
        FROM user_account AS u
        CROSS JOIN workspace AS w
        WHERE u.email = 'demo@techpilot.local'
        ON CONFLICT (user_id, workspace_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_column("conversation", "version")
    op.drop_index("ix_idempotency_record_workspace_id", table_name="idempotency_record")
    op.drop_index("ix_idempotency_record_user_id", table_name="idempotency_record")
    op.drop_table("idempotency_record")
    op.drop_index("ix_workspace_member_workspace_id", table_name="workspace_member")
    op.drop_table("workspace_member")
    op.drop_index("ix_user_account_email", table_name="user_account")
    op.drop_table("user_account")
