# session-info

Simulates users repeatedly fetching a single session by name, mirroring the frontend's session detail page polling (500ms–5s).

One session is created during setup. All virtual users poll that same session concurrently.

**Target:** `GET /api/projects/:project/agentic-sessions/:name` on the backend service directly (k8s FQDN).

## Load profiles

### Flat (default)

Uses `TEST_USERS`, `TEST_SPAWN_RATE`, and `TEST_RUN_TIME` for a constant load throughout the test.

### Stepped (via `LOAD_STEPS`)

When `LOAD_STEPS` is set, it overrides `--users`/`--run-time` with a stepped profile. Format:

```
LOAD_STEPS="duration:users,duration:users,..."
```

Duration supports `s` (seconds) and `m` (minutes). Example:

```
LOAD_STEPS="5m:10,5m:50,5m:100"
```

This runs:
- **Step 1** (0–5 min): 10 users
- **Step 2** (5–10 min): 50 users
- **Step 3** (10–15 min): 100 users

The test ends automatically after all steps complete.

## Usage

```bash
# Flat profile
export TEST_SCENARIO=session-info
./ci-scripts/load-test.sh

# Stepped profile
export TEST_SCENARIO=session-info
export LOAD_STEPS="5m:10,5m:50,5m:100"
./ci-scripts/load-test.sh
```

## Scenario-specific environment variables

| Variable | Default | Description |
|---|---|---|
| `PROJECT_NAME` | `session-info` | Target project name |
| `LOAD_STEPS` | _(empty)_ | Stepped load profile. Empty = flat profile using `TEST_USERS`/`TEST_RUN_TIME` |

## Standard environment variables

| Variable | Default | Description |
|---|---|---|
| `TEST_USERS` | `10` | Number of simulated users (ignored when `LOAD_STEPS` is set) |
| `TEST_SPAWN_RATE` | `2` | Users spawned per second (ignored when `LOAD_STEPS` is set) |
| `TEST_RUN_TIME` | `5m` | Test duration (ignored when `LOAD_STEPS` is set) |
| `TEST_WORKERS` | `3` | Locust worker replicas |
| `AUTH_TOKEN` | auto-detected | Bearer token (falls back to SA secret) |
| `LOCUST_HOST` | `http://backend-service.<ns>.svc.cluster.local:8080` | Override target host |
| `AMBIENT_NAMESPACE` | `ambient-code` | Namespace where ambient is deployed |

## Examples

```bash
# 50 users, flat load, 5 minutes
export TEST_SCENARIO=session-info
export TEST_USERS=50
export TEST_SPAWN_RATE=5
export TEST_RUN_TIME=5m
./ci-scripts/load-test.sh

# Stepped: gradual ramp-up over 12 minutes
export TEST_SCENARIO=session-info
export LOAD_STEPS="2m:5,3m:20,5m:50,2m:100"
./ci-scripts/load-test.sh
```
