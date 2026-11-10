from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from src.domain.catalog import CatalogItem, CatalogItemType, money


class QuoteStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ConflictingQuoteDecision(ValueError):
    """Raised when approval and rejection are attempted on the same quote."""


@dataclass(frozen=True)
class QuoteLine:
    catalog_item_id: UUID
    name: str
    item_type: CatalogItemType
    unit_price: Decimal
    quantity: int
    id: UUID = field(default_factory=uuid4)

    @classmethod
    def from_catalog(cls, item: CatalogItem, quantity: int) -> "QuoteLine":
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if not item.active:
            raise ValueError("catalog item is inactive")
        return cls(
            catalog_item_id=item.id,
            name=item.name,
            item_type=item.item_type,
            unit_price=money(item.unit_price),
            quantity=quantity,
        )

    @property
    def subtotal(self) -> Decimal:
        return money(self.unit_price * self.quantity)


@dataclass
class Quote:
    order_id: UUID
    saga_id: UUID
    correlation_id: UUID
    lines: list[QuoteLine]
    id: UUID = field(default_factory=uuid4)
    status: QuoteStatus = QuoteStatus.PENDING_APPROVAL

    @property
    def total(self) -> Decimal:
        return money(sum((line.subtotal for line in self.lines), Decimal("0.00")))

    def approve(self) -> bool:
        if self.status is QuoteStatus.APPROVED:
            return False
        if self.status is QuoteStatus.REJECTED:
            raise ConflictingQuoteDecision("rejected quote cannot be approved")
        self.status = QuoteStatus.APPROVED
        return True

    def reject(self) -> bool:
        if self.status is QuoteStatus.REJECTED:
            return False
        if self.status is QuoteStatus.APPROVED:
            raise ConflictingQuoteDecision("approved quote cannot be rejected")
        self.status = QuoteStatus.REJECTED
        return True
