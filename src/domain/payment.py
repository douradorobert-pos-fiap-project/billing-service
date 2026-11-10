from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from src.domain.catalog import money


class PaymentStatus(StrEnum):
    CREATED = "CREATED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REFUNDED = "REFUNDED"


@dataclass
class Payment:
    order_id: UUID
    quote_id: UUID
    amount: Decimal
    id: UUID = field(default_factory=uuid4)
    status: PaymentStatus = PaymentStatus.CREATED

    def __post_init__(self) -> None:
        self.amount = money(self.amount)
        if self.amount <= 0:
            raise ValueError("payment amount must be positive")
