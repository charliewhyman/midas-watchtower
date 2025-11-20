# .github/scripts/wait-for-service.sh
#!/bin/bash
set -euo pipefail

SERVICE_URL="${1:?Service URL required}"
MAX_ATTEMPTS="${2:-30}"
INTERVAL="${3:-5}"
TIMEOUT_TOTAL=$((MAX_ATTEMPTS * INTERVAL))

echo "Waiting for $SERVICE_URL (timeout: ${TIMEOUT_TOTAL}s)..."

for i in $(seq 1 "$MAX_ATTEMPTS"); do
  if curl -sf "$SERVICE_URL" > /dev/null 2>&1; then
    echo "✅ Service is ready"
    return 0
  fi
  echo "Attempt $i/$MAX_ATTEMPTS - retrying in ${INTERVAL}s..."
  sleep "$INTERVAL"
done

echo "❌ Service failed to become ready after ${TIMEOUT_TOTAL}s"
return 1