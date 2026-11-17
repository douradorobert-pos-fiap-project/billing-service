# Billing Service

Microsserviço proprietário de catálogo, orçamentos, decisões e pagamentos. Persiste em PostgreSQL próprio, integra Mercado Pago somente com credenciais de teste e publica eventos via Outbox/SQS.

## Executar e validar

```bash
poetry install
poetry run uvicorn src.api.main:app --reload --port 8002
poetry run pytest --cov=src --cov-report=term-missing --cov-fail-under=80
bash scripts/quality.sh
```

Swagger: `http://localhost:8002/docs`; OpenAPI: `/openapi.json`; health: `/health`. Rotas: `/api/v1/catalog-items`, `/api/v1/quotes`, `/api/v1/payments` e `/api/v1/webhooks/mercado-pago`.

Defina token e segredo de webhook apenas por variável/Secret Kubernetes. Webhooks validam assinatura, rejeitam notificações live no ambiente de teste e são idempotentes. Pull requests executam qualidade e build Docker local, sem publicar ou acessar a AWS. O workflow publica a imagem por SHA e resolve seu digest, sincroniza o Secret, executa `alembic upgrade head` e somente então atualiza o Deployment quando iniciado manualmente por `workflow_dispatch` no environment `sandbox`.

## Configuração do sandbox

GitHub Variables:

```text
AWS_REGION
EKS_CLUSTER_NAME
RDS_ENDPOINT
DB_PORT
BILLING_DATABASE
BILLING_DATABASE_USERNAME
```

GitHub Secrets:

```text
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_SESSION_TOKEN
BILLING_DB_PASSWORD
NEW_RELIC_LICENSE_KEY
MERCADO_PAGO_ACCESS_TOKEN
MERCADO_PAGO_EXTERNAL_POS_ID
MERCADO_PAGO_WEBHOOK_SECRET
```

Use somente credenciais de teste do Mercado Pago. `scripts/sync-k8s-secrets.sh` monta `DATABASE_URL` com usuário e senha percent-encoded e atualiza `billing-service-secrets` sem gravar o manifesto renderizado. O mesmo Secret recebe as credenciais temporárias AWS para SQS e as credenciais de pagamento. Nunca habilite `set -x` nesse fluxo.

O Job `k8s/migration-job.yaml` executa dentro do EKS e alcança o RDS privado. Em ambiente `sandbox`, Alembic rejeita a execução se `DATABASE_URL` não estiver presente; o endereço de desenvolvimento do `alembic.ini` permanece somente como conveniência local.

## Cotação manual e pagamento por QR

`QuoteGenerationRequestedV1` sinaliza que o diagnóstico está pronto para revisão. O worker do Billing registra a mensagem como processada, mas não associa automaticamente os serviços recomendados a itens ou preços do catálogo. Uma pessoa seleciona os itens e usa `POST /api/v1/quotes`, depois aprova a cotação pelo endpoint de aprovação existente.

Após a conclusão do reparo, a saga envia `PaymentCreationRequestedV1`. O Billing cria uma única intenção por cotação aprovada, usando uma chave de idempotência estável no Mercado Pago. Consulte `GET /api/v1/payments/by-order/{order_id}` para obter valor, estado e `qr_data` enquanto o pagamento estiver pendente. O QR deixa de ser retornado quando o pagamento sai do estado pendente. O conteúdo do QR não é gravado em logs.

## Webhook de QR no sandbox

Configure o webhook na aplicação Mercado Pago usada pela conta vendedora de teste. Para credenciais de teste, a documentação de QR orienta configurar o usuário vendedor de teste na aba **Modo de produção** e selecionar o evento **Order (Mercado Pago)**. Use como URL:

```text
<api_gateway_endpoint>/api/v1/webhooks/mercado-pago
```

Obtenha `<api_gateway_endpoint>` com `terraform output -raw api_gateway_endpoint` no ambiente `sandbox`. O API Gateway público encaminha a chamada pelo VPC Link ao ALB interno; não torne o ALB público.

Copie a assinatura secreta gerada pelo Mercado Pago para o GitHub Secret `MERCADO_PAGO_WEBHOOK_SECRET` do ambiente `sandbox` e execute o workflow do Billing para sincronizá-la ao Kubernetes. Não coloque o segredo na URL, no corpo da notificação ou no repositório. O endpoint valida `x-signature` e consulta a order na API do Mercado Pago antes de alterar o pagamento; no sandbox, também rejeita notificações com `live_mode=true`.

Use **Simular** na configuração de Webhooks para validar a URL antes de depender de uma nova compra de teste. A simulação deve receber HTTP 200; uma notificação válida para uma order processada deve atualizar o Billing e publicar `PaymentConfirmedV1` para a saga.
