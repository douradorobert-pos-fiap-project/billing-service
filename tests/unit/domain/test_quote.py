from decimal import Decimal
from uuid import uuid4

import pytest

from src.application.approve_quote import decide_quote
from src.application.generate_quote import generate_quote
from src.domain.catalog import CatalogItem, CatalogItemType
from src.domain.quote import ConflictingQuoteDecision, QuoteStatus


def catalog_item(name: str, price: str, item_type=CatalogItemType.SERVICE):
    return CatalogItem(
        id=uuid4(), name=name, item_type=item_type, unit_price=Decimal(price)
    )


def test_quote_uses_decimal_and_calculates_line_snapshots() -> None:
    labor = catalog_item("Troca de óleo", "100.10")
    oil = catalog_item("Óleo 5W30", "49.95", CatalogItemType.PART)

    quote = generate_quote(
        order_id=uuid4(),
        saga_id=uuid4(),
        correlation_id=uuid4(),
        selections=[(labor, 1), (oil, 3)],
    )

    assert quote.total == Decimal("249.95")
    assert quote.lines[1].unit_price == Decimal("49.95")
    assert quote.lines[1].subtotal == Decimal("149.85")


def test_catalog_price_change_does_not_change_existing_quote_snapshot() -> None:
    item = catalog_item("Alinhamento", "120.00")
    quote = generate_quote(uuid4(), uuid4(), uuid4(), [(item, 1)])

    item.change_price(Decimal("150.00"))

    assert quote.lines[0].unit_price == Decimal("120.00")
    assert quote.total == Decimal("120.00")


@pytest.mark.parametrize("quantity", [0, -1])
def test_quote_rejects_non_positive_quantity(quantity: int) -> None:
    with pytest.raises(ValueError, match="quantity must be positive"):
        generate_quote(
            uuid4(), uuid4(), uuid4(), [(catalog_item("Serviço", "10.00"), quantity)]
        )


def test_approval_is_idempotent_for_the_same_decision() -> None:
    quote = generate_quote(
        uuid4(), uuid4(), uuid4(), [(catalog_item("Serviço", "10.00"), 1)]
    )

    assert decide_quote(quote, "APPROVE") is True
    assert decide_quote(quote, "APPROVE") is False
    assert quote.status is QuoteStatus.APPROVED


def test_rejection_is_idempotent_for_the_same_decision() -> None:
    quote = generate_quote(
        uuid4(), uuid4(), uuid4(), [(catalog_item("Serviço", "10.00"), 1)]
    )

    assert decide_quote(quote, "REJECT") is True
    assert decide_quote(quote, "REJECT") is False
    assert quote.status is QuoteStatus.REJECTED


def test_conflicting_decision_is_rejected() -> None:
    quote = generate_quote(
        uuid4(), uuid4(), uuid4(), [(catalog_item("Serviço", "10.00"), 1)]
    )
    decide_quote(quote, "APPROVE")

    with pytest.raises(ConflictingQuoteDecision):
        decide_quote(quote, "REJECT")
