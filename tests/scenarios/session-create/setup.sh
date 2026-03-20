#!/bin/bash
# setup.sh — Pre-test setup for session-create scenario.
#
# Sourced by ci-scripts/load-test.sh (inherits all exported variables
# including AUTH_TOKEN, LOADTEST_SA_IDENTITY, LOCUST_HOST).

info "Setting up session-create scenario …"

AMBIENT_NAMESPACE="${AMBIENT_NAMESPACE:-ambient-code}"

# Point locust directly at the backend service via k8s FQDN
if [[ -z "${LOCUST_HOST:-}" ]]; then
    LOCUST_HOST="http://backend-service.${AMBIENT_NAMESPACE}.svc.cluster.local:8080"
    export LOCUST_HOST
fi

# Scenario-specific defaults
export PROJECT_NAME="${PROJECT_NAME:-session-create}"

info "  LOCUST_HOST          = $LOCUST_HOST"
info "  PROJECT_NAME         = $PROJECT_NAME"
