#!/bin/bash
# wait-for-locust.sh — Wait for a LocustTest to complete or timeout.
# Usage: wait-for-locust.sh <test-name> <namespace> <timeout-seconds>

set -o nounset
set -o errexit
set -o pipefail

TEST_NAME="${1:?Usage: wait-for-locust.sh <test-name> <namespace> <timeout-seconds>}"
NAMESPACE="${2:?}"
TIMEOUT="${3:-600}"

POLL_INTERVAL=15
ELAPSED=0

echo "Waiting for LocustTest '$TEST_NAME' in namespace '$NAMESPACE' (timeout: ${TIMEOUT}s) …"

while [[ $ELAPSED -lt $TIMEOUT ]]; do
    # Check if master pod has completed
    PHASE=$(kubectl get pods -n "$NAMESPACE" \
        -l "locust-test=$TEST_NAME,role=master" \
        -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo "Pending")

    if [[ "$PHASE" == "Succeeded" || "$PHASE" == "Failed" ]]; then
        echo "LocustTest '$TEST_NAME' finished with phase: $PHASE"
        [[ "$PHASE" == "Succeeded" ]]
        exit $?
    fi

    # Also check if the container has exited (for jobs that stay in Running phase)
    CONTAINER_STATE=$(kubectl get pods -n "$NAMESPACE" \
        -l "locust-test=$TEST_NAME,role=master" \
        -o jsonpath='{.items[0].status.containerStatuses[0].state.terminated.reason}' 2>/dev/null || echo "")

    if [[ -n "$CONTAINER_STATE" ]]; then
        echo "LocustTest '$TEST_NAME' container terminated: $CONTAINER_STATE"
        [[ "$CONTAINER_STATE" == "Completed" ]]
        exit $?
    fi

    sleep $POLL_INTERVAL
    ELAPSED=$((ELAPSED + POLL_INTERVAL))
    echo "  … still running (${ELAPSED}s / ${TIMEOUT}s)"
done

echo "Timeout waiting for LocustTest '$TEST_NAME'"
exit 1
