import hashlib
import hmac
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.api.main import create_app
from src.application.ports.payment_gateway import PaymentGatewayResult, RefundResult
from src.domain.payment import Payment, PaymentStatus
from src.infrastructure.database.models import (
    InboxMessageModel,
    OutboxMessageModel,
    PaymentModel,
    QuoteModel,
)

SECRET = "webhook-test-secret"
EXTERNAL_ID = "ORD01JQ4S4KY8HWQ6NA5PXB65B3D3"
PAYMENT_ID = "10000000-0000-4000-8000-000000000001"
QUOTE_ID = "20000000-0000-4000-8000-000000000002"
ORDER_ID = "30000000-0000-4000-8000-000000000003"
SAGA_ID = "40000000-0000-4000-8000-000000000004"
CORRELATION_ID = "50000000-0000-4000-8000-000000000005"


class StatusGateway:
    def __init__(self, status: PaymentStatus) -> None:
        self.status = status
        self.status_queries: list[str] = []

    def create_dynamic_qr(self, payment: Payment) -> PaymentGatewayResult:
        raise AssertionError("not used by webhook")

    def get_payment_status(self, external_id: str) -> PaymentStatus:
        self.status_queries.append(external_id)
        return self.status

    def refund(self, external_id: str, amount: Decimal) -> RefundResult:
        raise AssertionError("not used by webhook")


def seed_payment(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        session.add(
            QuoteModel(
                id=QUOTE_ID,
                order_id=ORDER_ID,
                saga_id=SAGA_ID,
                correlation_id=CORRELATION_ID,
                status="APPROVED",
                total=Decimal("125.50"),
            )
        )
        session.add(
            PaymentModel(
                id=PAYMENT_ID,
                order_id=ORDER_ID,
                quote_id=QUOTE_ID,
                amount=Decimal("125.50"),
                status=PaymentStatus.PENDING.value,
                external_id=EXTERNAL_ID,
            )
        )


def signature(data_id: str, request_id: str, timestamp: str = "1742505638683") -> str:
    manifest = (
        f"id:{data_id.lower()};request-id:{request_id};ts:{timestamp};"
    ).encode()
    digest = hmac.new(SECRET.encode(), manifest, hashlib.sha256).hexdigest()
    return f"ts={timestamp},v1={digest}"


def webhook_client(
    session_factory: sessionmaker[Session], gateway: StatusGateway
) -> TestClient:
    return TestClient(
        create_app(
            session_factory,
            payment_gateway=gateway,
            mercado_pago_webhook_secret=SECRET,
            environment="test",
        )
    )


def post_webhook(
    client: TestClient,
    *,
    notification_id: str = "notification-1",
    live_mode: bool = False,
    request_id: str = "request-1",
    signature_value: str | None = None,
):
    return client.post(
        f"/api/v1/webhooks/mercado-pago?data.id={EXTERNAL_ID}&type=order",
        headers={
            "X-Request-Id": request_id,
            "X-Signature": signature_value
            or signature(EXTERNAL_ID, request_id),
        },
        json={
            "id": notification_id,
            "live_mode": live_mode,
            "type": "order",
            "data": {"id": EXTERNAL_ID},
        },
    )


def test_webhook_confirms_status_via_api_and_emits_event_once(
    session_factory: sessionmaker[Session],
):
    seed_payment(session_factory)
    gateway = StatusGateway(PaymentStatus.APPROVED)

    with webhook_client(session_factory, gateway) as client:
        first = post_webhook(client)
        duplicate = post_webhook(client)

    assert first.status_code == 200
    assert first.json() == {"status": "processed"}
    assert duplicate.status_code == 200
    assert duplicate.json() == {"status": "duplicate"}
    assert gateway.status_queries == [EXTERNAL_ID]

    with session_factory() as session:
        payment = session.get(PaymentModel, PAYMENT_ID)
        event = session.scalar(
            select(OutboxMessageModel).where(
                OutboxMessageModel.message_type == "PaymentConfirmedV1"
            )
        )
        assert payment.status == PaymentStatus.APPROVED.value
        assert event.aggregate_id == PAYMENT_ID
        assert event.payload["order_id"] == ORDER_ID
        assert event.payload["payload"] == {
            "payment_id": PAYMENT_ID,
            "external_id": EXTERNAL_ID,
            "amount": "125.50",
        }
        assert session.scalar(select(func.count()).select_from(InboxMessageModel)) == 1
        assert session.scalar(select(func.count()).select_from(OutboxMessageModel)) == 1


def test_webhook_body_cannot_confirm_payment_without_active_api_confirmation(
    session_factory: sessionmaker[Session],
):
    seed_payment(session_factory)
    gateway = StatusGateway(PaymentStatus.PENDING)

    with webhook_client(session_factory, gateway) as client:
        response = post_webhook(client)

    assert response.status_code == 200
    with session_factory() as session:
        assert session.get(PaymentModel, PAYMENT_ID).status == PaymentStatus.PENDING.value
        assert session.scalar(select(func.count()).select_from(OutboxMessageModel)) == 0


def test_invalid_signature_is_rejected_before_gateway_query(
    session_factory: sessionmaker[Session],
):
    seed_payment(session_factory)
    gateway = StatusGateway(PaymentStatus.APPROVED)

    with webhook_client(session_factory, gateway) as client:
        response = post_webhook(client, signature_value="ts=1,v1=invalid")

    assert response.status_code == 401
    assert gateway.status_queries == []
    with session_factory() as session:
        assert session.get(PaymentModel, PAYMENT_ID).status == PaymentStatus.PENDING.value


def test_live_mode_notification_is_rejected_in_test_environment(
    session_factory: sessionmaker[Session],
):
    seed_payment(session_factory)
    gateway = StatusGateway(PaymentStatus.APPROVED)

    with webhook_client(session_factory, gateway) as client:
        response = post_webhook(client, live_mode=True)

    assert response.status_code == 400
    assert response.json() == {
        "detail": "live Mercado Pago notification is disabled in test environment"
    }
    assert gateway.status_queries == []
