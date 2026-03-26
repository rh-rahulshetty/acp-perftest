"""session-listing — Load test for the session listing endpoint.

Simulates dashboard users repeatedly hitting the session list API.
A global setup (runs once) creates the project and pre-populates sessions,
then virtual users hammer the listing endpoint.

Environment variables (set via locusttest.yaml env or shell):
  PROJECT_NAME           Target project name (default: session-listing)
  AUTH_TOKEN              Bearer token (SA token created by setup.sh)
  SESSIONS_TO_CREATE     Number of sessions to create globally (default: 20)
  LOADTEST_SA_IDENTITY   SA identity for X-Forwarded-User header
                         (default: system:serviceaccount:ambient-code:loadtest-sa)
"""

import os
import logging
import time

import requests as req_lib
from locust import HttpUser, task, between, events
from locust.runners import MasterRunner, WorkerRunner

logger = logging.getLogger(__name__)

PROJECT_NAME = os.environ.get("PROJECT_NAME", "session-listing")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")
SESSIONS_TO_CREATE = int(os.environ.get("SESSIONS_TO_CREATE", "20"))
LOADTEST_SA_IDENTITY = os.environ.get(
    "LOADTEST_SA_IDENTITY",
    "system:serviceaccount:ambient-code:loadtest-sa",
)
RUNNER_API_KEY = os.environ.get("RUNNER_API_KEY", "mock-replay-key")

# Module-level list so teardown can access session names created during setup
_created_sessions = []


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
    """Global setup — runs once on master (or standalone). Skipped on workers."""
    if isinstance(environment.runner, WorkerRunner):
        return

    host = environment.host
    headers = _build_headers()
    base = f"{host}/api/projects/{PROJECT_NAME}/agentic-sessions"

    logger.info("Global setup: creating project %s and %d sessions …", PROJECT_NAME, SESSIONS_TO_CREATE)

    # Create project (409 = already exists)
    resp = req_lib.post(f"{host}/api/projects", json={"name": PROJECT_NAME}, headers=headers)
    if resp.status_code not in (200, 201, 409):
        logger.error("Failed to create project %s: %s %s", PROJECT_NAME, resp.status_code, resp.text)
        return
    logger.info("Project %s ready (status %d)", PROJECT_NAME, resp.status_code)

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


    # Create sessions sequentially, then stop each one so that no runner pods
    # are left running on the cluster. This allows scaling to hundreds/thousands
    # of sessions without consuming pod resources (matching the "many idle" SLO).
    # The backend names sessions as session-<uuid>, so we sleep briefly between
    # requests to avoid name collisions.
    for i in range(SESSIONS_TO_CREATE):
        payload = {"displayName": f"lt-session-{i}", "labels": {"loadtest": "true"}}
        for attempt in range(3):
            resp = req_lib.post(base, json=payload, headers=headers)
            if resp.status_code in (200, 201):
                name = resp.json().get("name")
                if name:
                    _created_sessions.append(name)
                    # Stop the session immediately to prevent runner pod creation
                    stop_resp = req_lib.post(
                        f"{base}/{name}/stop", headers=headers,
                    )
                    if stop_resp.status_code not in (200, 201, 204):
                        logger.warning(
                            "Failed to stop session %s: %s",
                            name, stop_resp.status_code,
                        )
                break
            elif resp.status_code == 500 and attempt < 2:
                time.sleep(1.1)
            else:
                logger.warning("Failed to create session %d/%d: %s", i + 1, SESSIONS_TO_CREATE, resp.status_code)
                break

    logger.info("Global setup complete: created and stopped %d/%d sessions", len(_created_sessions), SESSIONS_TO_CREATE)

    if len(_created_sessions) == 0:
        logger.error("No sessions were created — aborting test")
        environment.runner.quit()


class SessionListingUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.headers = _build_headers()
        self.base = f"/api/projects/{PROJECT_NAME}/agentic-sessions"

    @task
    def list_sessions(self):
        """List all sessions in the project — the primary load target."""
        with self.client.get(
            self.base,
            headers=self.headers,
            name="GET /agentic-sessions (list)",
            catch_response=True,
        ) as resp:
            if resp.ok:
                resp.success()
            else:
                resp.failure(f"List failed: {resp.status_code} {resp.text[:200]}")
