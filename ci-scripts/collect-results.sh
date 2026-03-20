#!/bin/bash
#
# collect-results.sh — Collect benchmark results, cluster metrics, and logs.
#
# Typically called by load-test.sh at the end of a run, but can also be
# invoked standalone if you pass the right env vars.
#
# Gathers:
#   1. Application pod logs (backend, public-api, operator, etc.)
#   2. Cluster info (nodes, versions, resource usage)
#   3. Prometheus metrics via OPL cluster_read
#   4. Consolidated benchmark JSON
#
# Environment variables:
#   RUN_ID              Run UUID (required when called standalone)
#   RUN_ARTIFACTS       Path to this run's artifacts dir (required)
#   TEST_SCENARIO       Scenario name (default: session-crud)
#   MONITORING_COLLECTION_ENABLED  Collect Prometheus metrics (default: true)
#   OPL_REPO            OPL git repo URL

set -o nounset
set -o errexit
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$SCRIPT_DIR/lib.sh"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
TEST_SCENARIO="${TEST_SCENARIO:-session-crud}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-$PROJECT_ROOT/artifacts}"

# RUN_ARTIFACTS must be set (by load-test.sh or by the user)
if [[ -z "${RUN_ARTIFACTS:-}" ]]; then
    if [[ -n "${RUN_ID:-}" ]]; then
        RUN_ARTIFACTS="$ARTIFACTS_DIR/$RUN_ID"
    else
        fatal "RUN_ARTIFACTS (or RUN_ID) must be set — pass the run's artifact directory"
    fi
fi

MONITORING_COLLECTION_ENABLED="${MONITORING_COLLECTION_ENABLED:-true}"
OPL_REPO="${OPL_REPO:-https://github.com/redhat-performance/opl.git}"
AMBIENT_NAMESPACE="${AMBIENT_NAMESPACE:-ambient-code}"

mkdir -p "$RUN_ARTIFACTS/monitoring" "$RUN_ARTIFACTS/logs" "$RUN_ARTIFACTS/cluster-info"

info "Collecting results into $RUN_ARTIFACTS …"

# ---------------------------------------------------------------------------
# 1. Collect application logs
# ---------------------------------------------------------------------------
info "Collecting application logs …"

for deploy in backend-api public-api agentic-operator ambient-api-server frontend; do
    kubectl logs -n "$AMBIENT_NAMESPACE" "deployment/$deploy" --all-containers --tail=10000 \
        > "$RUN_ARTIFACTS/logs/${deploy}.log" 2>/dev/null || true
done

# ---------------------------------------------------------------------------
# 2. Collect cluster info
# ---------------------------------------------------------------------------
info "Collecting cluster info …"

kubectl get nodes -o json > "$RUN_ARTIFACTS/cluster-info/nodes.json" 2>/dev/null || true
kubectl get pods -n "$AMBIENT_NAMESPACE" -o json > "$RUN_ARTIFACTS/cluster-info/pods.json" 2>/dev/null || true
kubectl top nodes > "$RUN_ARTIFACTS/cluster-info/node-usage.txt" 2>/dev/null || true
kubectl top pods -n "$AMBIENT_NAMESPACE" > "$RUN_ARTIFACTS/cluster-info/pod-usage.txt" 2>/dev/null || true
kubectl version -o json > "$RUN_ARTIFACTS/cluster-info/k8s-version.json" 2>/dev/null || true

kubectl get agenticsessions -n "$AMBIENT_NAMESPACE" -o json \
    > "$RUN_ARTIFACTS/cluster-info/agenticsessions.json" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 3. Collect Prometheus monitoring data via OPL
# ---------------------------------------------------------------------------
if is_truthy "$MONITORING_COLLECTION_ENABLED"; then
    info "Collecting monitoring data via OPL …"

    METADATA_FILE="$RUN_ARTIFACTS/test-metadata.json"
    if [[ ! -f "$METADATA_FILE" ]]; then
        warning "test-metadata.json not found — skipping monitoring collection"
    else
        START_TS=$(python3 -c "import json; print(json.load(open('$METADATA_FILE'))['start_ts'])")
        END_TS=$(python3 -c "import json; print(json.load(open('$METADATA_FILE')).get('end_ts', ''))")

        if [[ -z "$END_TS" ]]; then
            END_TS="$(date -Ins --utc)"
        fi

        # Set up OPL in a virtual environment
        OPL_VENV="$PROJECT_ROOT/.venv-opl"
        if [[ ! -d "$OPL_VENV" ]]; then
            info "Setting up OPL virtual environment …"
            python3 -m venv "$OPL_VENV"
        fi
        # shellcheck disable=SC1091
        source "$OPL_VENV/bin/activate"
        pip install --quiet -e "git+${OPL_REPO}#egg=opl" 2>/dev/null || {
            warning "Failed to install OPL — skipping monitoring collection"
            deactivate
        }

        # Determine Prometheus/Thanos endpoint
        MONITORING_URL="${MONITORING_URL:-}"
        if [[ -z "$MONITORING_URL" ]]; then
            MONITORING_URL="https://$(oc get route thanos-querier -n openshift-monitoring -o jsonpath='{.spec.host}' 2>/dev/null || echo '')"
        fi
        MONITORING_TOKEN="${MONITORING_TOKEN:-$(oc whoami -t 2>/dev/null || echo '')}"

        # Choose cluster_read_config: scenario-specific if present, else default
        CLUSTER_READ_CONFIG="$PROJECT_ROOT/config/cluster_read_config.yaml"
        SCENARIO_READ_CONFIG="$PROJECT_ROOT/tests/scenarios/$TEST_SCENARIO/cluster_read_config.yaml"
        if [[ -f "$SCENARIO_READ_CONFIG" ]]; then
            CLUSTER_READ_CONFIG="$SCENARIO_READ_CONFIG"
        fi

        if [[ -f "$CLUSTER_READ_CONFIG" && -n "$MONITORING_URL" && "$MONITORING_URL" != "https://" ]]; then
            info "Querying Prometheus ($MONITORING_URL) for metrics …"
            python3 -m opl.status_data \
                --status-data-file "$RUN_ARTIFACTS/monitoring/benchmark-data.json" \
                --config "$CLUSTER_READ_CONFIG" \
                --monitoring-start "$START_TS" \
                --monitoring-end "$END_TS" \
                --monitoring-raw-data-dir "$RUN_ARTIFACTS/monitoring/" \
                --prometheus-host "$MONITORING_URL" \
                --prometheus-port 443 \
                --prometheus-token "$MONITORING_TOKEN" \
                || warning "OPL monitoring collection returned non-zero"
        else
            warning "Skipping Prometheus collection (missing config or URL)"
        fi

        deactivate 2>/dev/null || true
    fi
else
    info "Monitoring collection disabled — skipping"
fi

# ---------------------------------------------------------------------------
# 4. Build consolidated benchmark JSON
# ---------------------------------------------------------------------------
info "Building benchmark JSON …"

python3 "$PROJECT_ROOT/tools/build-benchmark-json.py" \
    --artifacts-dir "$RUN_ARTIFACTS" \
    --output "$RUN_ARTIFACTS/benchmark.json" \
    || warning "build-benchmark-json.py failed — partial results may be available"

info "=== Results collected in: $RUN_ARTIFACTS ==="
ls -lh "$RUN_ARTIFACTS/"
