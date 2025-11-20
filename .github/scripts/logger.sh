#!/bin/bash

# Logger functions for consistent output
log_info() {
    echo "ℹ️  INFO: $1"
}

log_success() {
    echo "✅ SUCCESS: $1"
}

log_warning() {
    echo "⚠️  WARNING: $1"
}

log_failure() {
    echo "❌ FAILURE: $1"
}

log_debug() {
    if [ "${DEBUG:-false}" = "true" ]; then
        echo "🐛 DEBUG: $1"
    fi
}

# Function to log with timestamp
log_with_timestamp() {
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $1"
}