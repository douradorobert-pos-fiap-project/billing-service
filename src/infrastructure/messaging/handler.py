from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from src.application.ports.payment_gateway import PaymentGateway
from src.domain.payment import Payment, PaymentStatus
from src.infrastructure.database.inbox import claim_event
from src.infrastructure.database.models import PaymentModel, QuoteModel
from src.infrastructure.database.outbox import payment_event
from src.infrastructure.messaging.envelope import EventEnvelopeV1

CONSUMER_NAME = "billing-workflows"


def process_event(
    session_factory: sessionmaker,
    message: dict,
    payment_gateway: PaymentGateway,
) -> bool:
    event = EventEnvelopeV1.model_validate(message)
    if event.event_type == "QuoteGenerationRequestedV1":
        return _acknowledge_manual_quote_request(session_factory, event)
    if event.event_type == "PaymentCreationRequestedV1":
        return _create_payment(session_factory, event, payment_gateway)
    raise ValueError(f"billing cannot handle {event.event_type}")


def _acknowledge_manual_quote_request(
    session_factory: sessionmaker, event: EventEnvelopeV1
) -> bool:
    with session_factory.begin() as session:
        return claim_event(session, event.event_id, CONSUMER_NAME)


def _create_payment(
    session_factory: sessionmaker,
    event: EventEnvelopeV1,
    payment_gateway: PaymentGateway,
) -> bool:
    with session_factory.begin() as session:
        if not claim_event(session, event.event_id, CONSUMER_NAME):
            return False
        quote = session.scalar(
            select(QuoteModel)
            .where(
                QuoteModel.saga_id == str(event.saga_id),
                QuoteModel.order_id == str(event.order_id),
                QuoteModel.status == "APPROVED",
            )
            .with_for_update()
        )
        if quote is None:
            raise LookupError("approved quote not found for payment request")
        existing_payment = session.scalar(
            select(PaymentModel)
            .where(PaymentModel.quote_id == quote.id)
            .with_for_update()
        )
        if existing_payment is not None:
            return True
        payment_id = uuid5(NAMESPACE_URL, f"payment-intent:{quote.id}")
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
        session.flush()
        session.add(payment_event(model, quote, outgoing_type, str(event.event_id)))
    return True
