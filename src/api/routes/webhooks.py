from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.dependencies import SessionDep
from src.application.ports.payment_gateway import (
    PaymentGatewayError,
    PaymentGatewayTemporaryError,
)
from src.domain.payment import PaymentStatus
from src.infrastructure.database.inbox import claim_event
from src.infrastructure.database.models import PaymentModel, QuoteModel
from src.infrastructure.database.outbox import payment_event
from src.infrastructure.mercado_pago.webhook import validate_signature

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


class WebhookResource(BaseModel):
    id: str


class MercadoPagoNotification(BaseModel):
    id: str
    live_mode: bool
    type: str
    data: WebhookResource


def validate_notification(
    notification: MercadoPagoNotification,
    *,
    data_id: str,
    request_id: str,
    signature: str,
    secret: str,
    environment: str,
) -> None:
    if not secret:
        raise HTTPException(status_code=503, detail="webhook secret is not configured")
    if notification.data.id != data_id or notification.type != "order":
        raise HTTPException(status_code=400, detail="invalid order notification")
    if not validate_signature(
        secret=secret,
        data_id=data_id,
        request_id=request_id,
        signature=signature,
    ):
        raise HTTPException(status_code=401, detail="invalid webhook signature")
    is_test_environment = environment.strip().lower() in {"test", "sandbox"}
    if is_test_environment and notification.live_mode:
        raise HTTPException(
            status_code=400,
            detail="live Mercado Pago notification is disabled in test environment",
        )


def process_notification(
    session: Session, gateway, notification: MercadoPagoNotification, data_id: str
) -> dict[str, str]:
    if not claim_event(session, notification.id, "mercado-pago-webhook"):
        return {"status": "duplicate"}
    payment = session.scalar(
        select(PaymentModel).where(PaymentModel.external_id == data_id)
    )
    if payment is None:
        return {"status": "ignored"}
    remote_status = gateway.get_payment_status(data_id)
    if payment.status == remote_status.value:
        return {"status": "processed"}
    payment.status = remote_status.value
    event_type = {
        PaymentStatus.APPROVED: "PaymentConfirmedV1",
        PaymentStatus.REJECTED: "PaymentRejectedV1",
        PaymentStatus.REFUNDED: "PaymentRefundedV1",
    }.get(remote_status)
    if event_type:
        quote = session.get(QuoteModel, payment.quote_id)
        if quote is None:
            raise RuntimeError("payment quote not found")
        session.add(payment_event(payment, quote, event_type, notification.id))
    return {"status": "processed"}


@router.post("/mercado-pago")
def mercado_pago_webhook(
    notification: MercadoPagoNotification,
    request: Request,
    session: SessionDep,
    data_id: Annotated[str, Query(alias="data.id")],
    request_id: Annotated[str, Header(alias="X-Request-Id")],
    signature: Annotated[str, Header(alias="X-Signature")],
) -> dict[str, str]:
    validate_notification(
        notification,
        data_id=data_id,
        request_id=request_id,
        signature=signature,
        secret=request.app.state.mercado_pago_webhook_secret,
        environment=request.app.state.environment,
    )

    gateway = request.app.state.payment_gateway
    if gateway is None:
        raise HTTPException(status_code=503, detail="payment gateway is not configured")

    try:
        with session.begin():
            return process_notification(session, gateway, notification, data_id)
    except PaymentGatewayTemporaryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PaymentGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
