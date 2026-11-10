from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from src.domain.quote import Quote
from src.infrastructure.database.models import OutboxMessageModel


def quote_event(quote: Quote, event_type: str) -> OutboxMessageModel:
    occurred_at = datetime.now(UTC)
    event_id = uuid5(NAMESPACE_URL, f"{quote.id}:{event_type}")
    return OutboxMessageModel(
        id=str(event_id),
        message_type=event_type,
        aggregate_id=str(quote.id),
        occurred_at=occurred_at,
        payload={
            "event_id": str(event_id),
            "event_type": event_type,
            "event_version": 1,
            "occurred_at": occurred_at.isoformat(),
            "correlation_id": str(quote.correlation_id),
            "causation_id": str(event_id),
            "saga_id": str(quote.saga_id),
            "order_id": str(quote.order_id),
            "payload": {"quote_id": str(quote.id), "total": str(quote.total)},
        },
    )
