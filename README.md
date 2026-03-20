# Ambient Code Platform (ACP) – Performance & Load Testing

This repository provides tooling to set up an OpenShift cluster with the Ambient Code Platform and run load/performance tests against it.

## Prerequisites

- **OpenShift CLI** (`oc`) – logged in with admin or namespace-admin privileges
- **Git**
- **Bash** (4.x+)
- **OpenSSL** – used for random password generation
- **envsubst** (from `gettext`) – used for template rendering

## Repository Structure

```
├── ci-scripts/
│   ├── setup-cluster.sh   # Main cluster setup entrypoint
│   └── lib.sh             # Shared helpers (logging, secrets, git clone)
├── config/
│   └── ambient/
│       ├── .env           # Environment configuration sample
│       └── secrets.yaml   # Kubernetes secrets template (MinIO, PostgreSQL, Unleash)
├── tools/                 # Load test scripts (planned)
└── workspaces/            # Cloned platform repo (gitignored, created at runtime)
```

## Command Usage

### Setup Cluster

```bash
# export MINIO_ROOT_PASSWORD="<password>"
# export POSTGRES_PASSWORD="<password>"
# export UNLEASH_ADMIN_PASSWORD="<password>"

./ci-scripts/setup-cluster.sh
```


```bash
export TEST_SCENARIO=session-crud
export TEST_USERS=20
export TEST_SPAWN_RATE=5
export TEST_RUN_TIME=5m
export TEST_WORKERS=3

./ci-scripts/load-test.sh
```


```bash
# export DELETE_SECRETS=true

./ci-scripts/clean-cluster.sh
```