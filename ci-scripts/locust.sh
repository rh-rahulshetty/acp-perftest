source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

LOCUST_HELM_REPO_NAME="locust-k8s-operator"
LOCUST_HELM_REPO_URL="https://abdelrhmanhamouda.github.io/locust-k8s-operator/"
LOCUST_HELM_RELEASE="locust-operator"
LOCUST_HELM_CHART="${LOCUST_HELM_REPO_NAME}/locust-k8s-operator"

LOCUST_OPERATOR_NAMESPACE="${LOCUST_OPERATOR_NAMESPACE:-locust-system}"
LOCUST_NAMESPACE="${LOCUST_NAMESPACE:-locust-tests}"

function install_locust_operator() {
    # Installs (or upgrades) the locust-k8s-operator Helm chart.
    #
    # Optional env overrides:
    #   LOCUST_OPERATOR_NAMESPACE  – namespace for the operator  (default: locust-system)
    #   LOCUST_NAMESPACE           – namespace where tests run   (default: locust-tests)
    #   LOCUST_VALUES_FILE         – path to Helm values file
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local values_file="${LOCUST_VALUES_FILE:-${script_dir}/../config/locust/locust-operator.yaml}"

    info "Adding Helm repo ${LOCUST_HELM_REPO_NAME}"
    helm repo add "$LOCUST_HELM_REPO_NAME" "$LOCUST_HELM_REPO_URL" 2>/dev/null || true
    helm repo update "$LOCUST_HELM_REPO_NAME"

    info "Installing locust-operator into namespace ${LOCUST_OPERATOR_NAMESPACE}"
    helm upgrade --install "$LOCUST_HELM_RELEASE" "$LOCUST_HELM_CHART" \
        --namespace "$LOCUST_OPERATOR_NAMESPACE" \
        --create-namespace \
        -f "$values_file" \
        --wait --timeout 120s

    info "Creating test namespace ${LOCUST_NAMESPACE}"
    kubectl create namespace "$LOCUST_NAMESPACE" 2>/dev/null || true

    info "Locust operator installed successfully"
}

function setup_locust_monitoring() {
    # Deploys ServiceMonitor resources so Prometheus discovers both the
    # operator metrics and the per-test Locust exporter metrics.
    #
    # Requires: LOCUST_OPERATOR_NAMESPACE, LOCUST_NAMESPACE to be set.
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local sm_template="${script_dir}/../config/locust/locust-servicemonitor.yaml"

    if [[ ! -f "$sm_template" ]]; then
        warning "ServiceMonitor template not found at ${sm_template}, skipping monitoring setup"
        return 0
    fi

    info "Deploying Locust ServiceMonitors"
    export LOCUST_OPERATOR_NAMESPACE LOCUST_NAMESPACE
    envsubst '${LOCUST_OPERATOR_NAMESPACE} ${LOCUST_NAMESPACE}' < "$sm_template" \
        | kubectl apply -n "$LOCUST_NAMESPACE" -f -

    info "Locust monitoring configured"
}

function uninstall_locust_operator() {
    # Uninstalls the locust-k8s-operator Helm release and optionally deletes
    # the operator and test namespaces.
    #
    # Set DELETE_LOCUST_NAMESPACES=true to also remove the namespaces.
    local delete_ns="${DELETE_LOCUST_NAMESPACES:-false}"

    info "Uninstalling locust-operator from namespace ${LOCUST_OPERATOR_NAMESPACE}"
    helm uninstall "$LOCUST_HELM_RELEASE" --namespace "$LOCUST_OPERATOR_NAMESPACE" 2>/dev/null || true

    # Clean up CRDs left behind by Helm
    kubectl delete crd locusttests.locust.io 2>/dev/null || true

    if is_truthy "$delete_ns"; then
        info "Deleting namespace ${LOCUST_NAMESPACE}"
        kubectl delete namespace "$LOCUST_NAMESPACE" --ignore-not-found
        info "Deleting namespace ${LOCUST_OPERATOR_NAMESPACE}"
        kubectl delete namespace "$LOCUST_OPERATOR_NAMESPACE" --ignore-not-found
    fi

    info "Locust operator uninstalled"
}
