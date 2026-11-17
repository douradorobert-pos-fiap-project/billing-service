from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from sqlalchemy import func, select

from src.application.ports.payment_gateway import PaymentGatewayResult, RefundResult
from src.domain.payment import Payment, PaymentStatus
from src.infrastructure.database.models import (
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


def event(
    event_type: str, order_id=None, saga_id=None, payload=None, event_id=None
) -> dict:
    return {
        "event_id": str(event_id or uuid4()),
        "event_type": event_type,
        "event_version": 1,
        "occurred_at": datetime.now(UTC).isoformat(),
        "correlation_id": str(uuid4()),
        "causation_id": str(uuid4()),
        "saga_id": str(saga_id or uuid4()),
        "order_id": str(order_id or uuid4()),
        "payload": payload or {},
    }


def test_quote_request_is_acknowledged_without_automatically_pricing_services(
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
    # A human must choose the catalog items and prices using the quote API.
    gateway = FakeGateway()
    assert process_event(session_factory, message, gateway) is True
    assert process_event(session_factory, message, gateway) is False

    with session_factory() as session:
        quote_count = session.scalar(select(func.count()).select_from(QuoteModel))
        line_count = session.scalar(select(func.count()).select_from(QuoteLineModel))
        event_count = session.scalar(
            select(func.count()).select_from(OutboxMessageModel)
        )
        inbox_count = session.scalar(
            select(func.count()).select_from(InboxMessageModel)
        )
    assert quote_count == 0
    assert line_count == 0
    assert event_count == 0
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

    expected_id = uuid5(NAMESPACE_URL, f"payment-intent:{quote_id}")
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
    response = client.get(f"/api/v1/payments/by-order/{order_id}")
    assert response.status_code == 200
    assert response.json()["qr_data"] == "qr-data"


def test_different_payment_events_for_one_quote_create_only_one_gateway_order(
    session_factory,
):
    order_id = uuid4()
    saga_id = uuid4()
    quote_id = uuid4()
    first = event("PaymentCreationRequestedV1", order_id, saga_id)
    second = event("PaymentCreationRequestedV1", order_id, saga_id)
    with session_factory.begin() as session:
        session.add(
            QuoteModel(
                id=str(quote_id),
                order_id=str(order_id),
                saga_id=str(saga_id),
                correlation_id=first["correlation_id"],
                status="APPROVED",
                total=Decimal("250.00"),
            )
        )
    gateway = FakeGateway()

    assert process_event(session_factory, first, gateway) is True
    assert process_event(session_factory, second, gateway) is True

    with session_factory() as session:
        payments = session.scalars(select(PaymentModel)).all()
        pending_events = session.scalars(
            select(OutboxMessageModel).where(
                OutboxMessageModel.message_type == "PaymentPendingV1"
            )
        ).all()
    assert len(gateway.created) == 1
    assert len(payments) == 1
    assert len(pending_events) == 1


def test_gateway_failure_leaves_payment_request_retryable(session_factory):
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
                total=Decimal("25.00"),
            )
        )

    class FlakyGateway(FakeGateway):
        attempts = 0

        def create_dynamic_qr(self, payment: Payment) -> PaymentGatewayResult:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("temporary gateway failure")
            return super().create_dynamic_qr(payment)

    gateway = FlakyGateway()
    with pytest.raises(RuntimeError, match="temporary gateway failure"):
        process_event(session_factory, message, gateway)

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(InboxMessageModel)) == 0
        assert session.scalar(select(func.count()).select_from(PaymentModel)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxMessageModel)) == 0

    assert process_event(session_factory, message, gateway) is True
    assert gateway.attempts == 2
    assert len(gateway.created) == 1


def test_payment_request_rejects_multiple_approved_quotes_for_same_saga(
    session_factory,
):
    order_id = uuid4()
    saga_id = uuid4()
    message = event("PaymentCreationRequestedV1", order_id, saga_id)
    with session_factory.begin() as session:
        for amount in (Decimal("25.00"), Decimal("50.00")):
            session.add(
                QuoteModel(
                    id=str(uuid4()),
                    order_id=str(order_id),
                    saga_id=str(saga_id),
                    correlation_id=message["correlation_id"],
                    status="APPROVED",
                    total=amount,
                )
            )
    gateway = FakeGateway()

    with pytest.raises(LookupError, match="multiple approved quotes"):
        process_event(session_factory, message, gateway)

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(InboxMessageModel)) == 0
        assert session.scalar(select(func.count()).select_from(PaymentModel)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxMessageModel)) == 0
    assert gateway.created == []


def test_payment_lookup_hides_qr_after_payment_is_approved(client, session_factory):
    order_id = uuid4()
    payment_id = uuid4()
    with session_factory.begin() as session:
        session.add(
            QuoteModel(
                id=str(uuid4()),
                order_id=str(order_id),
                saga_id=str(uuid4()),
                correlation_id=str(uuid4()),
                status="APPROVED",
                total=Decimal("10.00"),
            )
        )
        quote = session.scalar(select(QuoteModel))
        session.add(
            PaymentModel(
                id=str(payment_id),
                order_id=str(order_id),
                quote_id=quote.id,
                amount=quote.total,
                status="APPROVED",
                external_id="order-123",
                qr_data="qr-data",
            )
        )

    response = client.get(f"/api/v1/payments/by-order/{order_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "APPROVED"
    assert response.json()["qr_data"] is None


def test_payment_lookup_by_order_rejects_ambiguous_payment_history(
    client, session_factory
):
    order_id = uuid4()
    with session_factory.begin() as session:
        for _ in range(2):
            quote = QuoteModel(
                id=str(uuid4()),
                order_id=str(order_id),
                saga_id=str(uuid4()),
                correlation_id=str(uuid4()),
                status="APPROVED",
                total=Decimal("10.00"),
            )
            session.add(quote)
            session.flush()
            session.add(
                PaymentModel(
                    id=str(uuid4()),
                    order_id=str(order_id),
                    quote_id=quote.id,
                    amount=quote.total,
                    status="PENDING",
                    external_id=f"order-{quote.id}",
                    qr_data="qr-data",
                )
            )

    response = client.get(f"/api/v1/payments/by-order/{order_id}")

    assert response.status_code == 409
