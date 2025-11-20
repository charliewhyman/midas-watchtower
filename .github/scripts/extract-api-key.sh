#!/bin/bash

# Extract API key from changedetection.io datastore - MINIMAL VERSION
set -e

CID=$(docker ps --filter "status=running" --format '{{.Names}}' | grep changedetection)
[ -z "$CID" ] && exit 1

# Wait for file
for i in {1..20}; do
    docker exec $CID sh -c '[ -f /datastore/url-watches.json ]' 2>/dev/null && break
    sleep 3
done

docker cp $CID:/datastore/url-watches.json url-watches.json 2>/dev/null || exit 1
grep -oP '"api_access_token"\s*:\s*"\K[^"]+' url-watches.json || exit 1