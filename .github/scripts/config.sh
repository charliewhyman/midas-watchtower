#!/bin/bash
# Configuration constants for AI Safety Monitor

# Retry configuration
readonly MAX_WAIT_ATTEMPTS=30
readonly WAIT_INTERVAL=5
readonly MAX_MONITOR_ATTEMPTS=3
readonly RESTART_DELAY=15

# Timeouts
readonly DOCKER_OPERATION_TIMEOUT=120  # seconds
readonly API_READY_TIMEOUT=$((MAX_WAIT_ATTEMPTS * WAIT_INTERVAL))

# Logging
readonly LOG_DIR="logs"
readonly DOCKER_LOG_DIR="docker-logs"
readonly REPORT_DIR="data/reports"