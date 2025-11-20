#!/bin/bash
# Configuration constants for AI Safety Monitor

# Service endpoints
readonly CHANGEDETECTION_PORT=5000
readonly CHANGEDETECTION_HOST=localhost
readonly CHANGEDETECTION_URL="http://${CHANGEDETECTION_HOST}:${CHANGEDETECTION_PORT}"
readonly CHANGEDETECTION_CONTAINER_URL="http://changedetection:${CHANGEDETECTION_PORT}"

# Retry configuration
readonly MAX_WAIT_ATTEMPTS=30
readonly WAIT_INTERVAL=5
readonly MAX_MONITOR_ATTEMPTS=3
readonly RESTART_DELAY=15

# Datastore paths
readonly DATASTORE_FILE="/datastore/url-watches.json"
readonly LOCAL_DATASTORE="url-watches.json"
readonly API_KEY_FIELD="api_access_token"

# Timeouts
readonly DOCKER_OPERATION_TIMEOUT=120  # seconds
readonly API_READY_TIMEOUT=$((MAX_WAIT_ATTEMPTS * WAIT_INTERVAL))

# Logging
readonly LOG_DIR="logs"
readonly DOCKER_LOG_DIR="docker-logs"
readonly REPORT_DIR="data/reports"