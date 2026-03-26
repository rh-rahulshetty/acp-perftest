# user-journey scenario

Single-shot end-to-end load test that simulates a complete user workflow through the Ambient Code Platform. Each virtual user performs the full journey **exactly once**: creating a session, waiting for the runner pod, adding a repository, triggering a mock Claude interaction, and consuming the SSE event stream. Users that finish early idle until the test ends.

This means `TEST_USERS` directly controls how many sessions are created (one per user). Use `TEST_SPAWN_RATE` to control concurrency pressure:
- **Burst test**: `TEST_USERS=50 TEST_SPAWN_RATE=50` — all 50 sessions created at once
- **Ramp test**: `TEST_USERS=50 TEST_SPAWN_RATE=5` — gradual pressure, 5 new sessions/second

## What it tests

| Step | Endpoint | What it measures |
|------|----------|-----------------|
| 1 | `POST /agentic-sessions` | Session creation (backend + operator) |
| 2 | `GET /agentic-sessions/:name` (poll) | Runner pod startup time until `Running` phase |
| 3 | `POST /agentic-sessions/:name/repos` | Repo cloning via backend → runner proxy |
| 4 | `POST /agui/run` | AG-UI run trigger (returns immediately) |
| 5 | `GET /agui/events` (SSE) | Full SSE event stream consumption until `RUN_FINISHED` |

The runner uses `ANTHROPIC_API_KEY=mock-replay-key`, which activates the mock replay client. The prompt contains "comprehensive" to trigger the `comprehensive.jsonl` fixture — a multi-turn interaction with thinking blocks, tool calls, and sub-agent messages.

## Usage

```bash
# Burst — 10 users all at once
export TEST_SCENARIO=user-journey
export TEST_USERS=10
export TEST_SPAWN_RATE=10
export TEST_RUN_TIME=10m
./ci-scripts/load-test.sh

# Ramp — 50 users, 5 per second
export TEST_SCENARIO=user-journey
export TEST_USERS=50
export TEST_SPAWN_RATE=5
export TEST_RUN_TIME=15m
./ci-scripts/load-test.sh

# Use a different dummy repo
export TEST_SCENARIO=user-journey
export DUMMY_REPO_URL=https://github.com/octocat/Spoon-Knife.git
./ci-scripts/load-test.sh
```

> **Note**: `TEST_RUN_TIME` should be long enough for all users to complete the
> journey (session creation + pod startup + repo clone + SSE stream). Users that
> finish early idle until the test ends.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PROJECT_NAME` | `user-journey` | Target project namespace |
| `DUMMY_REPO_URL` | `https://github.com/octocat/Hello-World.git` | Public repo URL added to each session |
| `SESSION_READY_TIMEOUT` | `300` | Seconds to wait for runner pod to reach `Running` |
| `SSE_TIMEOUT` | `120` | Seconds to wait for SSE stream to complete |
| `TEST_USERS` | `10` | Number of sessions to create (one per user) |
| `TEST_SPAWN_RATE` | `2` | Users spawned per second (controls concurrency ramp) |
| `TEST_RUN_TIME` | `5m` | Total test duration (must exceed journey completion time) |
| `TEST_WORKERS` | `3` | Locust worker replicas |

## Interpreting results

Locust reports show each step as a separate named request (prefixed `01`–`05` for ordering):

- **01 POST /agentic-sessions (create)** — should complete in <1s; failures indicate backend or operator issues
- **02 GET /agentic-sessions/:name (poll status)** — high response times here reflect runner pod scheduling and startup latency
- **03 POST /agentic-sessions/:name/repos (add repo)** — measures the backend→runner proxy and git clone; depends on network access to the repo URL
- **04 POST /agui/run (trigger comprehensive)** — should return immediately (~100ms); the backend returns `{runId, threadId}` and proxies to the runner in the background
- **05 GET /agui/events (SSE stream)** — total time includes the full mock interaction replay; failures here indicate SSE proxy or runner issues

## Prerequisites

- ACP deployed on the cluster (`ci-scripts/setup-cluster.sh`)
- Locust operator installed (`ci-scripts/setup-cluster.sh` handles this)
- The loadtest service account token exists in `ambient-code` namespace
