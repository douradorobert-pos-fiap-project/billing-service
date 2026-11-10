import json
from decimal import Decimal
from uuid import UUID

import httpx
import pytest

from src.application.ports.payment_gateway import (
    PaymentGatewayConfigurationError,
    PaymentGatewayInvalidResponse,
    PaymentGatewayTemporaryError,
)
from src.domain.payment import Payment, PaymentStatus
from src.infrastructure.mercado_pago.client import MercadoPagoClient

PAYMENT_ID = UUID("10000000-0000-4000-8000-000000000001")


def payment() -> Payment:
    return Payment(
        id=PAYMENT_ID,
        order_id=UUID("20000000-0000-4000-8000-000000000002"),
        quote_id=UUID("30000000-0000-4000-8000-000000000003"),
        amount=Decimal("125.50"),
    )


def client(handler: httpx.MockTransport) -> MercadoPagoClient:
    return MercadoPagoClient(
        access_token="TEST-token",
        base_url="https://mercado-pago.test",
        transport=handler,
    )


def test_create_dynamic_qr_sends_order_contract_and_stable_idempotency_key():
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json={
                "id": "ORD-123",
                "status": "created",
                "type_response": {"qr_data": "000201010212..."},
            },
        )

    gateway = client(httpx.MockTransport(respond))

    result = gateway.create_dynamic_qr(payment())

    assert result.external_id == "ORD-123"
    assert result.qr_data == "000201010212..."
    assert result.status is PaymentStatus.PENDING
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/orders"
    assert request.headers["Authorization"] == "Bearer TEST-token"
    assert request.headers["X-Idempotency-Key"] == str(PAYMENT_ID)
    assert json.loads(request.read()) == {
        "type": "qr",
        "total_amount": "125.50",
        "external_reference": "10000000-0000-4000-8000-000000000001",
        "config": {"qr": {"mode": "dynamic"}},
        "transactions": {"payments": [{"amount": "125.50"}]},
    }


@pytest.mark.parametrize(
    ("remote_status", "expected"),
    [
        ("created", PaymentStatus.PENDING),
        ("processed", PaymentStatus.APPROVED),
        ("canceled", PaymentStatus.REJECTED),
        ("expired", PaymentStatus.REJECTED),
        ("refunded", PaymentStatus.REFUNDED),
    ],
)
def test_get_payment_status_maps_order_status(remote_status, expected):
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "X-Idempotency-Key" not in request.headers
        return httpx.Response(200, json={"id": "ORD-123", "status": remote_status})

    assert (
        client(httpx.MockTransport(respond)).get_payment_status("ORD-123")
        is expected
    )


def test_refund_uses_dedicated_endpoint_and_idempotency_key():
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "ORD-123", "status": "refunded"})

    result = client(httpx.MockTransport(respond)).refund(
        "ORD-123", Decimal("25.00")
    )

    assert result.external_id == "ORD-123"
    assert result.amount == Decimal("25.00")
    assert result.status is PaymentStatus.REFUNDED
    assert requests[0].url.path == "/v1/orders/ORD-123/refund"
    assert requests[0].headers["X-Idempotency-Key"]
    assert json.loads(requests[0].read()) == {"amount": "25.00"}


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_retries_transient_responses_three_times(status_code):
    attempts = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code, json={"message": "try later"})

    with pytest.raises(PaymentGatewayTemporaryError):
        client(httpx.MockTransport(respond)).get_payment_status("ORD-123")

    assert attempts == 3


def test_timeout_is_reported_as_temporary_error_after_retries():
    attempts = 0

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(PaymentGatewayTemporaryError):
        client(httpx.MockTransport(timeout)).get_payment_status("ORD-123")

    assert attempts == 3


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"id": "ORD-123"}),
        httpx.Response(
            201,
            json={"id": "ORD-123", "status": "created", "type_response": {}},
        ),
    ],
)
def test_invalid_gateway_response_is_rejected(response):
    gateway = client(httpx.MockTransport(lambda request: response))

    with pytest.raises(PaymentGatewayInvalidResponse):
        if response.status_code == 201:
            gateway.create_dynamic_qr(payment())
        else:
            gateway.get_payment_status("ORD-123")


def test_missing_access_token_is_rejected(monkeypatch):
    monkeypatch.delenv("MERCADO_PAGO_ACCESS_TOKEN", raising=False)

    with pytest.raises(PaymentGatewayConfigurationError):
        MercadoPagoClient.from_env()
