"""session-info — Load test for the session GET-by-id endpoint.

Simulates users repeatedly fetching a single session's details, mirroring
the real frontend polling pattern (500ms–5s) on the session detail page.

A single session is created during global setup. All virtual users
poll that same session concurrently.

Step load profiles via LOAD_STEPS (optional):

  When set, overrides --users/--run-time with a stepped profile.
  Format: "duration:users,duration:users,..."

  Example: LOAD_STEPS="5m:10,5m:50,5m:100"
    Step 1 — 0-5min:   10 users
    Step 2 — 5-10min:  50 users
    Step 3 — 10-15min: 100 users

  Duration supports s (seconds) and m (minutes).
  When unset, locust uses flat --users/--spawn-rate/--run-time as normal.

Environment variables (set via locusttest.yaml env or shell):
  PROJECT_NAME           Target project name (default: session-info)
  AUTH_TOKEN              Bearer token (SA token created by setup-cluster.sh)
  LOAD_STEPS             Step profile string (optional, see above)
  LOADTEST_SA_IDENTITY   SA identity for X-Remote-User header
                         (default: system:serviceaccount:ambient-code:loadtest-sa)
"""

import os
import logging
import re
import time

import requests as req_lib
from locust import HttpUser, LoadTestShape, task, between, events
from locust.runners import WorkerRunner

logger = logging.getLogger(__name__)

PROJECT_NAME = os.environ.get("PROJECT_NAME", "session-info")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")
LOAD_STEPS = os.environ.get("LOAD_STEPS", "")
LOADTEST_SA_IDENTITY = os.environ.get(
    "LOADTEST_SA_IDENTITY",
    "system:serviceaccount:ambient-code:loadtest-sa",
)
RUNNER_API_KEY = os.environ.get("RUNNER_API_KEY", "mock-replay-key")


def _parse_duration(s):
    """Parse a duration string like '5m', '30s', '1m30s' into seconds."""
    total = 0
    for value, unit in re.findall(r"(\d+)\s*(m|s)", s.strip().lower()):
        total += int(value) * (60 if unit == "m" else 1)
    if total == 0:
        # bare number = seconds
        total = int(s.strip())
    return total


def _parse_load_steps(steps_str):
    """Parse LOAD_STEPS env var into list of (duration_s, users)."""
    steps = []
    for part in steps_str.split(","):
        part = part.strip()
        if not part:
            continue
        tokens = part.split(":")
        if len(tokens) != 2:
            raise ValueError(
                f"Invalid LOAD_STEPS segment '{part}': expected 'duration:users'"
            )
        duration_s = _parse_duration(tokens[0])
        users = int(tokens[1])
        steps.append((duration_s, users))
    return steps


def _build_headers():
    headers = {
        "Content-Type": "application/json",
        "X-Remote-User": LOADTEST_SA_IDENTITY,
    }
    if AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {AUTH_TOKEN}"
    else:
        logger.warning("AUTH_TOKEN is empty — requests will fail with 401")
    return headers


# Session name — discovered once per worker process, shared by all users in that process
_session_name = None


def _discover_session(host, headers):
    """Find the first session in the project by listing sessions.

    Caches the result in _session_name so it's only called once per process.
    """
    global _session_name
    if _session_name is not None:
        return _session_name

    url = f"{host}/api/projects/{PROJECT_NAME}/agentic-sessions?limit=1"
    logger.info("Discovering session: GET %s", url)
    for attempt in range(10):
        try:
            resp = req_lib.get(url, headers=headers)
            if resp.ok:
                body = resp.json()
                logger.info("Discovery attempt %d: status=%d body_keys=%s", attempt + 1, resp.status_code, list(body.keys()) if isinstance(body, dict) else type(body).__name__)
                items = body.get("items", [])
                if items:
                    item = items[0]
                    # Session name is in metadata.name (k8s-style resource)
                    name = (
                        item.get("metadata", {}).get("name")
                        or item.get("name")
                        or item.get("id")
                    )
                    if name:
                        _session_name = name
                        logger.info("Session discovered: %s", _session_name)
                        return _session_name
                    logger.warning("Discovery attempt %d: item found but no name field. Keys: %s", attempt + 1, list(item.keys()))
                else:
                    logger.info("Discovery attempt %d: no items yet", attempt + 1)
            else:
                logger.warning("Discovery attempt %d: status=%d body=%s", attempt + 1, resp.status_code, resp.text[:300])
        except Exception as exc:
            logger.warning("Discovery attempt %d failed: %s", attempt + 1, exc)
        time.sleep(2)
    logger.error("Session discovery failed after 10 attempts")
    return None


# ---------------------------------------------------------------------------
# Global setup — runs once on master (or standalone)
# ---------------------------------------------------------------------------
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Create project and the session all users will poll."""
    if isinstance(environment.runner, WorkerRunner):
        return

    host = environment.host
    headers = _build_headers()

    logger.info("Global setup: project=%s", PROJECT_NAME)

    # Create project (409 = already exists)
    resp = req_lib.post(
        f"{host}/api/projects", json={"name": PROJECT_NAME}, headers=headers,
    )
    if resp.status_code not in (200, 201, 409):
        logger.error(
            "Failed to create project %s: %s %s",
            PROJECT_NAME, resp.status_code, resp.text,
        )
        environment.runner.quit()
        return
    logger.info("Project %s ready (status %d)", PROJECT_NAME, resp.status_code)

    # Set runner secrets so session creation validation passes
    logger.info("Setting runner secrets for project %s …", PROJECT_NAME)
    resp = req_lib.put(
        f"{host}/api/projects/{PROJECT_NAME}/runner-secrets",
        json={"data": {"ANTHROPIC_API_KEY": RUNNER_API_KEY}},
        headers=headers,
    )
    if resp.status_code not in (200, 201, 204):
        logger.error(
            "Failed to set runner secrets: %s %s",
            resp.status_code, resp.text[:200],
        )

    # Create the single session
    base = f"{host}/api/projects/{PROJECT_NAME}/agentic-sessions"
    payload = {"displayName": "lt-session-info", "labels": {"loadtest": "true"}}
    session_name = None
    for attempt in range(3):
        resp = req_lib.post(base, json=payload, headers=headers)
        if resp.status_code in (200, 201):
            data = resp.json()
            session_name = (
                data.get("metadata", {}).get("name")
                or data.get("name")
            )
            break
        elif resp.status_code == 500 and attempt < 2:
            time.sleep(1.1)
        else:
            logger.error(
                "Failed to create session: %s %s", resp.status_code, resp.text[:200],
            )

    if not session_name:
        logger.error("Failed to create session — aborting")
        environment.runner.quit()
        return
    logger.info("Session created: %s", session_name)


# ---------------------------------------------------------------------------
# Virtual user
# ---------------------------------------------------------------------------
class SessionInfoUser(HttpUser):
    wait_time = between(0.2, 2)

    def on_start(self):
        self.headers = _build_headers()
        # Discover session once per worker process (cached in _session_name)
        self._session_name = _discover_session(self.host, self.headers)
        if not self._session_name:
            raise RuntimeError(
                "Could not discover session after 10 attempts — "
                "check AUTH_TOKEN, LOCUST_HOST, and PROJECT_NAME"
            )

    @task
    def get_session(self):
        """Fetch a single session by name — the primary load target."""
        with self.client.get(
            f"/api/projects/{PROJECT_NAME}/agentic-sessions/{self._session_name}",
            headers=self.headers,
            name="GET /agentic-sessions/:name",
            catch_response=True,
        ) as resp:
            if resp.ok:
                resp.success()
            else:
                resp.failure(f"Get failed: {resp.status_code} {resp.text[:200]}")


# ---------------------------------------------------------------------------
# Step load shape (only active when LOAD_STEPS is set)
# ---------------------------------------------------------------------------
if LOAD_STEPS:
    _steps = _parse_load_steps(LOAD_STEPS)
    # Build cumulative time boundaries: [(end_time, users), ...]
    _step_schedule = []
    _cursor = 0
    for duration_s, users in _steps:
        _cursor += duration_s
        _step_schedule.append((_cursor, users))
    _total_duration = _cursor

    logger.info("Step load profile enabled (%d steps, %ds total):", len(_steps), _total_duration)
    _t = 0
    for i, (duration_s, users) in enumerate(_steps):
        logger.info(
            "  Step %d: %s–%s → %d users",
            i + 1,
            f"{_t // 60}m{_t % 60}s",
            f"{(_t + duration_s) // 60}m{(_t + duration_s) % 60}s",
            users,
        )
        _t += duration_s

    class SteppedShape(LoadTestShape):
        """Custom shape that steps through user counts over time."""

        def tick(self):
            run_time = self.get_run_time()
            for end_time, users in _step_schedule:
                if run_time < end_time:
                    # Spawn all target users immediately
                    return (users, users)
            # All steps exhausted — stop the test
            return None
