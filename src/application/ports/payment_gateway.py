from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from src.domain.payment import Payment, PaymentStatus


class PaymentGatewayError(RuntimeError):
    pass


class PaymentGatewayConfigurationError(PaymentGatewayError):
    pass


class PaymentGatewayTemporaryError(PaymentGatewayError):
    pass


class PaymentGatewayInvalidResponse(PaymentGatewayError):
    pass


@dataclass(frozen=True)
class PaymentGatewayResult:
    external_id: str
    qr_data: str
    status: PaymentStatus


@dataclass(frozen=True)
class RefundResult:
    external_id: str
    amount: Decimal
    status: PaymentStatus


class PaymentGateway(Protocol):
    def create_dynamic_qr(self, payment: Payment) -> PaymentGatewayResult: ...

    def get_payment_status(self, external_id: str) -> PaymentStatus: ...

    def refund(self, external_id: str, amount: Decimal) -> RefundResult: ...
