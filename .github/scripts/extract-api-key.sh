#!/bin/bash

# Extract API key from changedetection.io datastore
set -e

source .github/scripts/logger.sh

log_info "Extracting API key from changedetection..."
CID=$(docker ps --filter "status=running" --format '{{.Names}}' | grep changedetection)

if [ -z "$CID" ]; then
    log_failure "changedetection container not found"
    docker ps -a
    exit 1
fi

log_info "Container ID: $CID"

# Wait for datastore file
for i in {1..20}; do
    if docker exec $CID sh -c '[ -f /datastore/url-watches.json ]' 2>/dev/null; then
        log_success "Datastore file found"
        break
    fi
    log_info "Waiting for datastore... attempt $i/20"
    sleep 3
done

log_info "Copying datastore to local filesystem..."
docker cp $CID:/datastore/url-watches.json url-watches.json || true

if [ -f url-watches.json ]; then
    log_info "Extracting API key..."
    KEY=$(grep -oP '"api_access_token"\s*:\s*"\K[^"]+' url-watches.json || echo "")
    if [ -n "$KEY" ]; then
        echo "$KEY"
        log_success "API key extracted successfully"
    else
        log_failure "Could not extract API key from url-watches.json"
        echo "Debug: First 500 chars of datastore:"
        head -c 500 url-watches.json || true
        exit 1
    fi
else
    log_failure "url-watches.json not found in datastore"
    exit 1
fi