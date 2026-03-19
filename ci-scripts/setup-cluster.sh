#!/bin/bash

set -o nounset
set -o errexit
set -o pipefail

source "$(dirname "$0")/lib.sh"

# Ambient Code platform repository
AMBIENT_CODE_GIT="${AMBIENT_CODE_GIT:-https://github.com/ambient-code/platform.git}"
AMBIENT_CODE_COMMIT="${AMBIENT_CODE_COMMIT:-163d0f3e7d6ee3fb888143c444bb4ee129a8a53d}"

# Deployment configuration
LOCAL_DIR="${LOCAL_DIR:-workspaces/ambient-code}"
MANIFESTS_DIR="$LOCAL_DIR/components/manifests"

AMBIENT_NAMESPACE="ambient-code"


function setup() {
    mkdir -p $LOCAL_DIR
    
    # Clone the Ambient Code platform repository
    clone_repository $AMBIENT_CODE_GIT $AMBIENT_CODE_COMMIT $LOCAL_DIR

    oc create namespace $AMBIENT_NAMESPACE || true

    # Generate passwords and apply secrets to the cluster
    export AMBIENT_NAMESPACE
    cp config/ambient/.env "$MANIFESTS_DIR/.env"
    apply_secrets

    # Deploy ACP
    make deploy -C $LOCAL_DIR
    if [ $? -eq 0 ]; then
        info "Deployed successfully"
    else
        fatal "Deployment failed"
    fi
    
    # Setup Minio
    make setup-minio -C $LOCAL_DIR
    if [ $? -eq 0 ]; then
        info "Minio setup successful"
    else
        fatal "Minio setup failed"
    fi
    
    # Enable Observability
    make deploy-observability -C $LOCAL_DIR
}

setup
