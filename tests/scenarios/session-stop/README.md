# session-stop

Load test for the session **stop** endpoint.

## How it works

Each virtual user:
1. Creates a session in `on_start` (unmeasured, uses `requests` library directly)
2. Calls `POST /agentic-sessions/{id}/stop` exactly once as its measured task
3. Idles for the remainder of the test

Because each worker creates its own sessions, there is no cross-worker
coordination — every stop call targets a unique session.

## Controlling the load profile

| Variable | Controls | Default |
|---|---|---|
| `TEST_USERS` | Total sessions created & stopped | `10` |
| `TEST_SPAWN_RATE` | Users spawned per second | `2` |
| `SESSION_READY_WAIT` | Seconds to wait after create before stopping | `20` |
| `TEST_RUN_TIME` | Total test duration | `5m` |
| `TEST_WORKERS` | Locust worker pods | `3` |

**Timing note:** Users spawned within the same `SESSION_READY_WAIT` window
will fire their stop calls roughly together. The effective concurrent stop
burst is approximately `TEST_SPAWN_RATE * SESSION_READY_WAIT`.

## Load profiles

### 1. Baseline — validate the scenario works

Small scale, gentle rate. Use to verify end-to-end correctness before
scaling up.

```bash
export TEST_SCENARIO=session-stop
export TEST_RUN_TIME=5m

TEST_USERS=10  TEST_SPAWN_RATE=2  ./ci-scripts/load-test.sh
```

- 10 sessions total, 2 spawned/sec
- Stops trickle in over ~5s after the wait window
- Expect: 0 failures, clean teardown

### 2. Moderate ramp — steady stop pressure

Sustained stop traffic at a controlled rate. Tests the operator's ability
to handle a continuous stream of stop requests without queuing or errors.

```bash
export TEST_SCENARIO=session-stop
export TEST_RUN_TIME=5m

TEST_USERS=50  TEST_SPAWN_RATE=5  ./ci-scripts/load-test.sh
```

- 50 sessions total, 5 spawned/sec (ramp over ~10s)
- ~100 concurrent stops hit after the wait window (5 * 20s window)
- Expect: steady throughput, no 500 errors

### 3. Concurrent burst — stop spike

All users spawn fast, creating a thundering-herd of stops after the wait
window. Tests how the backend and operator handle a large simultaneous
batch of stop requests.

```bash
export TEST_SCENARIO=session-stop
export TEST_RUN_TIME=5m

TEST_USERS=100  TEST_SPAWN_RATE=50  ./ci-scripts/load-test.sh
```

- 100 sessions total, 50 spawned/sec (all spawned in ~2s)
- All ~100 stops fire within a tight window after SESSION_READY_WAIT
- Expect: possible latency spike, monitor for 500/retry storms

### 4. Large scale ramp — sustained high volume

Many sessions stopped at a moderate rate over a longer period. Tests
sustained throughput and resource cleanup at scale.

```bash
export TEST_SCENARIO=session-stop
export TEST_RUN_TIME=10m

TEST_USERS=200  TEST_SPAWN_RATE=10  ./ci-scripts/load-test.sh
```

- 200 sessions total, 10 spawned/sec (ramp over ~20s)
- Stops spread across the wait window, ~200 concurrent in-flight
- Expect: stable RPS, no memory/resource leaks in operator

### 5. Stress burst — find the breaking point

Maximum concurrent stops to identify the system's upper limit. Use to
determine SLO boundaries and where the backend/operator starts degrading.

```bash
export TEST_SCENARIO=session-stop
export TEST_RUN_TIME=10m
export TEST_WORKERS=5

TEST_USERS=500  TEST_SPAWN_RATE=100  ./ci-scripts/load-test.sh
```

- 500 sessions total, 100 spawned/sec (all spawned in ~5s)
- Massive stop burst after the wait window
- Expect: find error rate threshold, latency degradation curve
