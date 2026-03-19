#!/bin/bash
# Use this script to clean up cluster

set -o nounset
set -o errexit
set -o pipefail

source "$(dirname "$0")/lib.sh"

LOCAL_DIR="${LOCAL_DIR:-workspaces/ambient-code}"
DELETE_SECRETS="${DELETE_SECRETS:-false}"

AMBIENT_NAMESPACE="ambient-code"

function delete_deployment() {
    make clean -C $LOCAL_DIR
    if [ $? -eq 0 ]; then
        info "Cleanup successful"
    else
        error "Cleanup failed"
        exit 1
    fi

    if is_truthy "$DELETE_SECRETS"; then
        oc delete secret minio-credentials postgresql-credentials unleash-credentials -n $AMBIENT_NAMESPACE --ignore-not-found
        info "Secrets deleted"
    fi
}


delete_deployment
