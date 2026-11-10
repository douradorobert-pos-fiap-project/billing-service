from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

MONEY = Decimal("0.01")


class CatalogItemType(StrEnum):
    SERVICE = "SERVICE"
    PART = "PART"


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY)


@dataclass
class CatalogItem:
    id: UUID
    name: str
    item_type: CatalogItemType
    unit_price: Decimal
    active: bool = True

    def __post_init__(self) -> None:
        self.change_price(self.unit_price)

    def change_price(self, value: Decimal) -> None:
        value = money(value)
        if value < 0:
            raise ValueError("unit price cannot be negative")
        self.unit_price = value
