"""constrain document status

Revision ID: 580b94157371
Revises: 6ef8f659348c
Create Date: 2026-07-12 17:27:38.620485

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '580b94157371'
down_revision: Union[str, Sequence[str], None] = '6ef8f659348c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_check_constraint(
        "ck_document_status_valid",
        "document",
        "status IN ('PENDING', 'COMPLETED', 'PARTIAL', 'FAILED')",
    )

def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ck_document_status_valid",
        "document",
        type_="check",
    )
