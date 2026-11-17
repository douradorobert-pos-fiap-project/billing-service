from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from src.application.generate_quote import generate_quote
from src.application.ports.payment_gateway import PaymentGateway
from src.domain.payment import Payment, PaymentStatus
from src.infrastructure.database.inbox import claim_event
from src.infrastructure.database.models import (
    InboxMessageModel,
    PaymentModel,
    QuoteModel,
)
from src.infrastructure.database.outbox import payment_event
from src.infrastructure.database.repositories import get_catalog_item, persist_quote
from src.infrastructure.messaging.envelope import EventEnvelopeV1

CONSUMER_NAME = "billing-workflows"


def process_event(
    session_factory: sessionmaker,
    message: dict,
    payment_gateway: PaymentGateway,
) -> bool:
    event = EventEnvelopeV1.model_validate(message)
    if event.event_type == "QuoteGenerationRequestedV1":
        return _generate_quote(session_factory, event)
    if event.event_type == "PaymentCreationRequestedV1":
        return _create_payment(session_factory, event, payment_gateway)
    raise ValueError(f"billing cannot handle {event.event_type}")


def _generate_quote(session_factory: sessionmaker, event: EventEnvelopeV1) -> bool:
    with session_factory.begin() as session:
        if not claim_event(session, event.event_id, CONSUMER_NAME):
            return False
        existing_quote = session.scalar(
            select(QuoteModel)
            .where(
                QuoteModel.order_id == str(event.order_id),
                QuoteModel.saga_id == str(event.saga_id),
            )
            .order_by(QuoteModel.created_at.desc())
        )
        if existing_quote is not None:
            return True
        service_ids = event.payload.get("recommended_service_ids")
        if not isinstance(service_ids, list) or not service_ids:
            raise ValueError("quote request must include recommended_service_ids")
        item_ids = [UUID(value) for value in service_ids]
        selections = []
        for item_id in item_ids:
            item = get_catalog_item(session, item_id)
            if item is None or not item.active:
                raise LookupError(f"active catalog item {item_id} not found")
            selections.append((item, 1))
        quote = generate_quote(
            event.order_id, event.saga_id, event.correlation_id, selections
        )
        persist_quote(session, quote)
    return True


def _create_payment(
    session_factory: sessionmaker,
    event: EventEnvelopeV1,
    payment_gateway: PaymentGateway,
) -> bool:
    payment_id = uuid5(NAMESPACE_URL, f"payment-intent:{event.event_id}")
    with session_factory() as session:
        if (
            session.scalar(
                select(InboxMessageModel).where(
                    InboxMessageModel.event_id == str(event.event_id),
                    InboxMessageModel.consumer == CONSUMER_NAME,
                )
            )
            is not None
        ):
            return False
        quote = session.scalar(
            select(QuoteModel)
            .where(
                QuoteModel.saga_id == str(event.saga_id),
                QuoteModel.order_id == str(event.order_id),
                QuoteModel.status == "APPROVED",
            )
            .order_by(QuoteModel.created_at.desc())
        )
        if quote is None:
            raise LookupError("approved quote not found for payment request")
        payment = Payment(
            id=payment_id,
            order_id=event.order_id,
            quote_id=UUID(quote.id),
            amount=quote.total,
        )

    result = payment_gateway.create_dynamic_qr(payment)
    event_types = {
        PaymentStatus.PENDING: "PaymentPendingV1",
        PaymentStatus.APPROVED: "PaymentConfirmedV1",
        PaymentStatus.REJECTED: "PaymentRejectedV1",
    }
    outgoing_type = event_types.get(result.status)
    if outgoing_type is None:
        raise ValueError(f"unsupported initial payment status: {result.status}")

    with session_factory.begin() as session:
        if not claim_event(session, event.event_id, CONSUMER_NAME):
            return False
        quote = session.get(QuoteModel, str(payment.quote_id))
        if quote is None:
            raise LookupError("quote disappeared during payment creation")
        model = session.get(PaymentModel, str(payment_id))
        if model is None:
            model = PaymentModel(
                id=str(payment_id),
                order_id=str(event.order_id),
                quote_id=str(payment.quote_id),
                amount=payment.amount,
                status=result.status.value,
                external_id=result.external_id,
                qr_data=result.qr_data,
            )
            session.add(model)
        else:
            model.status = result.status.value
            model.external_id = result.external_id
            model.qr_data = result.qr_data
        session.flush()
        session.add(payment_event(model, quote, outgoing_type, str(event.event_id)))
    return True
