function _log() {
    echo "$( date -Ins --utc ) $1 $2" >&1
}

function debug() {
    _log DEBUG "$1"
}

function info() {
    _log INFO "$1"
}

function warning() {
    _log WARNING "$1"
}

function error() {
    _log ERROR "$1"
}

function fatal() {
    _log FATAL "$1"
    exit 1
}


function generate_password() {
    local length="${1:-24}"
    openssl rand -base64 "$length" | tr -d '/+=' | head -c "$length"
}

function render_template() {
    # Generic template renderer. Substitutes only the listed variables in a
    # template file and writes the result to stdout.
    #
    # Usage: render_template <template_file> <VAR1> [VAR2 ...]
    #
    # Each VAR must be exported in the environment before calling. Only the
    # explicitly listed variables are replaced — everything else in the file
    # (including other $-references) is left untouched.
    local template="$1"; shift

    if [[ ! -f "$template" ]]; then
        fatal "Template not found: $template"
    fi

    local envsubst_vars=""
    for var in "$@"; do
        if [[ -z "${!var+set}" ]]; then
            fatal "render_template: variable $var is not set"
        fi
        envsubst_vars+='${'"$var"'} '
    done

    envsubst "$envsubst_vars" < "$template"
}

function apply_secrets() {
    # Generates random passwords for the secrets template and applies the
    # rendered manifest to the cluster.
    #
    # Required: AMBIENT_NAMESPACE must be set before calling.
    # Generated (unless already exported): MINIO_ROOT_PASSWORD,
    #   POSTGRES_PASSWORD, UNLEASH_ADMIN_PASSWORD
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local template="${script_dir}/../config/ambient/secrets.yaml"

    if [[ -z "${AMBIENT_NAMESPACE:-}" ]]; then
        fatal "AMBIENT_NAMESPACE must be set before calling apply_secrets"
    fi

    export AMBIENT_NAMESPACE

    local secrets=(minio-credentials postgresql-credentials unleash-credentials)
    local all_exist=true
    for secret in "${secrets[@]}"; do
        if ! oc get secret "$secret" -n "$AMBIENT_NAMESPACE" &>/dev/null; then
            info "Secret $secret not found in namespace $AMBIENT_NAMESPACE"
            all_exist=false
        fi
    done

    if [[ "$all_exist" == "true" ]]; then
        info "All secrets already exist in namespace $AMBIENT_NAMESPACE, skipping creation"
        return 0
    fi

    export MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-$(generate_password 24)}"
    export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(generate_password 24)}"
    export UNLEASH_ADMIN_PASSWORD="${UNLEASH_ADMIN_PASSWORD:-$(generate_password 24)}"

    info "Generated secrets for namespace $AMBIENT_NAMESPACE"
    # debug "  MINIO_ROOT_PASSWORD    = ${MINIO_ROOT_PASSWORD}"
    # debug "  POSTGRES_PASSWORD      = ${POSTGRES_PASSWORD}"
    # debug "  UNLEASH_ADMIN_PASSWORD = ${UNLEASH_ADMIN_PASSWORD}"

    render_template "$template" \
        AMBIENT_NAMESPACE \
        MINIO_ROOT_PASSWORD \
        POSTGRES_PASSWORD \
        UNLEASH_ADMIN_PASSWORD \
    | oc apply -f -

    info "Secrets applied successfully"
}

function clone_repository() {
    local repo_url="$1"
    local commit="$2"
    local target_dir="$3"

    if [ -d "$target_dir/.git" ]; then
        info "Repository exists at $target_dir, fetching latest..."
        git -C "$target_dir" fetch --all --tags
    else
        info "Cloning repository $repo_url into $target_dir..."
        git clone "$repo_url" "$target_dir"
    fi

    info "Checking out commit $commit in $target_dir..."
    git -C "$target_dir" checkout "$commit"
}

function wait_for_agenticsessions() {
    # Wait for all agenticsession pods to be created (Running/Succeeded).
    #
    # Usage: wait_for_agenticsessions <namespace> [timeout_seconds]
    local namespace="$1"
    local timeout="${2:-300}"

    local total
    total=$(oc get agenticsessions -n "$namespace" --no-headers 2>/dev/null | wc -l)
    if [[ "$total" -eq 0 ]]; then
        info "No agenticsessions found in namespace $namespace"
        return 0
    fi

    info "Waiting up to ${timeout}s for $total agenticsession pod(s) in namespace $namespace …"

    local deadline=$(( $(date +%s) + timeout ))
    local log_interval=$(( timeout / 4 ))
    [[ "$log_interval" -lt 5 ]] && log_interval=5
    local next_log=0
    while true; do
        # Count pods owned by agenticsessions that are Running or Succeeded
        local ready
        ready=$(oc get pods -n "$namespace" \
            -l app=ambient-code-runner \
            --no-headers 2>/dev/null \
            | grep -cE '\s(Running|Succeeded)\s' || true)

        if [[ "$ready" -ge "$total" ]]; then
            info "All agenticsession pods are ready in namespace $namespace ($ready/$total)"
            return 0
        fi

        local now
        now=$(date +%s)

        if [[ "$now" -ge "$deadline" ]]; then
            warning "Timed out waiting for agenticsession pods ($ready/$total ready after ${timeout}s) — proceeding anyway"
            return 0
        fi

        if [[ "$now" -ge "$next_log" ]]; then
            info "Waiting for agenticsession pods … ($ready/$total ready)"
            next_log=$(( now + log_interval ))
        fi
        sleep 5
    done
}

function save_agenticsessions() {
    # Save each agenticsession resource as individual YAML files.
    #
    # Usage: save_agenticsessions <namespace> <output_dir>
    local namespace="$1"
    local output_dir="$2"

    mkdir -p "$output_dir"
    info "Saving agenticsession resources from namespace $namespace to $output_dir …"

    # Save summary listing before fetching individual resources
    oc get agenticsessions -n "$namespace" -o wide \
        > "$output_dir/agenticsessions.log" 2>/dev/null || true

    local count=0
    while IFS= read -r name; do
        [[ -z "$name" ]] && continue
        oc get agenticsession "$name" -n "$namespace" -o yaml \
            > "$output_dir/${name}.yaml" 2>/dev/null || true
        count=$((count + 1))
    done < <(oc get agenticsessions -n "$namespace" --no-headers -o custom-columns=":metadata.name" 2>/dev/null || true)

    info "Saved $count agenticsession(s) from namespace $namespace"
}

function extract_locust_csv() {
    # Extract CSV result files from a completed locust master pod.
    #
    # kubectl cp fails on completed pods (needs a running container to exec tar).
    # This function creates a debug copy of the pod with shared process namespaces,
    # reads the CSV files from the original container's filesystem via /proc,
    # and cleans up the debug pod afterwards.
    #
    # Usage: extract_locust_csv <pod_name> <namespace> <output_dir> [csv_prefix]
    local pod_name="$1"
    local namespace="$2"
    local output_dir="$3"
    local csv_prefix="${4:-/tmp/locust-results}"

    local debug_pod="${pod_name}-debug"
    local csv_suffixes=(stats.csv stats_history.csv failures.csv exceptions.csv)

    mkdir -p "$output_dir"

    # Try kubectl cp first — works if the container is still running
    local cp_ok=true
    for suffix in "${csv_suffixes[@]}"; do
        kubectl cp "$namespace/$pod_name:${csv_prefix}_${suffix}" \
            "$output_dir/locust_${suffix}" 2>/dev/null || { cp_ok=false; break; }
    done

    if [[ "$cp_ok" == "true" ]]; then
        info "CSV files extracted via kubectl cp"
        return 0
    fi

    # Container exited — use kubectl debug to create a pod copy with shared
    # process namespaces and a busybox sidecar that can read the files
    info "Container exited, extracting CSV files via debug pod …"

    # Find the main container name
    local container_name
    container_name=$(kubectl get pod "$pod_name" -n "$namespace" \
        -o jsonpath='{.spec.containers[0].name}' 2>/dev/null || echo "")
    if [[ -z "$container_name" ]]; then
        warning "Could not determine container name for pod $pod_name"
        return 1
    fi

    # Create a debug copy — the original container restarts in the copy,
    # and the debug container shares its PID namespace
    kubectl debug "$pod_name" -n "$namespace" \
        --copy-to="$debug_pod" \
        --container=csv-extract \
        --image=busybox:latest \
        --share-processes=true \
        -- sleep 300 >/dev/null 2>&1 &
    local debug_pid=$!

    # Wait for the debug pod and csv-extract container to be running
    local deadline=$(( $(date +%s) + 60 ))
    while true; do
        local phase
        phase=$(kubectl get pod "$debug_pod" -n "$namespace" \
            -o jsonpath='{.status.containerStatuses[?(@.name=="csv-extract")].state.running.startedAt}' 2>/dev/null || echo "")
        if [[ -n "$phase" ]]; then
            break
        fi
        if [[ "$(date +%s)" -ge "$deadline" ]]; then
            warning "Timeout waiting for debug pod $debug_pod"
            kill "$debug_pid" 2>/dev/null || true
            kubectl delete pod "$debug_pod" -n "$namespace" --ignore-not-found >/dev/null 2>&1
            return 1
        fi
        sleep 2
    done

    # Find the PID of the original container's process inside the debug pod.
    # The original container's root filesystem is at /proc/<pid>/root/
    local target_pid
    target_pid=$(kubectl exec "$debug_pod" -n "$namespace" -c csv-extract -- \
        sh -c "ls /proc/*/root${csv_prefix}_stats.csv 2>/dev/null | head -1 | cut -d/ -f3" 2>/dev/null || echo "")

    if [[ -z "$target_pid" ]]; then
        warning "Could not find CSV files in debug pod — locust may not have written them"
        kill "$debug_pid" 2>/dev/null || true
        kubectl delete pod "$debug_pod" -n "$namespace" --ignore-not-found >/dev/null 2>&1
        return 1
    fi

    # Copy each CSV file out
    local extracted=0
    for suffix in "${csv_suffixes[@]}"; do
        local remote_path="/proc/${target_pid}/root${csv_prefix}_${suffix}"
        if kubectl exec "$debug_pod" -n "$namespace" -c csv-extract -- \
            cat "$remote_path" > "$output_dir/locust_${suffix}" 2>/dev/null; then
            extracted=$((extracted + 1))
        else
            warning "Could not extract ${suffix}"
        fi
    done

    # Clean up
    kill "$debug_pid" 2>/dev/null || true
    kubectl delete pod "$debug_pod" -n "$namespace" --ignore-not-found >/dev/null 2>&1

    info "Extracted $extracted/${#csv_suffixes[@]} CSV files via debug pod"
    return 0
}

function collect_env_metadata() {
    # Collect relevant environment variables into a JSON object for test metadata.
    #
    # Usage: collect_env_metadata
    # Output: JSON object string to stdout
    #
    # Plain variables are recorded with their value.
    # Secret variables are recorded as true/false (whether they are set).

    local plain_vars=(
        TEST_SCENARIO
        TEST_USERS
        TEST_SPAWN_RATE
        TEST_RUN_TIME
        TEST_WORKERS
        TEST_DO_CLEANUP
        LOCUST_NAMESPACE
        LOCUST_HOST
        LOCUST_SCRIPT
        AMBIENT_NAMESPACE
        ARTIFACTS_DIR
        PROJECT_NAME
        SESSIONS_TO_CREATE
        SESSION_MODE
        SESSION_CREATION_TIMEOUT
        LOAD_STEPS
        LOADTEST_SA
        LOADTEST_SA_IDENTITY
        MONITORING_COLLECTION_ENABLED
        MONITORING_URL
    )

    local secret_vars=(
        AUTH_TOKEN
        MONITORING_TOKEN
        RUNNER_API_KEY
    )

    local json="{"
    local first=true

    for var in "${plain_vars[@]}"; do
        if [[ -n "${!var+set}" ]]; then
            $first || json+=","
            first=false
            # Escape double quotes in value
            local val="${!var//\"/\\\"}"
            json+="\"${var}\":\"${val}\""
        fi
    done

    for var in "${secret_vars[@]}"; do
        $first || json+=","
        first=false
        if [[ -n "${!var:-}" ]]; then
            json+="\"${var}\":true"
        else
            json+="\"${var}\":false"
        fi
    done

    json+="}"
    echo "$json"
}

function is_truthy() {
    # Usage: is_truthy "$value"
    # Returns 0 (true) if the value is a recognized truthy string, 1 (false) otherwise

    local val="$(echo "$1" | tr '[:upper:]' '[:lower:]' | xargs)"
    case "$val" in
        1|true|yes|on|y|enable|enabled)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}