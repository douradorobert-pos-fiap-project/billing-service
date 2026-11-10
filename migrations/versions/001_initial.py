"""Create the Billing Service-owned schema.

Revision ID: 001
Revises:
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("catalog_items", sa.Column("id", sa.String(36), primary_key=True), sa.Column("name", sa.String(255), nullable=False), sa.Column("item_type", sa.String(20), nullable=False), sa.Column("unit_price", sa.Numeric(12, 2), nullable=False), sa.Column("active", sa.Boolean(), nullable=False))
    op.create_table("quotes", sa.Column("id", sa.String(36), primary_key=True), sa.Column("order_id", sa.String(36), nullable=False), sa.Column("saga_id", sa.String(36), nullable=False), sa.Column("correlation_id", sa.String(36), nullable=False), sa.Column("status", sa.String(30), nullable=False), sa.Column("total", sa.Numeric(12, 2), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_quotes_order_id", "quotes", ["order_id"])
    op.create_index("ix_quotes_saga_id", "quotes", ["saga_id"])
    op.create_table("quote_lines", sa.Column("id", sa.String(36), primary_key=True), sa.Column("quote_id", sa.String(36), sa.ForeignKey("quotes.id"), nullable=False), sa.Column("catalog_item_id", sa.String(36), nullable=False), sa.Column("name", sa.String(255), nullable=False), sa.Column("item_type", sa.String(20), nullable=False), sa.Column("unit_price", sa.Numeric(12, 2), nullable=False), sa.Column("quantity", sa.Integer(), nullable=False))
    op.create_index("ix_quote_lines_quote_id", "quote_lines", ["quote_id"])
    op.create_table("payments", sa.Column("id", sa.String(36), primary_key=True), sa.Column("order_id", sa.String(36), nullable=False), sa.Column("quote_id", sa.String(36), sa.ForeignKey("quotes.id"), nullable=False), sa.Column("amount", sa.Numeric(12, 2), nullable=False), sa.Column("status", sa.String(30), nullable=False), sa.Column("external_id", sa.String(100), nullable=True, unique=True))
    op.create_index("ix_payments_order_id", "payments", ["order_id"])
    op.create_table("outbox_messages", sa.Column("id", sa.String(36), primary_key=True), sa.Column("message_type", sa.String(100), nullable=False), sa.Column("aggregate_id", sa.String(36), nullable=False), sa.Column("payload", sa.JSON(), nullable=False), sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False), sa.Column("published_at", sa.DateTime(timezone=True), nullable=True), sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_billing_outbox_type", "outbox_messages", ["message_type"])
    op.create_index("ix_billing_outbox_aggregate", "outbox_messages", ["aggregate_id"])
    op.create_table("inbox_messages", sa.Column("id", sa.String(36), primary_key=True), sa.Column("event_id", sa.String(36), nullable=False), sa.Column("consumer", sa.String(100), nullable=False), sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("event_id", "consumer", name="uq_billing_inbox"))


def downgrade() -> None:
    op.drop_table("inbox_messages")
    op.drop_index("ix_billing_outbox_aggregate", table_name="outbox_messages")
    op.drop_index("ix_billing_outbox_type", table_name="outbox_messages")
    op.drop_table("outbox_messages")
    op.drop_index("ix_payments_order_id", table_name="payments")
    op.drop_table("payments")
    op.drop_index("ix_quote_lines_quote_id", table_name="quote_lines")
    op.drop_table("quote_lines")
    op.drop_index("ix_quotes_saga_id", table_name="quotes")
    op.drop_index("ix_quotes_order_id", table_name="quotes")
    op.drop_table("quotes")
    op.drop_table("catalog_items")
