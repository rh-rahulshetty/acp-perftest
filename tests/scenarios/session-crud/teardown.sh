#!/bin/bash
# teardown.sh — Post-test cleanup for session-crud scenario.
#
# Sourced by ci-scripts/load-test.sh after the test completes.

info "Tearing down session-crud scenario …"

PROJECT_NAME="${PROJECT_NAME:-loadtest-project}"
AMBIENT_NAMESPACE="${AMBIENT_NAMESPACE:-ambient-code}"

# Delete any leftover AgenticSession CRs created during the test
kubectl delete agenticsessions -n "$AMBIENT_NAMESPACE" -l loadtest=true --ignore-not-found 2>/dev/null || true

# Optionally delete the test project namespace
# kubectl delete ns "$PROJECT_NAME" --ignore-not-found 2>/dev/null || true

info "session-crud teardown complete"
