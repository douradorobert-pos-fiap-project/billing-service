from uuid import UUID

from src.domain.catalog import CatalogItem
from src.domain.quote import Quote, QuoteLine


def generate_quote(
    order_id: UUID,
    saga_id: UUID,
    correlation_id: UUID,
    selections: list[tuple[CatalogItem, int]],
) -> Quote:
    if not selections:
        raise ValueError("quote must contain at least one item")
    return Quote(
        order_id=order_id,
        saga_id=saga_id,
        correlation_id=correlation_id,
        lines=[QuoteLine.from_catalog(item, quantity) for item, quantity in selections],
    )
