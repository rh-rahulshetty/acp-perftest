# session-listing

Simulates dashboard users repeatedly listing sessions. Each virtual user creates a configurable number of sessions during setup, then continuously hits the listing endpoint.

**Target:** `GET /api/projects/:project/agentic-sessions` on the backend service directly (k8s FQDN).

## Usage

```bash
export TEST_SCENARIO=session-listing
./ci-scripts/load-test.sh
```

## Scenario-specific environment variables

| Variable | Default | Description |
|---|---|---|
| `PROJECT_NAME` | `session-listing` | Target project name |
| `SESSIONS_TO_CREATE` | `20` | Number of sessions each virtual user creates during setup |

## Standard environment variables

| Variable | Default | Description |
|---|---|---|
| `TEST_USERS` | `10` | Number of simulated users |
| `TEST_SPAWN_RATE` | `2` | Users spawned per second |
| `TEST_RUN_TIME` | `5m` | Test duration |
| `TEST_WORKERS` | `3` | Locust worker replicas |
| `AUTH_TOKEN` | auto-detected | Bearer token (falls back to `oc whoami -t`) |
| `LOCUST_HOST` | `http://backend-service.<ns>.svc.cluster.local:8080` | Override target host |
| `AMBIENT_NAMESPACE` | `ambient-code` | Namespace where ambient is deployed |

## Example

```bash
export TEST_SCENARIO=session-listing
export SESSIONS_TO_CREATE=50

export TEST_USERS=20
export TEST_SPAWN_RATE=0.083
export TEST_RUN_TIME=5m
export SESSION_CREATION_TIMEOUT=60
export TEST_WORKERS=3

./ci-scripts/load-test.sh
```
