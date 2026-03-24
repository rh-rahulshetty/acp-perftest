# session-create

Evaluates backend throughput and stability when multiple users create sessions concurrently. Measures creation latency, failure rate, and name-collision behavior.

**Target:** `POST /api/projects/:project/agentic-sessions` on the backend service directly (k8s FQDN).

**Note:** The backend names sessions as `session-<unix_seconds>`, so concurrent requests within the same second will collide. This is expected and part of what this scenario measures.

## Usage

```bash
export TEST_SCENARIO=session-create
./ci-scripts/load-test.sh
```

## Scenario-specific environment variables

| Variable | Default | Description |
|---|---|---|
| `PROJECT_NAME` | `session-create` | Target project name |

## Standard environment variables

| Variable | Default | Description |
|---|---|---|
| `TEST_USERS` | `10` | Number of simulated users |
| `TEST_SPAWN_RATE` | `2` | Users spawned per second |
| `TEST_RUN_TIME` | `5m` | Test duration |
| `TEST_WORKERS` | `2` | Locust worker replicas |
| `AUTH_TOKEN` | auto-detected | Bearer token (SA token from loadtest-sa) |
| `LOCUST_HOST` | `http://backend-service.<ns>.svc.cluster.local:8080` | Override target host |
| `AMBIENT_NAMESPACE` | `ambient-code` | Namespace where ambient is deployed |

## Example

```bash
export TEST_SCENARIO=session-create
export TEST_USERS=100
export TEST_SPAWN_RATE=5
export TEST_RUN_TIME=3m
./ci-scripts/load-test.sh
```
