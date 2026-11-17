import os
import time

from src.infrastructure.database.session import SessionFactory
from src.infrastructure.mercado_pago.client import MercadoPagoClient
from src.infrastructure.messaging.consumer import SQSConsumer
from src.infrastructure.messaging.handler import process_event
from src.infrastructure.messaging.outbox_relay import (
    OutboxRelay,
    SQLAlchemyOutboxStore,
)
from src.infrastructure.messaging.sqs_client import SQSClient
from src.infrastructure.observability.logging import log_event

OUTBOUND_EVENT_TYPES = (
    "QuoteGeneratedV1",
    "QuoteApprovedV1",
    "QuoteRejectedV1",
    "PaymentPendingV1",
    "PaymentConfirmedV1",
    "PaymentRejectedV1",
    "PaymentRefundedV1",
)


def main() -> None:
    environment = os.getenv("APP_ENV", "local")
    sqs = SQSClient.from_env()
    queue_urls = {
        service: sqs.queue_url(f"{environment}-{service}-events")
        for service in ("os-service", "billing-service")
    }
    routes = {
        event_type: queue_urls["os-service"] for event_type in OUTBOUND_EVENT_TYPES
    }
    relay = OutboxRelay(SQLAlchemyOutboxStore(SessionFactory), sqs, routes)
    consumer = SQSConsumer(sqs, queue_urls["billing-service"], wait_time_seconds=10)
    gateway = MercadoPagoClient.from_env()
    log_event("worker.started", service="billing-service")

    while True:
        relay.run_once()
        result = consumer.poll_once(
            lambda message: process_event(SessionFactory, message, gateway)
        )
        if result == "empty":
            time.sleep(0.5)


if __name__ == "__main__":
    main()
