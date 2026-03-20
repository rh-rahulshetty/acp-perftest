#!/bin/bash
# setup.sh — Pre-test setup for session-crud scenario.
#
# Sourced by ci-scripts/load-test.sh (inherits all exported variables).

info "Setting up session-crud scenario …"

AMBIENT_NAMESPACE="${AMBIENT_NAMESPACE:-ambient-code}"

# Obtain an auth token for the load test (if using OpenShift OAuth)
if [[ -z "${AUTH_TOKEN:-}" ]]; then
    AUTH_TOKEN="$(oc whoami -t 2>/dev/null || echo '')"
    export AUTH_TOKEN
fi

# Set the locust target host
if [[ -z "${LOCUST_HOST:-}" ]]; then
    ROUTE_HOST="$(kubectl get route frontend -n "$AMBIENT_NAMESPACE" -o jsonpath='{.spec.host}' 2>/dev/null || echo '')"
    if [[ -n "$ROUTE_HOST" ]]; then
        LOCUST_HOST="https://${ROUTE_HOST}"
    else
        LOCUST_HOST="http://$(kubectl get svc public-api-service -n "$AMBIENT_NAMESPACE" -o jsonpath='{.spec.clusterIP}'):8081"
    fi
    export LOCUST_HOST
fi

info "  LOCUST_HOST = $LOCUST_HOST"
info "  AUTH_TOKEN  = ${AUTH_TOKEN:+(set)}"
