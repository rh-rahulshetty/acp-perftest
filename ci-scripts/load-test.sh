#!/bin/bash
#
# load-test.sh — Orchestrate a load test scenario against the Ambient Code Platform.
#
# Scenario layout:
#   tests/scenarios/<name>/
#     ├── setup.sh                   # (optional) pre-test setup, sourced
#     ├── teardown.sh                # (optional) post-test cleanup, sourced
#     ├── cluster_read_config.yaml   # (optional) scenario-specific OPL config
#     └── locust/                    # (optional) locust load test files
#         ├── <name>.py              # locust test scripts (one or more)
#         └── locusttest.yaml        # (optional) custom LocustTest CR template
#
# If the scenario has no locust/ directory, only setup.sh and teardown.sh run
# (useful for non-locust scenarios — e.g. shell-based benchmarks, operator stress).
#
# Environment variables (all have sensible defaults):
#   TEST_SCENARIO       Scenario directory name under tests/scenarios/ (default: session-crud)
#   LOCUST_SCRIPT       Specific .py file in locust/ to run (default: auto-detect single file)
#   TEST_USERS          Number of simulated users (default: 10)
#   TEST_SPAWN_RATE     Users spawned per second (default: 2)
#   TEST_RUN_TIME       Duration string, e.g. "5m" (default: 5m)
#   TEST_WORKERS        Locust worker replicas (default: 2)
#   LOCUST_NAMESPACE    Namespace where locust operator runs (default: locust-operator)
#   LOCUST_HOST         Target URL for locust (auto-detected from route/svc if unset)
#   ARTIFACTS_DIR       Where to store results (default: artifacts)
#   TEST_DO_CLEANUP     Delete scenario resources after test (default: true)

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
TEST_USERS="${TEST_USERS:-10}"
TEST_SPAWN_RATE="${TEST_SPAWN_RATE:-2}"
TEST_RUN_TIME="${TEST_RUN_TIME:-5m}"
TEST_WORKERS="${TEST_WORKERS:-2}"
LOCUST_NAMESPACE="${LOCUST_NAMESPACE:-locust-operator}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-$PROJECT_ROOT/artifacts}"
TEST_DO_CLEANUP="${TEST_DO_CLEANUP:-true}"
AMBIENT_NAMESPACE="${AMBIENT_NAMESPACE:-ambient-code}"

SCENARIOS_DIR="$PROJECT_ROOT/tests/scenarios"
SCENARIO_DIR="$SCENARIOS_DIR/$TEST_SCENARIO"

if [[ ! -d "$SCENARIO_DIR" ]]; then
    fatal "Scenario directory not found: $SCENARIO_DIR"
fi

LOCUST_DIR="$SCENARIO_DIR/locust"
HAS_LOCUST=false
[[ -d "$LOCUST_DIR" ]] && HAS_LOCUST=true

# ---------------------------------------------------------------------------
# Prepare artifacts
# ---------------------------------------------------------------------------
SCENARIO_ARTIFACTS="$ARTIFACTS_DIR/$TEST_SCENARIO"
mkdir -p "$SCENARIO_ARTIFACTS"

# Record test metadata
cat > "$SCENARIO_ARTIFACTS/test-metadata.json" <<EOF
{
  "scenario": "$TEST_SCENARIO",
  "has_locust": $HAS_LOCUST,
  "users": $TEST_USERS,
  "spawn_rate": $TEST_SPAWN_RATE,
  "run_time": "$TEST_RUN_TIME",
  "workers": $TEST_WORKERS,
  "start_ts": "$(date -Ins --utc)",
  "cluster": "$(kubectl config current-context 2>/dev/null || echo unknown)"
}
EOF

# ---------------------------------------------------------------------------
# Phase 1: Scenario setup
# ---------------------------------------------------------------------------
info "=== Scenario: $TEST_SCENARIO ==="

if [[ -f "$SCENARIO_DIR/setup.sh" ]]; then
    info "Running scenario setup …"
    export TEST_SCENARIO TEST_USERS TEST_SPAWN_RATE TEST_RUN_TIME TEST_WORKERS
    export LOCUST_NAMESPACE ARTIFACTS_DIR SCENARIO_ARTIFACTS AMBIENT_NAMESPACE
    export PROJECT_ROOT SCENARIO_DIR LOCUST_DIR HAS_LOCUST
    source "$SCENARIO_DIR/setup.sh"
fi

# ---------------------------------------------------------------------------
# Phase 2: Deploy Locust test (only if locust/ exists)
# ---------------------------------------------------------------------------
if [[ "$HAS_LOCUST" == "true" ]]; then
    # Resolve which locust script to run
    LOCUST_SCRIPT="${LOCUST_SCRIPT:-}"
    if [[ -z "$LOCUST_SCRIPT" ]]; then
        # Auto-detect: if only one .py file, use it; otherwise require LOCUST_SCRIPT
        py_files=("$LOCUST_DIR"/*.py)
        if [[ ${#py_files[@]} -eq 1 && -f "${py_files[0]}" ]]; then
            LOCUST_SCRIPT="$(basename "${py_files[0]}")"
        elif [[ ${#py_files[@]} -gt 1 ]]; then
            fatal "Multiple .py files in $LOCUST_DIR — set LOCUST_SCRIPT to choose one: $(ls "$LOCUST_DIR"/*.py | xargs -n1 basename | tr '\n' ' ')"
        else
            fatal "No .py files found in $LOCUST_DIR"
        fi
    fi

    LOCUST_SCRIPT_PATH="$LOCUST_DIR/$LOCUST_SCRIPT"
    if [[ ! -f "$LOCUST_SCRIPT_PATH" ]]; then
        fatal "Locust script not found: $LOCUST_SCRIPT_PATH"
    fi

    CONFIGMAP_NAME="locust.${TEST_SCENARIO}"
    LOCUST_TEST_NAME="${TEST_SCENARIO}"
    LOCUST_CR_TEMPLATE="$LOCUST_DIR/locusttest.yaml"

    export CONFIGMAP_NAME LOCUST_TEST_NAME LOCUST_SCRIPT

    # Build ConfigMap from all .py files in locust/ (the operator mounts them all)
    info "Creating ConfigMap '$CONFIGMAP_NAME' from $LOCUST_DIR/ …"
    configmap_args=()
    for f in "$LOCUST_DIR"/*.py; do
        [[ -f "$f" ]] && configmap_args+=(--from-file="$(basename "$f")=$f")
    done
    kubectl create configmap "$CONFIGMAP_NAME" \
        -n "$LOCUST_NAMESPACE" \
        "${configmap_args[@]}" \
        --dry-run=client -o yaml | kubectl apply -f -

    # Resolve target host
    if [[ -z "${LOCUST_HOST:-}" ]]; then
        ROUTE_HOST="$(kubectl get route frontend -n "$AMBIENT_NAMESPACE" -o jsonpath='{.spec.host}' 2>/dev/null || echo '')"
        if [[ -n "$ROUTE_HOST" ]]; then
            LOCUST_HOST="https://${ROUTE_HOST}"
        else
            LOCUST_HOST="http://$(kubectl get svc public-api-service -n "$AMBIENT_NAMESPACE" -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo 'localhost'):8081"
        fi
    fi
    export LOCUST_HOST
    info "Locust target: $LOCUST_HOST"
    info "Locust script: $LOCUST_SCRIPT"

    # Apply LocustTest CR
    if [[ -f "$LOCUST_CR_TEMPLATE" ]]; then
        info "Applying custom LocustTest CR from $LOCUST_CR_TEMPLATE …"
        export TEST_USERS TEST_SPAWN_RATE TEST_RUN_TIME TEST_WORKERS LOCUST_NAMESPACE
        envsubst < "$LOCUST_CR_TEMPLATE" | kubectl apply -f -
    else
        info "Generating default LocustTest CR …"
        cat <<EOCR | kubectl apply -f -
apiVersion: locust.io/v2
kind: LocustTest
metadata:
  name: ${LOCUST_TEST_NAME}
  namespace: ${LOCUST_NAMESPACE}
spec:
  image: locustio/locust:2.43.3
  testFiles:
    configMapRef: ${CONFIGMAP_NAME}
  master:
    command: >-
      --locustfile /lotest/src/${LOCUST_SCRIPT}
      --host ${LOCUST_HOST}
      --users ${TEST_USERS}
      --spawn-rate ${TEST_SPAWN_RATE}
      --run-time ${TEST_RUN_TIME}
      --csv /tmp/locust-results
      --headless
  worker:
    command: "--locustfile /lotest/src/${LOCUST_SCRIPT}"
    replicas: ${TEST_WORKERS}
EOCR
    fi

    # -------------------------------------------------------------------
    # Phase 3: Wait for test completion
    # -------------------------------------------------------------------
    info "Waiting for load test to complete (timeout: ${TEST_RUN_TIME} + 120s buffer) …"
    RUN_SECONDS=$("$PROJECT_ROOT/tools/parse-duration.sh" "$TEST_RUN_TIME")
    TIMEOUT_SECONDS=$((RUN_SECONDS + 120))

    if ! "$PROJECT_ROOT/tools/wait-for-locust.sh" "$LOCUST_TEST_NAME" "$LOCUST_NAMESPACE" "$TIMEOUT_SECONDS"; then
        warning "Load test did not complete within timeout — collecting partial results"
    fi

    # -------------------------------------------------------------------
    # Phase 4: Extract results from locust master
    # -------------------------------------------------------------------
    info "Extracting results from locust master pod …"
    MASTER_POD=$(kubectl get pods -n "$LOCUST_NAMESPACE" \
        -l "locust-test=$LOCUST_TEST_NAME,role=master" \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

    if [[ -n "$MASTER_POD" ]]; then
        for suffix in stats.csv stats_history.csv failures.csv exceptions.csv; do
            kubectl cp "$LOCUST_NAMESPACE/$MASTER_POD:/tmp/locust-results_${suffix}" \
                "$SCENARIO_ARTIFACTS/locust_${suffix}" 2>/dev/null || true
        done

        kubectl logs "$MASTER_POD" -n "$LOCUST_NAMESPACE" \
            > "$SCENARIO_ARTIFACTS/locust-master.log" 2>/dev/null || true
    fi
else
    info "No locust/ directory — skipping locust deployment"
fi

# ---------------------------------------------------------------------------
# Record end timestamp
# ---------------------------------------------------------------------------
END_TS="$(date -Ins --utc)"
info "Test ended at $END_TS"

python3 -c "
import json
with open('$SCENARIO_ARTIFACTS/test-metadata.json') as f:
    d = json.load(f)
d['end_ts'] = '$END_TS'
with open('$SCENARIO_ARTIFACTS/test-metadata.json', 'w') as f:
    json.dump(d, f, indent=2)
"

# ---------------------------------------------------------------------------
# Phase 5: Scenario teardown
# ---------------------------------------------------------------------------
if [[ -f "$SCENARIO_DIR/teardown.sh" ]]; then
    info "Running scenario teardown …"
    source "$SCENARIO_DIR/teardown.sh"
fi

if is_truthy "$TEST_DO_CLEANUP" && [[ "$HAS_LOCUST" == "true" ]]; then
    info "Cleaning up locust resources …"
    kubectl delete locusttest "$LOCUST_TEST_NAME" -n "$LOCUST_NAMESPACE" --ignore-not-found
    kubectl delete configmap "$CONFIGMAP_NAME" -n "$LOCUST_NAMESPACE" --ignore-not-found
fi

info "=== Load test complete. Artifacts in: $SCENARIO_ARTIFACTS ==="
