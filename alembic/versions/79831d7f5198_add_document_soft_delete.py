"""add_document_soft_delete

Revision ID: 79831d7f5198
Revises: eb1c65724726
Create Date: 2026-07-25 21:23:29.368084

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '79831d7f5198'
down_revision: Union[str, Sequence[str], None] = 'eb1c65724726'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "document",
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column(
        "document",
        "deleted_at",
    )
