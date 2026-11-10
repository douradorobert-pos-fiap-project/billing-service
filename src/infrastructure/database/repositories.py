from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.domain.catalog import CatalogItem, CatalogItemType
from src.domain.quote import Quote, QuoteLine, QuoteStatus
from src.infrastructure.database.models import (
    CatalogItemModel,
    QuoteLineModel,
    QuoteModel,
)
from src.infrastructure.database.outbox import quote_event


def get_catalog_item(session: Session, item_id: UUID) -> CatalogItem | None:
    model = session.get(CatalogItemModel, str(item_id))
    if model is None:
        return None
    return CatalogItem(id=UUID(model.id), name=model.name, item_type=CatalogItemType(model.item_type), unit_price=model.unit_price, active=model.active)


def persist_quote(session: Session, quote: Quote) -> None:
    session.add(QuoteModel(id=str(quote.id), order_id=str(quote.order_id), saga_id=str(quote.saga_id), correlation_id=str(quote.correlation_id), status=quote.status.value, total=quote.total))
    session.add_all(QuoteLineModel(id=str(line.id), quote_id=str(quote.id), catalog_item_id=str(line.catalog_item_id), name=line.name, item_type=line.item_type.value, unit_price=line.unit_price, quantity=line.quantity) for line in quote.lines)
    session.add(quote_event(quote, "QuoteGenerated"))


def get_quote(session: Session, quote_id: UUID) -> Quote | None:
    model = session.get(QuoteModel, str(quote_id))
    if model is None:
        return None
    lines = session.scalars(select(QuoteLineModel).where(QuoteLineModel.quote_id == model.id)).all()
    return Quote(id=UUID(model.id), order_id=UUID(model.order_id), saga_id=UUID(model.saga_id), correlation_id=UUID(model.correlation_id), status=QuoteStatus(model.status), lines=[QuoteLine(id=UUID(line.id), catalog_item_id=UUID(line.catalog_item_id), name=line.name, item_type=CatalogItemType(line.item_type), unit_price=line.unit_price, quantity=line.quantity) for line in lines])


def persist_decision(session: Session, quote: Quote, changed: bool) -> None:
    model = session.get(QuoteModel, str(quote.id))
    if model is None:
        raise LookupError("quote not found")
    model.status = quote.status.value
    if changed:
        event_type = "QuoteApproved" if quote.status is QuoteStatus.APPROVED else "QuoteRejected"
        session.add(quote_event(quote, event_type))
