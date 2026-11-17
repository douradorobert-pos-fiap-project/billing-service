from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.application.generate_quote import generate_quote
from src.domain.catalog import CatalogItem, CatalogItemType
from src.infrastructure.database.inbox import claim_event
from src.infrastructure.database.models import (
    Base,
    CatalogItemModel,
    OutboxMessageModel,
    QuoteLineModel,
    QuoteModel,
)
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


def test_quote_parent_is_flushed_before_quote_lines():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    quote = quote_fixture()
    line = quote.lines[0]
    with factory.begin() as session:
        session.add(
            CatalogItemModel(
                id=str(line.catalog_item_id),
                name=line.name,
                item_type=line.item_type.value,
                unit_price=line.unit_price,
                active=True,
            )
        )

    with factory.begin() as session:
        persist_quote(session, quote)

    with factory() as session:
        assert session.get(QuoteModel, str(quote.id)) is not None
        assert session.scalar(select(func.count()).select_from(QuoteLineModel)) == 1

    engine.dispose()
