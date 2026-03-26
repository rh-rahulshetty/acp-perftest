#!/bin/bash

set -o nounset
set -o errexit
set -o pipefail

source "$(dirname "$0")/lib.sh"
source "$(dirname "$0")/locust.sh"

# Ambient Code platform repository
AMBIENT_CODE_GIT="${AMBIENT_CODE_GIT:-https://github.com/ambient-code/platform.git}"
AMBIENT_CODE_COMMIT="${AMBIENT_CODE_COMMIT:-02b2a7d8dad0e38d1390046a340ca38dab35650a}"

# Vertex configuration
ENABLE_VERTEX="${ENABLE_VERTEX:-false}"
if is_truthy "$ENABLE_VERTEX"; then
    export ENABLE_VERTEX
    export GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS:-}"
    export ANTHROPIC_VERTEX_PROJECT_ID="${ANTHROPIC_VERTEX_PROJECT_ID:-}"
    export CLOUD_ML_REGION="${CLOUD_ML_REGION:-}"
else
    unset GOOGLE_APPLICATION_CREDENTIALS ANTHROPIC_VERTEX_PROJECT_ID CLOUD_ML_REGION
fi


# Deployment configuration
LOCAL_DIR="${LOCAL_DIR:-workspaces/ambient-code}"
MANIFESTS_DIR="$LOCAL_DIR/components/manifests"

AMBIENT_NAMESPACE="ambient-code"

function setup() {
    if check_acp_installed "$AMBIENT_NAMESPACE"; then
        fatal "ACP may already be installed on this cluster. Aborting setup to avoid conflicts."
    fi

    mkdir -p $LOCAL_DIR
    
    # Clone the Ambient Code platform repository
    clone_repository $AMBIENT_CODE_GIT $AMBIENT_CODE_COMMIT $LOCAL_DIR

    oc create namespace $AMBIENT_NAMESPACE || true

    # Generate passwords and apply secrets to the cluster
    export AMBIENT_NAMESPACE
    cp config/ambient/.env "$MANIFESTS_DIR/.env"
    apply_secrets

    # Patch unleash image to use GHCR mirror (avoids Docker Hub rate limits)
    info "Applying GHCR mirror patch for unleash image"
    git -C $LOCAL_DIR apply "$PWD/config/ambient/use-ghcr-mirror.patch"

    # Deploy ACP
    make deploy -C $LOCAL_DIR
    deploy_rc=$?

    # Revert the GHCR mirror patch to keep workspace clean
    info "Reverting GHCR mirror patch"
    git -C $LOCAL_DIR apply -R "$PWD/config/ambient/use-ghcr-mirror.patch"

    if [ $deploy_rc -eq 0 ]; then
        info "Deployed successfully"
    else
        fatal "Deployment failed"
    fi

    # The production overlay hardcodes USE_VERTEX=1 in operator-config-openshift.yaml.
    # Override it when Vertex is not enabled for this deployment.
    if ! is_truthy "$ENABLE_VERTEX"; then
        info "Disabling Vertex in operator-config ConfigMap"
        kubectl patch configmap operator-config -n "$AMBIENT_NAMESPACE" --type=merge \
            -p '{"data":{"USE_VERTEX":"0","ANTHROPIC_VERTEX_PROJECT_ID":""}}'
        kubectl rollout restart deployment/agentic-operator -n "$AMBIENT_NAMESPACE"
        kubectl rollout restart deployment/backend-api -n "$AMBIENT_NAMESPACE"
    fi
    
    # Setup Minio
    make setup-minio -C $LOCAL_DIR
    if [ $? -eq 0 ]; then
        info "Minio setup successful"
    else
        fatal "Minio setup failed"
    fi

    # Enable vertex integration
    if is_truthy "$ENABLE_VERTEX" && [ -f "$LOCAL_DIR/scripts/setup-vertex-kind.sh" ]; then
        info "Enabling vertex integration"
        ./$LOCAL_DIR/scripts/setup-vertex-kind.sh
    fi
    
    # Enable Observability
    enable_user_workload_monitoring
    make deploy-observability -C $LOCAL_DIR

    # Deploy Locust operator for load testing
    install_locust_operator
    setup_locust_monitoring

    # Create loadtest service account and RBAC for API access
    setup_loadtest_rbac
}

setup
