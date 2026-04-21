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
# Each run gets a unique RUN_ID (UUID). A short form (first 8 chars) is used
# for Kubernetes resource names (ConfigMaps, LocustTest CRs). Artifacts are
# stored under artifacts/<run-id>/.
#
# Environment variables (all have sensible defaults):
#   TEST_SCENARIO       Scenario directory name under tests/scenarios/ (default: session-listing)
#   LOCUST_SCRIPT       Specific .py file in locust/ to run (default: auto-detect single file)
#   TEST_USERS          Number of simulated users (default: 10)
#   TEST_SPAWN_RATE     Users spawned per second (default: 2)
#   TEST_RUN_TIME       Duration string, e.g. "5m" (default: 5m)
#   TEST_WORKERS        Locust worker replicas (default: 2)
#   LOCUST_NAMESPACE    Namespace where locust operator runs (default: locust-operator)
#   LOCUST_HOST         Target URL for locust (default: backend-service k8s FQDN)
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
TEST_SCENARIO="${TEST_SCENARIO:-session-listing}"
TEST_USERS="${TEST_USERS:-10}"
TEST_SPAWN_RATE="${TEST_SPAWN_RATE:-2}"
TEST_RUN_TIME="${TEST_RUN_TIME:-5m}"
TEST_WORKERS="${TEST_WORKERS:-3}"
LOCUST_NAMESPACE="${LOCUST_NAMESPACE:-locust-operator}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-$PROJECT_ROOT/artifacts}"
TEST_DO_CLEANUP="${TEST_DO_CLEANUP:-true}"
AMBIENT_NAMESPACE="${AMBIENT_NAMESPACE:-ambient-code}"
LOADTEST_SA="${LOADTEST_SA:-loadtest-sa}"

SCENARIOS_DIR="$PROJECT_ROOT/tests/scenarios"
SCENARIO_DIR="$SCENARIOS_DIR/$TEST_SCENARIO"

if [[ ! -d "$SCENARIO_DIR" ]]; then
    fatal "Scenario directory not found: $SCENARIO_DIR"
fi

LOCUST_DIR="$SCENARIO_DIR/locust"
HAS_LOCUST=false
[[ -d "$LOCUST_DIR" ]] && HAS_LOCUST=true

# ---------------------------------------------------------------------------
# Generate run ID
# ---------------------------------------------------------------------------
RUN_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
RUN_ID_SHORT="${RUN_ID:0:8}"

export RUN_ID RUN_ID_SHORT

info "Run ID: $RUN_ID (short: $RUN_ID_SHORT)"

# ---------------------------------------------------------------------------
# Prepare artifacts — artifacts/<run-id>/
# ---------------------------------------------------------------------------
RUN_ARTIFACTS="$ARTIFACTS_DIR/$RUN_ID"
mkdir -p "$RUN_ARTIFACTS"

# Redirect all script output to both terminal and run.log in the artifacts dir
exec > >(tee -a "$RUN_ARTIFACTS/run.log") 2>&1

# Record test metadata
cat > "$RUN_ARTIFACTS/test-metadata.json" <<EOF
{
  "run_id": "$RUN_ID",
  "run_id_short": "$RUN_ID_SHORT",
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
# Loadtest auth — read SA token from Secret (created by setup-cluster.sh)
# ---------------------------------------------------------------------------
if [[ -z "${AUTH_TOKEN:-}" ]]; then
    AUTH_TOKEN="$(kubectl get secret loadtest-sa-token -n "$AMBIENT_NAMESPACE" \
        -o jsonpath='{.data.token}' 2>/dev/null | base64 -d || echo '')"
    if [[ -z "$AUTH_TOKEN" ]]; then
        fatal "Failed to read token from secret loadtest-sa-token in $AMBIENT_NAMESPACE. Ensure setup-cluster.sh has been run."
    fi
fi
LOADTEST_SA_IDENTITY="system:serviceaccount:${AMBIENT_NAMESPACE}:${LOADTEST_SA}"
export AUTH_TOKEN LOADTEST_SA_IDENTITY

# ---------------------------------------------------------------------------
# Phase 1: Scenario setup
# ---------------------------------------------------------------------------
info "=== Scenario: $TEST_SCENARIO ==="

if [[ -f "$SCENARIO_DIR/setup.sh" ]]; then
    info "Running scenario setup …"
    export TEST_SCENARIO TEST_USERS TEST_SPAWN_RATE TEST_RUN_TIME TEST_WORKERS
    export LOCUST_NAMESPACE ARTIFACTS_DIR RUN_ARTIFACTS AMBIENT_NAMESPACE
    export PROJECT_ROOT SCENARIO_DIR LOCUST_DIR HAS_LOCUST
    source "$SCENARIO_DIR/setup.sh"
fi

# Append environment variables to test metadata (after scenario setup so
# scenario-specific vars like PROJECT_NAME, LOAD_STEPS are captured)
ENV_JSON="$(collect_env_metadata)"
python3 -c "
import json, sys
with open('$RUN_ARTIFACTS/test-metadata.json') as f:
    d = json.load(f)
d['env'] = json.loads(sys.argv[1])
with open('$RUN_ARTIFACTS/test-metadata.json', 'w') as f:
    json.dump(d, f, indent=2)
" "$ENV_JSON"

# ---------------------------------------------------------------------------
# Phase 2: Deploy Locust test (only if locust/ exists)
# ---------------------------------------------------------------------------
if [[ "$HAS_LOCUST" == "true" ]]; then
    # Resolve which locust script to run
    LOCUST_SCRIPT="${LOCUST_SCRIPT:-}"
    if [[ -z "$LOCUST_SCRIPT" ]]; then
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

    # Resource names include short UUID to avoid collisions and track runs
    CONFIGMAP_NAME="locust.${TEST_SCENARIO}.${RUN_ID_SHORT}"
    LOCUST_TEST_NAME="${TEST_SCENARIO}-${RUN_ID_SHORT}"
    LOCUST_CR_TEMPLATE="$LOCUST_DIR/locusttest.yaml"

    export CONFIGMAP_NAME LOCUST_TEST_NAME LOCUST_SCRIPT

    # Build ConfigMap from all .py files in locust/
    info "Creating ConfigMap '$CONFIGMAP_NAME' in namespace '$LOCUST_NAMESPACE' …"
    configmap_args=()
    for f in "$LOCUST_DIR"/*.py; do
        [[ -f "$f" ]] && configmap_args+=(--from-file="$(basename "$f")=$f")
    done
    kubectl create configmap "$CONFIGMAP_NAME" \
        -n "$LOCUST_NAMESPACE" \
        "${configmap_args[@]}" \
        --dry-run=client -o yaml | kubectl apply -f -

    # Resolve target host (scenario setup.sh may have already set this)
    if [[ -z "${LOCUST_HOST:-}" ]]; then
        LOCUST_HOST="http://backend-service.${AMBIENT_NAMESPACE}.svc.cluster.local:8080"
    fi
    export LOCUST_HOST
    info "Locust target: $LOCUST_HOST"
    info "Locust script: $LOCUST_SCRIPT"

    # Apply LocustTest CR — always in LOCUST_NAMESPACE (ServiceMonitor lives here)
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
  worker:
    command: "--locustfile /lotest/src/${LOCUST_SCRIPT}"
    replicas: ${TEST_WORKERS}
EOCR
    fi

    # -------------------------------------------------------------------
    # Phase 3: Wait for locust master pod to appear
    # -------------------------------------------------------------------
    MASTER_LABEL="performance-test-pod-name=${LOCUST_TEST_NAME}-master"

    info "Waiting for locust master pod to start …"
    WAIT_TIMEOUT=$(date -d "300 seconds" "+%s")
    while [[ -z "$(kubectl get pods -n "$LOCUST_NAMESPACE" -l "$MASTER_LABEL" -o name 2>/dev/null)" ]]; do
        if [[ "$(date "+%s")" -gt "$WAIT_TIMEOUT" ]]; then
            fatal "Timeout waiting for locust master pod to appear"
        fi
        sleep 5
    done

    kubectl wait -n "$LOCUST_NAMESPACE" --for=condition=Ready=true \
        $(kubectl get pods -n "$LOCUST_NAMESPACE" -l "$MASTER_LABEL" -o name)

    # -------------------------------------------------------------------
    # Phase 4: Follow master logs until container exits
    # -------------------------------------------------------------------
    LOCUST_ARTIFACTS="$RUN_ARTIFACTS/locust"
    mkdir -p "$LOCUST_ARTIFACTS"

    WORKER_LABEL="performance-test-pod-name=${LOCUST_TEST_NAME}-worker"

    info "Following locust master logs (test will block until completion) …"
    kubectl logs -n "$LOCUST_NAMESPACE" -f -l "$MASTER_LABEL" 2>&1 \
        | tee "$LOCUST_ARTIFACTS/master.log"

    # Collect worker logs after test completes
    info "Collecting locust worker logs …"
    for worker_pod in $(kubectl get pods -n "$LOCUST_NAMESPACE" -l "$WORKER_LABEL" \
        -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
        kubectl logs -n "$LOCUST_NAMESPACE" "$worker_pod" \
            > "$LOCUST_ARTIFACTS/${worker_pod}.log" 2>/dev/null || true
    done

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
with open('$RUN_ARTIFACTS/test-metadata.json') as f:
    d = json.load(f)
d['end_ts'] = '$END_TS'
with open('$RUN_ARTIFACTS/test-metadata.json', 'w') as f:
    json.dump(d, f, indent=2)
"

# ---------------------------------------------------------------------------
# Phase 6: Scenario teardown
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

# ---------------------------------------------------------------------------
# Phase 7: Collect results
# ---------------------------------------------------------------------------
info "Collecting results …"
export RUN_ID RUN_ARTIFACTS TEST_SCENARIO
"$SCRIPT_DIR/collect-results.sh"

info "=== Load test complete ==="
info "  Run ID:    $RUN_ID"
info "  Artifacts: $RUN_ARTIFACTS"
