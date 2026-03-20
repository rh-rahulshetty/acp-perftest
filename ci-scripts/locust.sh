source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

LOCUST_HELM_REPO_NAME="locust-k8s-operator"
LOCUST_HELM_REPO_URL="https://abdelrhmanhamouda.github.io/locust-k8s-operator/"
LOCUST_HELM_RELEASE="locust-operator"
LOCUST_HELM_CHART="${LOCUST_HELM_REPO_NAME}/locust-k8s-operator"
LOCUST_NAMESPACE="${LOCUST_NAMESPACE:-${LOCUST_OPERATOR_NAMESPACE:-locust-operator}}"

function install_locust_operator() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local values_file="${LOCUST_VALUES_FILE:-${script_dir}/../config/locust/locust-operator.yaml}"

    info "Creating namespace ${LOCUST_NAMESPACE}"
    kubectl get ns "$LOCUST_NAMESPACE" 2>/dev/null || kubectl create ns "$LOCUST_NAMESPACE"
    # Do NOT add openshift.io/cluster-monitoring=true — that label causes
    # user-workload Prometheus to ignore the namespace.

    info "Adding Helm repo ${LOCUST_HELM_REPO_NAME}"
    helm repo add "$LOCUST_HELM_REPO_NAME" "$LOCUST_HELM_REPO_URL" 2>/dev/null || true
    helm repo update "$LOCUST_HELM_REPO_NAME"

    info "Installing locust-operator into namespace ${LOCUST_NAMESPACE}"
    helm upgrade --install "$LOCUST_HELM_RELEASE" "$LOCUST_HELM_CHART" \
        --namespace "$LOCUST_NAMESPACE" \
        -f "$values_file" \
        --wait --timeout 120s

    info "Locust operator installed successfully"
}

function setup_locust_monitoring() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local sm_template="${script_dir}/../config/locust/locust-servicemonitor.yaml"

    if [[ ! -f "$sm_template" ]]; then
        warning "ServiceMonitor template not found at ${sm_template}, skipping monitoring setup"
        return 0
    fi

    info "Deploying Locust ServiceMonitor"
    export LOCUST_NAMESPACE
    envsubst '${LOCUST_NAMESPACE}' < "$sm_template" \
        | kubectl apply -n "$LOCUST_NAMESPACE" -f -

    info "Locust monitoring configured"
}

function uninstall_locust_operator() {
    local delete_ns="${DELETE_LOCUST_NAMESPACES:-false}"

    info "Uninstalling locust-operator from namespace ${LOCUST_NAMESPACE}"
    helm uninstall "$LOCUST_HELM_RELEASE" --namespace "$LOCUST_NAMESPACE" 2>/dev/null || true
    kubectl delete crd locusttests.locust.io 2>/dev/null || true

    if is_truthy "$delete_ns"; then
        info "Deleting namespace ${LOCUST_NAMESPACE}"
        kubectl delete namespace "$LOCUST_NAMESPACE" --ignore-not-found
    fi

    info "Locust operator uninstalled"
}
