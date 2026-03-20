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
#   TEST_SCENARIO       Scenario name (default: session-listing)
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
TEST_SCENARIO="${TEST_SCENARIO:-session-listing}"
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
info "Collecting application logs (since test start) …"

METADATA_FILE="$RUN_ARTIFACTS/test-metadata.json"
LOG_SINCE=""
if [[ -f "$METADATA_FILE" ]]; then
    LOG_SINCE="--since-time=$(python3 -c "import json; print(json.load(open('$METADATA_FILE'))['start_ts'])")"
fi

for deploy in backend-api public-api agentic-operator ambient-api-server frontend; do
    kubectl logs -n "$AMBIENT_NAMESPACE" "deployment/$deploy" --all-containers $LOG_SINCE \
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

        OPL_INSTALLED=false
        if pip install --quiet "opl-rhcloud-perf-team @ git+${OPL_REPO}"; then
            OPL_INSTALLED=true
        else
            warning "Failed to install OPL — skipping monitoring collection"
        fi

        if [[ "$OPL_INSTALLED" == "true" ]]; then
            # Determine Prometheus/Thanos endpoint
            MONITORING_URL="${MONITORING_URL:-}"
            if [[ -z "$MONITORING_URL" ]]; then
                MHOST=$(kubectl -n openshift-monitoring get route -l app.kubernetes.io/name=thanos-query -o json | jq --raw-output '.items[0].spec.host' 2>/dev/null || echo '')
                MONITORING_URL="https://${MHOST}"
            fi
            MONITORING_TOKEN="${MONITORING_TOKEN:-$(oc whoami -t 2>/dev/null || echo '')}"

            # Convert timestamps to ISO 8601 with seconds precision
            MSTART=$(date --utc --date "$START_TS" --iso-8601=seconds)
            MEND=$(date --utc --date "$END_TS" --iso-8601=seconds)

            MONITORING_DATA_FILE="$RUN_ARTIFACTS/monitoring/benchmark-data.json"
            MONITORING_LOG="$RUN_ARTIFACTS/monitoring/collection.log"

            DEFAULT_CONFIG="$PROJECT_ROOT/config/cluster_read_config.yaml"
            SCENARIO_CONFIG="$PROJECT_ROOT/tests/scenarios/$TEST_SCENARIO/cluster_read_config.yaml"

            if [[ -n "$MONITORING_URL" && "$MONITORING_URL" != "https://" ]]; then
                # Always run the default config
                if [[ -f "$DEFAULT_CONFIG" ]]; then
                    info "Collecting default metrics ($DEFAULT_CONFIG) …"
                    status_data.py \
                        --status-data-file "$MONITORING_DATA_FILE" \
                        --additional "$DEFAULT_CONFIG" \
                        --monitoring-start "$MSTART" \
                        --monitoring-end "$MEND" \
                        --monitoring-raw-data-dir "$RUN_ARTIFACTS/monitoring/" \
                        --prometheus-host "$MONITORING_URL" \
                        --prometheus-port 443 \
                        --prometheus-token "$MONITORING_TOKEN" \
                        -d >"$MONITORING_LOG" 2>&1 \
                        || warning "OPL default config collection returned non-zero"
                fi

                # Additionally run the scenario-specific config if present
                if [[ -f "$SCENARIO_CONFIG" ]]; then
                    info "Collecting scenario metrics ($SCENARIO_CONFIG) …"
                    status_data.py \
                        --status-data-file "$MONITORING_DATA_FILE" \
                        --additional "$SCENARIO_CONFIG" \
                        --monitoring-start "$MSTART" \
                        --monitoring-end "$MEND" \
                        --monitoring-raw-data-dir "$RUN_ARTIFACTS/monitoring/" \
                        --prometheus-host "$MONITORING_URL" \
                        --prometheus-port 443 \
                        --prometheus-token "$MONITORING_TOKEN" \
                        -d >>"$MONITORING_LOG" 2>&1 \
                        || warning "OPL scenario config collection returned non-zero"
                else
                    info "No scenario-specific cluster_read_config.yaml found for $TEST_SCENARIO"
                fi
            else
                warning "Skipping Prometheus collection (missing URL)"
            fi
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
