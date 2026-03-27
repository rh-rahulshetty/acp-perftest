"""session-create — Load test for concurrent session creation.

Each virtual user creates exactly one session, then idles until the test
ends. TEST_USERS controls the total number of sessions created and
TEST_SPAWN_RATE controls how many concurrent creation requests hit the
API per second.

Examples:
  Burst:  TEST_USERS=50 TEST_SPAWN_RATE=50  — all 50 sessions at once
  Ramp:   TEST_USERS=50 TEST_SPAWN_RATE=5   — 5 new sessions/second

A global setup ensures the target project exists. Teardown (via
teardown.sh) deletes all sessions after the test.

Environment variables (set via locusttest.yaml env or shell):
  PROJECT_NAME           Target project name (default: session-create)
  AUTH_TOKEN              Bearer token (SA token created by setup.sh)
  LOADTEST_SA_IDENTITY   SA identity for X-Forwarded-User header
                         (default: system:serviceaccount:ambient-code:loadtest-sa)
"""

import os
import logging

import requests as req_lib
from locust import HttpUser, task, between, events
from locust.runners import WorkerRunner

logger = logging.getLogger(__name__)

PROJECT_NAME = os.environ.get("PROJECT_NAME", "session-create")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")
LOADTEST_SA_IDENTITY = os.environ.get(
    "LOADTEST_SA_IDENTITY",
    "system:serviceaccount:ambient-code:loadtest-sa",
)
RUNNER_API_KEY = os.environ.get("RUNNER_API_KEY", "mock-replay-key")


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

    # Set runner secrets so session creation validation passes
    # (backend checks ambient-runner-secrets K8s Secret exists with a non-empty key)
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


class SessionCreateUser(HttpUser):
    # wait_time helps avoid repeated idle task calls
    wait_time = between(30, 60)

    def on_start(self):
        """Create exactly one session per user. Runs once when the user spawns."""
        headers = _build_headers()
        base = f"/api/projects/{PROJECT_NAME}/agentic-sessions"
        payload = {
            "displayName": "lt-create-test",
            "labels": {"loadtest": "true"},
        }
        with self.client.post(
            base,
            json=payload,
            headers=headers,
            name="POST /agentic-sessions (create)",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 201):
                resp.success()
            else:
                resp.failure(f"Create failed: {resp.status_code} {resp.text[:200]}")

    @task
    def idle(self):
        """No-op — keep the user alive after session creation."""
        pass
