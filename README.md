# Billing Service

Microsserviço proprietário de catálogo, orçamentos, decisões e pagamentos. Persiste em PostgreSQL próprio, integra Mercado Pago somente com credenciais de teste e publica eventos via Outbox/SQS.

## Executar e validar

```bash
poetry install
poetry run uvicorn src.api.main:app --reload --port 8002
poetry run pytest --cov=src --cov-report=term-missing --cov-fail-under=80
bash scripts/quality.sh
```

Swagger: `http://localhost:8002/docs`; OpenAPI: `/openapi.json`; health: `/health`. Rotas: `/api/v1/catalog-items`, `/api/v1/quotes` e `/api/v1/webhooks/mercado-pago`.

Defina token e segredo de webhook apenas por variável/Secret Kubernetes. Webhooks validam assinatura, rejeitam notificações live no ambiente de teste e são idempotentes. O pipeline independente publica imagem imutável pelo SHA somente após os gates.
