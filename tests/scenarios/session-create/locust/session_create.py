"""session-create — Load test for concurrent session creation.

Simulates multiple users creating sessions simultaneously to evaluate
backend throughput and stability under concurrent creation load.

A global setup ensures the target project exists. Each virtual user
then continuously creates sessions as the main load task. Created
sessions are tracked and cleaned up on test stop.

Note: The backend generates session names as session-<unix_seconds>,
so concurrent requests within the same second will collide (409/500).
This is expected and part of what this test measures.

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


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Global teardown — list sessions from the API and delete them.

    In distributed mode workers track sessions locally but the master
    runs this handler, so in-memory tracking is unreliable. Instead we
    query the API for all sessions in the project and delete them.
    """
    if isinstance(environment.runner, WorkerRunner):
        return

    host = environment.host
    headers = _build_headers()
    base = f"{host}/api/projects/{PROJECT_NAME}/agentic-sessions"

    # Fetch sessions from the API instead of relying on in-memory tracking
    sessions = []
    try:
        resp = req_lib.get(base, headers=headers)
        if resp.ok:
            data = resp.json()
            # Handle both list and wrapped response formats
            items = data if isinstance(data, list) else data.get("items", data.get("sessions", []))
            sessions = [s.get("name") for s in items if s.get("name")]
    except Exception as exc:
        logger.warning("Failed to list sessions for cleanup: %s", exc)

    logger.info("Global teardown: deleting %d sessions …", len(sessions))
    for name in sessions:
        try:
            req_lib.delete(f"{base}/{name}", headers=headers)
        except Exception:
            pass
    logger.info("Global teardown complete")


class SessionCreateUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.headers = _build_headers()
        self.base = f"/api/projects/{PROJECT_NAME}/agentic-sessions"

    @task
    def create_session(self):
        """Create a session — the primary load target."""
        payload = {
            "displayName": "lt-create-test",
            "labels": {"loadtest": "true"},
        }
        with self.client.post(
            self.base,
            json=payload,
            headers=self.headers,
            name="POST /agentic-sessions",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 201):
                resp.success()
            else:
                resp.failure(f"Create failed: {resp.status_code} {resp.text[:200]}")
