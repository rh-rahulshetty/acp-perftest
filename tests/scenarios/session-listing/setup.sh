#!/bin/bash
# setup.sh — Pre-test setup for session-listing scenario.
#
# Sourced by ci-scripts/load-test.sh (inherits all exported variables).

info "Setting up session-listing scenario …"

AMBIENT_NAMESPACE="${AMBIENT_NAMESPACE:-ambient-code}"
LOADTEST_SA="${LOADTEST_SA:-loadtest-sa}"

# Generate a service account token for the load test.
# The SA is created by setup-cluster.sh (config/loadtest-rbac.yaml).
if [[ -z "${AUTH_TOKEN:-}" ]]; then
    AUTH_TOKEN="$(kubectl create token "$LOADTEST_SA" -n "$AMBIENT_NAMESPACE" --duration=24h 2>/dev/null || echo '')"
    if [[ -z "$AUTH_TOKEN" ]]; then
        error "Failed to create token for SA $LOADTEST_SA in $AMBIENT_NAMESPACE"
        error "Ensure setup-cluster.sh has been run to create the loadtest SA"
    fi
    export AUTH_TOKEN
fi

# Point locust directly at the backend service via k8s FQDN
if [[ -z "${LOCUST_HOST:-}" ]]; then
    LOCUST_HOST="http://backend-service.${AMBIENT_NAMESPACE}.svc.cluster.local:8080"
    export LOCUST_HOST
fi

# Scenario-specific defaults
export PROJECT_NAME="${PROJECT_NAME:-session-listing}"
export SESSIONS_TO_CREATE="${SESSIONS_TO_CREATE:-20}"
export LOADTEST_SA_IDENTITY="system:serviceaccount:${AMBIENT_NAMESPACE}:${LOADTEST_SA}"

info "  LOCUST_HOST          = $LOCUST_HOST"
info "  AUTH_TOKEN            = ${AUTH_TOKEN:+(set)}"
info "  PROJECT_NAME         = $PROJECT_NAME"
info "  SESSIONS_TO_CREATE   = $SESSIONS_TO_CREATE"
info "  LOADTEST_SA_IDENTITY = $LOADTEST_SA_IDENTITY"
