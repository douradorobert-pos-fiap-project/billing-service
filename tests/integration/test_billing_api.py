from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.infrastructure.database.models import (
    OutboxMessageModel,
    QuoteLineModel,
)


def create_catalog_item(client: TestClient, name="Diagnóstico", price="100.00") -> str:
    response = client.post(
        "/api/v1/catalog-items",
        json={"name": name, "item_type": "SERVICE", "unit_price": price},
    )
    assert response.status_code == 201
    return response.json()["id"]


def create_quote(client: TestClient, item_id: str, correlation_id=None):
    order_id = uuid4()
    saga_id = uuid4()
    headers = {"X-Correlation-ID": str(correlation_id)} if correlation_id else {}
    response = client.post(
        "/api/v1/quotes",
        headers=headers,
        json={
            "order_id": str(order_id),
            "saga_id": str(saga_id),
            "items": [{"catalog_item_id": item_id, "quantity": 2}],
        },
    )
    return response, order_id, saga_id


def test_generate_quote_persists_snapshot_and_outbox_atomically(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    item_id = create_catalog_item(client, price="125.50")
    correlation_id = uuid4()

    response, order_id, saga_id = create_quote(client, item_id, correlation_id)

    assert response.status_code == 201
    assert response.headers["X-Correlation-ID"] == str(correlation_id)
    assert response.json()["order_id"] == str(order_id)
    assert response.json()["saga_id"] == str(saga_id)
    assert response.json()["total"] == "251.00"
    assert response.json()["status"] == "PENDING_APPROVAL"

    with session_factory() as session:
        line = session.scalar(select(QuoteLineModel))
        assert line is not None
        assert line.unit_price == Decimal("125.50")
        assert line.quantity == 2
        message = session.scalar(select(OutboxMessageModel))
        assert message is not None
        assert message.message_type == "QuoteGeneratedV1"


def test_approve_quote_is_idempotent_and_emits_one_event(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    item_id = create_catalog_item(client)
    quote, _, _ = create_quote(client, item_id)
    quote_id = quote.json()["id"]

    first = client.post(
        f"/api/v1/quotes/{quote_id}/approval", json={"action": "APPROVE"}
    )
    duplicate = client.post(
        f"/api/v1/quotes/{quote_id}/approval", json={"action": "APPROVE"}
    )

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "APPROVED"
    with session_factory() as session:
        approved_events = session.scalar(
            select(func.count())
            .select_from(OutboxMessageModel)
            .where(OutboxMessageModel.message_type == "QuoteApprovedV1")
        )
        assert approved_events == 1


def test_conflicting_quote_decision_returns_conflict(client: TestClient) -> None:
    item_id = create_catalog_item(client)
    quote, _, _ = create_quote(client, item_id)
    quote_id = quote.json()["id"]
    client.post(f"/api/v1/quotes/{quote_id}/approval", json={"action": "APPROVE"})

    response = client.post(
        f"/api/v1/quotes/{quote_id}/approval", json={"action": "REJECT"}
    )

    assert response.status_code == 409


def test_quote_can_be_queried(client: TestClient) -> None:
    item_id = create_catalog_item(client)
    quote, _, _ = create_quote(client, item_id)

    response = client.get(f"/api/v1/quotes/{quote.json()['id']}")

    assert response.status_code == 200
    assert response.json()["total"] == "200.00"
    assert len(response.json()["lines"]) == 1


def test_openapi_exposes_catalog_quote_and_approval(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/api/v1/catalog-items" in paths
    assert "/api/v1/quotes" in paths
    assert "/api/v1/quotes/{quote_id}" in paths
    assert "/api/v1/quotes/{quote_id}/approval" in paths
    assert "/api/v1/payments/by-order/{order_id}" in paths
