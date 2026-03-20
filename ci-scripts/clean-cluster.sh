#!/bin/bash
# Use this script to clean up cluster

set -o nounset
set -o errexit
set -o pipefail

source "$(dirname "$0")/lib.sh"
source "$(dirname "$0")/locust.sh"

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

    # Remove CRDs
    oc delete crd agenticsessions.vteam.ambient-code --ignore-not-found
    oc delete crd projectsettings.vteam.ambient-code --ignore-not-found
    oc delete crd rfeworkflows.vteam.ambient-code --ignore-not-found

    # Remove cluster-level RBAC
    oc delete clusterrole ambient-code-operator --ignore-not-found
    oc delete clusterrolebinding ambient-code-operator --ignore-not-found

    # Remove loadtest RBAC
    kubectl delete clusterrolebinding loadtest-sa-admin loadtest-sa-project-creator --ignore-not-found
    kubectl delete clusterrole loadtest-project-creator --ignore-not-found
    kubectl delete secret loadtest-sa-token -n $AMBIENT_NAMESPACE --ignore-not-found
    kubectl delete sa loadtest-sa -n $AMBIENT_NAMESPACE --ignore-not-found

    # Remove Locust operator
    uninstall_locust_operator
}


delete_deployment
