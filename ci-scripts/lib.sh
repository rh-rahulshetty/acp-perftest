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