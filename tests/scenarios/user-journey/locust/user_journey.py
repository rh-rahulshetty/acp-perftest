"""user-journey — Single-shot end-to-end load test simulating a full user workflow.

Each virtual user performs the complete journey exactly once, then idles:
  1. Create an agentic session
  2. Wait for the runner pod to reach "Running" phase
  3. Add a repository via the backend repos API
  4. Trigger a "comprehensive" mock Claude interaction via POST /agui/run
  5. Consume SSE events via GET /agui/events until RUN_FINISHED or RUN_ERROR

This is a single-shot scenario: TEST_USERS controls how many sessions are
created (one per user). Use TEST_SPAWN_RATE to control concurrency:
  - Burst:  TEST_USERS=50 TEST_SPAWN_RATE=50  (all at once)
  - Ramp:   TEST_USERS=50 TEST_SPAWN_RATE=5   (gradual pressure)

TEST_RUN_TIME should be long enough for all users to complete. Users that
finish early idle until the test ends.

This exercises the full ACP stack: backend API, operator, runner pod
lifecycle, repo cloning, AG-UI proxy, and SSE event streaming.

Environment variables (set via locusttest.yaml env or shell):
  PROJECT_NAME           Target project name (default: user-journey)
  AUTH_TOKEN              Bearer token (SA token created by setup-cluster.sh)
  LOADTEST_SA_IDENTITY   SA identity for X-Remote-User header
  DUMMY_REPO_URL         Public repo URL to add (default: octocat/Hello-World)
  SESSION_READY_TIMEOUT  Seconds to wait for runner pod (default: 300)
  SSE_TIMEOUT            Seconds to wait for SSE stream to complete (default: 120)
"""

import os
import logging
import time
import uuid

import requests as req_lib
from locust import HttpUser, task, between, events
from locust.runners import WorkerRunner

logger = logging.getLogger(__name__)

PROJECT_NAME = os.environ.get("PROJECT_NAME", "user-journey")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")
LOADTEST_SA_IDENTITY = os.environ.get(
    "LOADTEST_SA_IDENTITY",
    "system:serviceaccount:ambient-code:loadtest-sa",
)
RUNNER_API_KEY = os.environ.get("RUNNER_API_KEY", "mock-replay-key")
DUMMY_REPO_URL = os.environ.get(
    "DUMMY_REPO_URL", "https://github.com/octocat/Hello-World.git"
)
SESSION_READY_TIMEOUT = int(os.environ.get("SESSION_READY_TIMEOUT", "300"))
SSE_TIMEOUT = int(os.environ.get("SSE_TIMEOUT", "120"))


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


# ---------------------------------------------------------------------------
# Global setup — runs once on master (or standalone)
# ---------------------------------------------------------------------------
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Ensure the project and runner secrets exist."""
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


# ---------------------------------------------------------------------------
# Virtual user — full user journey
# ---------------------------------------------------------------------------
class UserJourneyUser(HttpUser):
    # Large wait_time so completed users effectively idle until the test ends.
    wait_time = between(30, 60)

    def on_start(self):
        """Run the full journey once when the user spawns."""
        self.headers = _build_headers()
        self.base = f"/api/projects/{PROJECT_NAME}/agentic-sessions"
        self._journey_done = False

        # ── Step 1: Create session ──
        session_name = self._create_session()
        if not session_name:
            return

        # ── Step 2: Wait for runner pod to be Running ──
        if not self._wait_for_running(session_name):
            return

        # ── Step 3: Add a repository ──
        self._add_repo(session_name)

        # ── Step 4: Trigger comprehensive mock interaction ──
        run_id, thread_id = self._trigger_run(session_name)
        if not run_id:
            return

        # ── Step 5: Consume SSE events ──
        self._consume_sse_events(session_name, run_id)

        self._journey_done = True
        logger.info("User journey complete for session %s", session_name)

    @task
    def idle(self):
        """No-op task — journey already executed in on_start."""
        pass

    # ── Step implementations ──

    def _create_session(self):
        """Create a session and return its name, or None on failure."""
        payload = {
            "displayName": "lt-user-journey",
            "labels": {"loadtest": "true"},
        }
        session_name = None
        for attempt in range(3):
            with self.client.post(
                self.base,
                json=payload,
                headers=self.headers,
                name="01 POST /agentic-sessions (create)",
                catch_response=True,
            ) as resp:
                if resp.status_code in (200, 201):
                    data = resp.json()
                    session_name = (
                        data.get("metadata", {}).get("name")
                        or data.get("name")
                    )
                    resp.success()
                    break
                elif resp.status_code == 500 and attempt < 2:
                    # Name collision — retry after brief delay
                    resp.failure(f"Create retry {attempt + 1}: {resp.status_code}")
                    time.sleep(1.1)
                else:
                    resp.failure(f"Create failed: {resp.status_code} {resp.text[:200]}")
                    return None

        if not session_name:
            logger.error("Failed to extract session name from create response")
            return None

        logger.info("Session created: %s", session_name)
        return session_name

    def _wait_for_running(self, session_name):
        """Poll session until phase=Running. Returns True on success."""
        deadline = time.time() + SESSION_READY_TIMEOUT
        poll_interval = 5

        while time.time() < deadline:
            with self.client.get(
                f"{self.base}/{session_name}",
                headers=self.headers,
                name="02 GET /agentic-sessions/:name (poll status)",
                catch_response=True,
            ) as resp:
                if resp.ok:
                    data = resp.json()
                    phase = (
                        data.get("status", {}).get("phase", "")
                        or data.get("phase", "")
                    )
                    if phase == "Running":
                        resp.success()
                        logger.info("Session %s is Running", session_name)
                        return True
                    resp.success()
                else:
                    resp.failure(f"Poll failed: {resp.status_code}")

            time.sleep(poll_interval)

        logger.error(
            "Session %s did not reach Running within %ds",
            session_name, SESSION_READY_TIMEOUT,
        )
        return False

    def _add_repo(self, session_name):
        """Add a repository to the running session."""
        payload = {
            "url": DUMMY_REPO_URL,
            "branch": "main",
        }
        with self.client.post(
            f"{self.base}/{session_name}/repos",
            json=payload,
            headers=self.headers,
            name="03 POST /agentic-sessions/:name/repos (add repo)",
            catch_response=True,
        ) as resp:
            if resp.ok:
                resp.success()
                logger.info("Repo added to session %s", session_name)
            else:
                resp.failure(f"Add repo failed: {resp.status_code} {resp.text[:200]}")

    def _trigger_run(self, session_name):
        """Trigger a comprehensive mock Claude interaction. Returns (run_id, thread_id)."""
        payload = {
            "threadId": session_name,
            "runId": str(uuid.uuid4()),
            "messages": [
                {
                    "id": str(uuid.uuid4()),
                    "role": "user",
                    "content": "Please perform a comprehensive analysis of this repository.",
                }
            ],
        }
        with self.client.post(
            f"{self.base}/{session_name}/agui/run",
            json=payload,
            headers=self.headers,
            name="04 POST /agui/run (trigger comprehensive)",
            catch_response=True,
        ) as resp:
            if resp.ok:
                data = resp.json()
                run_id = data.get("runId", "")
                thread_id = data.get("threadId", "")
                resp.success()
                logger.info("Run triggered: run_id=%s", run_id[:8] if run_id else "?")
                return run_id, thread_id
            else:
                resp.failure(f"Run trigger failed: {resp.status_code} {resp.text[:200]}")
                return None, None

    def _consume_sse_events(self, session_name, run_id):
        """Subscribe to SSE events and consume until RUN_FINISHED or RUN_ERROR."""
        event_count = 0
        finished = False

        with self.client.get(
            f"{self.base}/{session_name}/agui/events",
            headers={**self.headers, "Accept": "text/event-stream"},
            name="05 GET /agui/events (SSE stream)",
            catch_response=True,
            stream=True,
        ) as resp:
            if not resp.ok:
                resp.failure(f"SSE connect failed: {resp.status_code}")
                return

            deadline = time.time() + SSE_TIMEOUT
            try:
                for line in resp.iter_lines():
                    if time.time() > deadline:
                        logger.warning(
                            "SSE timeout after %ds (%d events received)",
                            SSE_TIMEOUT, event_count,
                        )
                        break

                    if not line:
                        continue

                    decoded = line.decode("utf-8") if isinstance(line, bytes) else line
                    if decoded.startswith("data: "):
                        event_count += 1
                        if '"RUN_FINISHED"' in decoded or '"RUN_ERROR"' in decoded:
                            finished = True
                            break

            except Exception as e:
                logger.error("SSE read error: %s", e)

            if finished:
                resp.success()
                logger.info(
                    "SSE stream complete for run %s: %d events",
                    run_id[:8] if run_id else "?", event_count,
                )
            else:
                resp.failure(
                    f"SSE stream ended without RUN_FINISHED ({event_count} events)"
                )
