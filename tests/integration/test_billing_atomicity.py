from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.application.generate_quote import generate_quote
from src.domain.catalog import CatalogItem, CatalogItemType
from src.infrastructure.database.inbox import claim_event
from src.infrastructure.database.models import OutboxMessageModel, QuoteModel
from src.infrastructure.database.repositories import persist_quote


def quote_fixture():
    item = CatalogItem(
        id=uuid4(),
        name="Diagnóstico",
        item_type=CatalogItemType.SERVICE,
        unit_price=Decimal("100.00"),
    )
    return generate_quote(uuid4(), uuid4(), uuid4(), [(item, 1)])


def test_rollback_removes_quote_and_outbox(
    session_factory: sessionmaker[Session],
) -> None:
    with (
        pytest.raises(RuntimeError, match="simulated failure"),
        session_factory.begin() as session,
    ):
        persist_quote(session, quote_fixture())
        raise RuntimeError("simulated failure")

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(QuoteModel)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxMessageModel)) == 0


def test_inbox_deduplicates_per_consumer(
    session_factory: sessionmaker[Session],
) -> None:
    event_id = uuid4()
    with session_factory.begin() as session:
        assert claim_event(session, event_id, "billing-quote") is True
        assert claim_event(session, event_id, "billing-quote") is False
        assert claim_event(session, event_id, "billing-payment") is True
