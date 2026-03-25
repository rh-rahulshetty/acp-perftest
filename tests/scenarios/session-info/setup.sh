#!/bin/bash
# setup.sh — Pre-test setup for session-info scenario.
#
# Sourced by ci-scripts/load-test.sh (inherits all exported variables
# including AUTH_TOKEN, LOADTEST_SA_IDENTITY, LOCUST_HOST).

info "Setting up session-info scenario …"

AMBIENT_NAMESPACE="${AMBIENT_NAMESPACE:-ambient-code}"

# Point locust directly at the backend service via k8s FQDN
if [[ -z "${LOCUST_HOST:-}" ]]; then
    LOCUST_HOST="http://backend-service.${AMBIENT_NAMESPACE}.svc.cluster.local:8080"
    export LOCUST_HOST
fi

# Scenario-specific defaults
#   LOAD_STEPS: stepped profile, e.g. "5m:10,5m:50" (empty = flat profile)
export PROJECT_NAME="${PROJECT_NAME:-session-info}"
export LOAD_STEPS="${LOAD_STEPS:-}"

info "  LOCUST_HOST          = $LOCUST_HOST"
info "  PROJECT_NAME         = $PROJECT_NAME"
if [[ -n "$LOAD_STEPS" ]]; then
    info "  LOAD_STEPS           = $LOAD_STEPS"
else
    info "  LOAD_STEPS           = (flat profile, using TEST_USERS/TEST_RUN_TIME)"
fi
