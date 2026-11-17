import os
from decimal import Decimal
from time import perf_counter
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx

from src.application.ports.payment_gateway import (
    PaymentGatewayConfigurationError,
    PaymentGatewayError,
    PaymentGatewayInvalidResponse,
    PaymentGatewayResult,
    PaymentGatewayTemporaryError,
    RefundResult,
)
from src.domain.catalog import money
from src.domain.payment import Payment, PaymentStatus
from src.infrastructure.observability.logging import log_event
from src.infrastructure.observability.telemetry import record_event


class MercadoPagoClient:
    def __init__(
        self,
        access_token: str,
        base_url: str,
        transport: httpx.BaseTransport | None = None,
        external_pos_id: str = "",
        timeout: float = 5.0,
        max_attempts: int = 3,
    ) -> None:
        if not access_token.strip():
            raise PaymentGatewayConfigurationError(
                "MERCADO_PAGO_ACCESS_TOKEN is required"
            )
        if not external_pos_id.strip():
            raise PaymentGatewayConfigurationError(
                "MERCADO_PAGO_EXTERNAL_POS_ID is required"
            )
        self._access_token = access_token
        self._base_url = base_url.rstrip("/")
        self._external_pos_id = external_pos_id
        self._transport = transport
        self._timeout = timeout
        self._max_attempts = max_attempts

    @classmethod
    def from_env(cls) -> "MercadoPagoClient":
        return cls(
            access_token=os.getenv("MERCADO_PAGO_ACCESS_TOKEN", ""),
            base_url=os.getenv("MERCADO_PAGO_BASE_URL", "https://api.mercadopago.com"),
            external_pos_id=os.getenv("MERCADO_PAGO_EXTERNAL_POS_ID", ""),
        )

    def create_dynamic_qr(self, payment: Payment) -> PaymentGatewayResult:
        amount = format(payment.amount, ".2f")
        payload = {
            "type": "qr",
            "total_amount": amount,
            "external_reference": str(payment.id),
            "config": {
                "qr": {"external_pos_id": self._external_pos_id, "mode": "dynamic"}
            },
            "transactions": {"payments": [{"amount": amount}]},
        }
        response = self._request(
            "POST",
            "/v1/orders",
            idempotency_key=str(payment.id),
            json=payload,
        )
        data = self._json_object(response)
        external_id = data.get("id")
        type_response = data.get("type_response")
        qr_data = (
            type_response.get("qr_data") if isinstance(type_response, dict) else None
        )
        if not isinstance(external_id, str) or not isinstance(qr_data, str):
            raise PaymentGatewayInvalidResponse("order response lacks id or qr_data")
        return PaymentGatewayResult(
            external_id=external_id,
            qr_data=qr_data,
            status=self._status(data),
        )

    def get_payment_status(self, external_id: str) -> PaymentStatus:
        response = self._request("GET", f"/v1/orders/{external_id}")
        return self._status(self._json_object(response))

    def refund(self, external_id: str, amount: Decimal) -> RefundResult:
        refund_amount = money(amount)
        response = self._request(
            "POST",
            f"/v1/orders/{external_id}/refund",
            idempotency_key=str(
                uuid5(
                    NAMESPACE_URL, f"mercado-pago:{external_id}:refund:{refund_amount}"
                )
            ),
            json={"amount": format(refund_amount, ".2f")},
        )
        data = self._json_object(response)
        response_id = data.get("id")
        if not isinstance(response_id, str):
            raise PaymentGatewayInvalidResponse("refund response lacks id")
        return RefundResult(
            external_id=response_id,
            amount=refund_amount,
            status=self._status(data),
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        idempotency_key: str | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._access_token}"}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key

        for attempt in range(self._max_attempts):
            started = perf_counter()
            try:
                with httpx.Client(
                    base_url=self._base_url,
                    timeout=self._timeout,
                    transport=self._transport,
                ) as http_client:
                    response = http_client.request(
                        method, path, headers=headers, json=json
                    )
            except httpx.RequestError as exc:
                log_event(
                    "mercado_pago.request",
                    method=method,
                    path=path,
                    outcome="network_error",
                    attempt=attempt + 1,
                )
                if attempt + 1 == self._max_attempts:
                    raise PaymentGatewayTemporaryError(
                        "Mercado Pago request failed temporarily"
                    ) from exc
                continue

            if response.status_code == 429 or response.status_code >= 500:
                log_event(
                    "mercado_pago.request",
                    method=method,
                    path=path,
                    outcome="retryable_error",
                    status_code=response.status_code,
                    attempt=attempt + 1,
                )
                if attempt + 1 == self._max_attempts:
                    raise PaymentGatewayTemporaryError(
                        f"Mercado Pago unavailable: HTTP {response.status_code}"
                    )
                continue
            if response.is_error:
                raise PaymentGatewayError(
                    f"Mercado Pago rejected request: HTTP {response.status_code}"
                )
            duration_ms = round((perf_counter() - started) * 1000, 2)
            attributes = {
                "method": method,
                "path": path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "attempt": attempt + 1,
            }
            log_event("mercado_pago.request", outcome="success", **attributes)
            record_event("MercadoPagoRequest", attributes)
            return response

        raise AssertionError("retry loop exhausted unexpectedly")

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise PaymentGatewayInvalidResponse("response is not valid JSON") from exc
        if not isinstance(data, dict):
            raise PaymentGatewayInvalidResponse("response must be a JSON object")
        return data

    @staticmethod
    def _status(data: dict[str, Any]) -> PaymentStatus:
        remote_status = data.get("status")
        statuses = {
            "created": PaymentStatus.PENDING,
            "processed": PaymentStatus.APPROVED,
            "canceled": PaymentStatus.REJECTED,
            "expired": PaymentStatus.REJECTED,
            "refunded": PaymentStatus.REFUNDED,
        }
        if not isinstance(remote_status, str):
            raise PaymentGatewayInvalidResponse(
                f"unsupported order status: {remote_status!r}"
            )
        try:
            return statuses[remote_status]
        except KeyError as exc:
            raise PaymentGatewayInvalidResponse(
                f"unsupported order status: {remote_status!r}"
            ) from exc
