from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import func, select

from src.application.ports.payment_gateway import PaymentGatewayResult, RefundResult
from src.domain.payment import Payment, PaymentStatus
from src.infrastructure.database.models import (
    CatalogItemModel,
    InboxMessageModel,
    OutboxMessageModel,
    PaymentModel,
    QuoteLineModel,
    QuoteModel,
)
from src.infrastructure.messaging.handler import process_event


class FakeGateway:
    def __init__(self, status=PaymentStatus.PENDING):
        self.status = status
        self.created: list[Payment] = []

    def create_dynamic_qr(self, payment: Payment) -> PaymentGatewayResult:
        self.created.append(payment)
        return PaymentGatewayResult("order-123", "qr-data", self.status)

    def get_payment_status(self, external_id: str) -> PaymentStatus:
        raise AssertionError("status lookup is not used by the worker")

    def refund(self, external_id: str, amount: Decimal) -> RefundResult:
        raise AssertionError("refund is not used by the worker")


def event(event_type: str, order_id=None, saga_id=None, payload=None) -> dict:
    return {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "event_version": 1,
        "occurred_at": datetime.now(UTC).isoformat(),
        "correlation_id": str(uuid4()),
        "causation_id": str(uuid4()),
        "saga_id": str(saga_id or uuid4()),
        "order_id": str(order_id or uuid4()),
        "payload": payload or {},
    }


def test_quote_request_generates_quote_and_acknowledges_duplicate(
    session_factory,
):
    item_id = uuid4()
    order_id = uuid4()
    saga_id = uuid4()
    message = event(
        "QuoteGenerationRequestedV1",
        order_id,
        saga_id,
        {"recommended_service_ids": [str(item_id)]},
    )
    with session_factory.begin() as session:
        session.add(
            CatalogItemModel(
                id=str(item_id),
                name="Brake service",
                item_type="SERVICE",
                unit_price=Decimal("125.00"),
                active=True,
            )
        )

    gateway = FakeGateway()
    assert process_event(session_factory, message, gateway) is True
    assert process_event(session_factory, message, gateway) is False

    with session_factory() as session:
        quote = session.scalar(select(QuoteModel))
        line = session.scalar(select(QuoteLineModel))
        event_count = session.scalar(
            select(func.count()).select_from(OutboxMessageModel)
        )
        inbox_count = session.scalar(
            select(func.count()).select_from(InboxMessageModel)
        )
    assert quote.order_id == str(order_id)
    assert quote.saga_id == str(saga_id)
    assert quote.total == Decimal("125.00")
    assert line.name == "Brake service"
    assert event_count == 1
    assert inbox_count == 1
    assert gateway.created == []


def test_quote_request_reuses_existing_manual_quote_without_recommendations(
    session_factory,
):
    order_id = uuid4()
    saga_id = uuid4()
    quote_id = uuid4()
    message = event("QuoteGenerationRequestedV1", order_id, saga_id)
    with session_factory.begin() as session:
        session.add(
            QuoteModel(
                id=str(quote_id),
                order_id=str(order_id),
                saga_id=str(saga_id),
                correlation_id=message["correlation_id"],
                status="APPROVED",
                total=Decimal("150.00"),
            )
        )
        session.add(
            OutboxMessageModel(
                id=str(uuid4()),
                message_type="QuoteGeneratedV1",
                aggregate_id=str(quote_id),
                payload={"event_type": "QuoteGeneratedV1"},
            )
        )

    assert process_event(session_factory, message, FakeGateway()) is True

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(QuoteModel)) == 1
        assert session.scalar(select(func.count()).select_from(OutboxMessageModel)) == 1


def test_payment_request_uses_stable_intent_and_persists_qr_once(
    client, session_factory
):
    order_id = uuid4()
    saga_id = uuid4()
    quote_id = uuid4()
    message = event("PaymentCreationRequestedV1", order_id, saga_id)
    with session_factory.begin() as session:
        session.add(
            QuoteModel(
                id=str(quote_id),
                order_id=str(order_id),
                saga_id=str(saga_id),
                correlation_id=message["correlation_id"],
                status="APPROVED",
                total=Decimal("250.00"),
            )
        )
    gateway = FakeGateway()

    assert process_event(session_factory, message, gateway) is True
    assert process_event(session_factory, message, gateway) is False

    expected_id = uuid5(NAMESPACE_URL, f"payment-intent:{message['event_id']}")
    with session_factory() as session:
        payment = session.get(PaymentModel, str(expected_id))
        emitted = session.scalar(select(OutboxMessageModel))
        inbox_count = session.scalar(
            select(func.count()).select_from(InboxMessageModel)
        )
    assert len(gateway.created) == 1
    assert gateway.created[0].id == expected_id
    assert payment.external_id == "order-123"
    assert payment.qr_data == "qr-data"
    assert payment.status == "PENDING"
    assert emitted.message_type == "PaymentPendingV1"
    assert emitted.payload["order_id"] == str(order_id)
    assert inbox_count == 1
    response = client.get(f"/api/v1/payments/{expected_id}")
    assert response.status_code == 200
    assert response.json()["qr_data"] == "qr-data"
