#!/bin/bash

# Wait for a service to become available
set -e

SERVICE_URL="$1"
MAX_ATTEMPTS="${2:-30}"
INTERVAL="${3:-5}"

source .github/scripts/logger.sh

log_info() {
    echo "ℹ️  INFO: $1"
}

log_success() {
    echo "✅ SUCCESS: $1"
}

log_failure() {
    echo "❌ FAILURE: $1"
}

log_info "Waiting for $SERVICE_URL (max attempts: $MAX_ATTEMPTS, interval: ${INTERVAL}s)"

for i in $(seq 1 "$MAX_ATTEMPTS"); do
    if curl -s -f "$SERVICE_URL" > /dev/null 2>&1; then
        log_success "Service is available after $((i * INTERVAL)) seconds"
        exit 0
    fi
    
    log_info "Attempt $i/$MAX_ATTEMPTS - retrying in ${INTERVAL}s..."
    sleep "$INTERVAL"
done

log_failure "Service at $SERVICE_URL never became available after $((MAX_ATTEMPTS * INTERVAL)) seconds"
exit 1