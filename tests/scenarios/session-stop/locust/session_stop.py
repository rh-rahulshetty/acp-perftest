"""session-stop — Load test for concurrent session stop operations.

Each virtual user creates a session during on_start (not measured), then
calls the stop endpoint exactly once as its primary task.  After stopping,
the user idles for the remainder of the test.

TEST_USERS controls how many sessions are created and stopped.
TEST_SPAWN_RATE controls how many stop requests hit the API per second.

Examples:
  Burst:  TEST_USERS=50 TEST_SPAWN_RATE=50  — stop 50 sessions at once
  Ramp:   TEST_USERS=50 TEST_SPAWN_RATE=5   — 5 stops/second over ~10s

Because each worker creates its own sessions in on_start, there is no need
for cross-worker coordination — every stop call targets a unique session.

A global setup ensures the target project and runner secrets exist.
Teardown (via teardown.sh) deletes any remaining sessions.

Environment variables (set via locusttest.yaml env or shell):
  PROJECT_NAME           Target project name (default: session-stop)
  AUTH_TOKEN              Bearer token (SA token created by setup.sh)
  LOADTEST_SA_IDENTITY   SA identity for X-Forwarded-User header
                         (default: system:serviceaccount:ambient-code:loadtest-sa)
  RUNNER_API_KEY         Runner API key for session validation
                         (default: mock-replay-key)
  SESSION_READY_WAIT     Seconds to wait after creating a session before
                         allowing the stop task to fire, giving the runner
                         pod time to start (default: 60)
"""

import os
import logging
import time

import requests as req_lib
from locust import HttpUser, task, constant, events
from locust.runners import WorkerRunner

logger = logging.getLogger(__name__)

PROJECT_NAME = os.environ.get("PROJECT_NAME", "session-stop")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")
LOADTEST_SA_IDENTITY = os.environ.get(
    "LOADTEST_SA_IDENTITY",
    "system:serviceaccount:ambient-code:loadtest-sa",
)
RUNNER_API_KEY = os.environ.get("RUNNER_API_KEY", "mock-replay-key")
SESSION_READY_WAIT = int(os.environ.get("SESSION_READY_WAIT", "20"))


def _build_headers():
    headers = {
        "Content-Type": "application/json",
        "X-Forwarded-User": LOADTEST_SA_IDENTITY,
    }
    if AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {AUTH_TOKEN}"
    return headers


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Global setup — ensure the project exists. Runs only on master/standalone."""
    if isinstance(environment.runner, WorkerRunner):
        return

    host = environment.host
    headers = _build_headers()

    logger.info("Global setup: ensuring project %s exists …", PROJECT_NAME)
    resp = req_lib.post(f"{host}/api/projects", json={"name": PROJECT_NAME}, headers=headers)
    if resp.status_code in (200, 201, 409):
        logger.info("Project %s ready (status %d)", PROJECT_NAME, resp.status_code)
    else:
        logger.error("Failed to create project %s: %s %s", PROJECT_NAME, resp.status_code, resp.text)
        return

    logger.info("Setting runner secrets for project %s …", PROJECT_NAME)
    resp = req_lib.put(
        f"{host}/api/projects/{PROJECT_NAME}/runner-secrets",
        json={"data": {"ANTHROPIC_API_KEY": RUNNER_API_KEY}},
        headers=headers,
    )
    if resp.status_code in (200, 201, 204):
        logger.info("Runner secrets set for project %s (status %d)", PROJECT_NAME, resp.status_code)
    else:
        logger.error("Failed to set runner secrets for %s: %s %s", PROJECT_NAME, resp.status_code, resp.text)


class SessionStopUser(HttpUser):
    wait_time = constant(0)

    def on_start(self):
        """Create a session (unmeasured) so there is something to stop."""
        self.headers = _build_headers()
        self.base = f"/api/projects/{PROJECT_NAME}/agentic-sessions"
        self._session_name = None
        self._stopped = False

        payload = {
            "displayName": "lt-stop-test",
            "labels": {"loadtest": "true"},
        }

        for attempt in range(3):
            resp = req_lib.post(
                f"{self.host}{self.base}",
                json=payload,
                headers=self.headers,
            )
            if resp.status_code in (200, 201):
                self._session_name = resp.json().get("name")
                logger.info("Created session %s — waiting %ds for pod readiness", self._session_name, SESSION_READY_WAIT)
                break
            elif resp.status_code == 500 and attempt < 2:
                time.sleep(1.1)
            else:
                logger.error(
                    "Failed to create session for stop test: %s %s",
                    resp.status_code, resp.text[:200],
                )

        if self._session_name and SESSION_READY_WAIT > 0:
            time.sleep(SESSION_READY_WAIT)

    @task
    def stop_session(self):
        """Stop the session exactly once, then idle."""
        if self._stopped or self._session_name is None:
            return

        with self.client.post(
            f"{self.base}/{self._session_name}/stop",
            headers=self.headers,
            name="POST /agentic-sessions/{id}/stop",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 201, 202, 204):
                resp.success()
                logger.info("Stopped session %s", self._session_name)
            else:
                resp.failure(f"Stop failed: {resp.status_code} {resp.text[:200]}")

        self._stopped = True
