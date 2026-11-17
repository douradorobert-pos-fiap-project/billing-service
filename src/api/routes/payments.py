from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.api.dependencies import SessionDep
from src.infrastructure.database.models import PaymentModel

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


class PaymentResponse(BaseModel):
    id: UUID
    order_id: UUID
    quote_id: UUID
    amount: Decimal
    status: str
    qr_data: str | None


@router.get("/{payment_id}", response_model=PaymentResponse)
def detail_payment(payment_id: UUID, session: SessionDep) -> PaymentResponse:
    payment = session.get(PaymentModel, str(payment_id))
    if payment is None:
        raise HTTPException(status_code=404, detail="payment not found")
    return PaymentResponse(
        id=UUID(payment.id),
        order_id=UUID(payment.order_id),
        quote_id=UUID(payment.quote_id),
        amount=payment.amount,
        status=payment.status,
        qr_data=payment.qr_data,
    )
