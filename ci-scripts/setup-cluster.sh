#!/bin/bash

set -o nounset
set -o errexit
set -o pipefail

source "$(dirname "$0")/lib.sh"
source "$(dirname "$0")/locust.sh"

# Ambient Code platform repository
AMBIENT_CODE_GIT="${AMBIENT_CODE_GIT:-https://github.com/ambient-code/platform.git}"
AMBIENT_CODE_COMMIT="${AMBIENT_CODE_COMMIT:-9d9d9b73b77f72981b4cee272494373920de9ac4}"

# Deployment configuration
LOCAL_DIR="${LOCAL_DIR:-workspaces/ambient-code}"
MANIFESTS_DIR="$LOCAL_DIR/components/manifests"

AMBIENT_NAMESPACE="ambient-code"

function enable_user_workload_monitoring() {
    info "Enabling user workload monitoring"
    config_dir=$(mktemp -d)
    if oc -n openshift-monitoring get cm cluster-monitoring-config; then
        oc -n openshift-monitoring extract configmap/cluster-monitoring-config --to=$config_dir --keys=config.yaml
        sed -i '/^enableUserWorkload:/d' $config_dir/config.yaml
        echo -e "\nenableUserWorkload: true" >> $config_dir/config.yaml
        oc -n openshift-monitoring set data configmap/cluster-monitoring-config --from-file=$config_dir/config.yaml
    else
        cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-monitoring-config
  namespace: openshift-monitoring
data:
  config.yaml: |
    enableUserWorkload: true
EOF
  fi
}


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
    enable_user_workload_monitoring
    make deploy-observability -C $LOCAL_DIR

    # Deploy Locust operator for load testing
    install_locust_operator
    setup_locust_monitoring
}

setup
