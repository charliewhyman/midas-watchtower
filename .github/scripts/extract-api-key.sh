#!/bin/bash

# Extract API key from changedetection.io datastore
set -e

DATASTORE_FILE="$1"

if [ -z "$DATASTORE_FILE" ]; then
    echo "❌ Error: No datastore file provided"
    exit 1
fi

if [ ! -f "$DATASTORE_FILE" ]; then
    echo "❌ Error: Datastore file not found: $DATASTORE_FILE"
    exit 1
fi

# Extract API key using jq
API_KEY=$(jq -r '.settings | .api_key // empty' "$DATASTORE_FILE" 2>/dev/null || true)

if [ -z "$API_KEY" ]; then
    # Try alternative extraction method
    API_KEY=$(grep -o '"api_key":[[:space:]]*"[^"]*"' "$DATASTORE_FILE" | cut -d'"' -f4 | head -1)
fi

if [ -z "$API_KEY" ]; then
    echo "❌ Error: Could not extract API key from datastore"
    exit 1
fi

echo "$API_KEY"