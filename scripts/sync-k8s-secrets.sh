#!/usr/bin/env bash
set -euo pipefail
set +x

for name in \
  RDS_ENDPOINT DB_PORT BILLING_DATABASE BILLING_DATABASE_USERNAME BILLING_DB_PASSWORD \
  NEW_RELIC_LICENSE_KEY MERCADO_PAGO_ACCESS_TOKEN MERCADO_PAGO_EXTERNAL_POS_ID \
  MERCADO_PAGO_WEBHOOK_SECRET \
  AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN; do
  if [[ -z "${!name:-}" ]]; then
    printf 'Missing required deployment configuration: %s\n' "$name" >&2
    exit 2
  fi
done

database_url="$(python3 <<'PY'
import os
from urllib.parse import quote

username = quote(os.environ["BILLING_DATABASE_USERNAME"], safe="")
password = quote(os.environ["BILLING_DB_PASSWORD"], safe="")
host = os.environ["RDS_ENDPOINT"]
port = os.environ["DB_PORT"]
database = quote(os.environ["BILLING_DATABASE"], safe="")
print(f"postgresql+psycopg://{username}:{password}@{host}:{port}/{database}")
PY
)"

kubectl --namespace oficina create secret generic billing-service-secrets \
  --from-literal="DATABASE_URL=${database_url}" \
  --from-literal="NEW_RELIC_LICENSE_KEY=${NEW_RELIC_LICENSE_KEY}" \
  --from-literal="MERCADO_PAGO_ACCESS_TOKEN=${MERCADO_PAGO_ACCESS_TOKEN}" \
  --from-literal="MERCADO_PAGO_EXTERNAL_POS_ID=${MERCADO_PAGO_EXTERNAL_POS_ID}" \
  --from-literal="MERCADO_PAGO_WEBHOOK_SECRET=${MERCADO_PAGO_WEBHOOK_SECRET}" \
  --from-literal="AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}" \
  --from-literal="AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}" \
  --from-literal="AWS_SESSION_TOKEN=${AWS_SESSION_TOKEN}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

printf 'Billing Service Kubernetes Secret synchronized\n'
