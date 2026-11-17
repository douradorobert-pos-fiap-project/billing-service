"""Store Mercado Pago QR data for customer payment.

Revision ID: 003
Revises: 002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("qr_data", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("payments", "qr_data")
