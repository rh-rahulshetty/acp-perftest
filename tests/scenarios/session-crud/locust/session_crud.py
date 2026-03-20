"""session-crud — Load test for AgenticSession CRUD lifecycle.

Tests the core session API endpoints:
  POST   /api/projects/:project/agentic-sessions          (create)
  GET    /api/projects/:project/agentic-sessions/:name     (get)
  GET    /api/projects/:project/agentic-sessions           (list)
  PATCH  /api/projects/:project/agentic-sessions/:name     (patch)
  DELETE /api/projects/:project/agentic-sessions/:name     (delete)

Environment variables (set via scenario setup or locust --env):
  PROJECT_NAME    Target project name (default: loadtest-project)
  AUTH_TOKEN      Bearer token for API auth
"""

import os
import random
import string
import time

from locust import HttpUser, task, between, events


PROJECT_NAME = os.environ.get("PROJECT_NAME", "loadtest-project")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")


def random_session_name():
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"loadtest-session-{suffix}"


class SessionCRUDUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.headers = {"Content-Type": "application/json"}
        if AUTH_TOKEN:
            self.headers["Authorization"] = f"Bearer {AUTH_TOKEN}"
        self.created_sessions = []

    @task(5)
    def create_and_get_session(self):
        """Create a session, then GET it, then delete it."""
        name = random_session_name()
        base = f"/api/projects/{PROJECT_NAME}/agentic-sessions"

        # Create
        payload = {
            "metadata": {"name": name},
            "spec": {
                "projectName": PROJECT_NAME,
            },
        }
        with self.client.post(
            base, json=payload, headers=self.headers, name="POST /agentic-sessions",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 201):
                resp.success()
                self.created_sessions.append(name)
            else:
                resp.failure(f"Create failed: {resp.status_code}")
                return

        # Get
        self.client.get(
            f"{base}/{name}",
            headers=self.headers,
            name="GET /agentic-sessions/:name",
        )

        # Delete
        with self.client.delete(
            f"{base}/{name}",
            headers=self.headers,
            name="DELETE /agentic-sessions/:name",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 204, 404):
                resp.success()
                if name in self.created_sessions:
                    self.created_sessions.remove(name)
            else:
                resp.failure(f"Delete failed: {resp.status_code}")

    @task(3)
    def list_sessions(self):
        """List all sessions in the project."""
        base = f"/api/projects/{PROJECT_NAME}/agentic-sessions"
        self.client.get(base, headers=self.headers, name="GET /agentic-sessions (list)")

    @task(2)
    def create_patch_delete(self):
        """Create → Patch → Delete lifecycle."""
        name = random_session_name()
        base = f"/api/projects/{PROJECT_NAME}/agentic-sessions"

        # Create
        payload = {
            "metadata": {"name": name},
            "spec": {
                "projectName": PROJECT_NAME,
            },
        }
        with self.client.post(
            base, json=payload, headers=self.headers, name="POST /agentic-sessions",
            catch_response=True,
        ) as resp:
            if resp.status_code not in (200, 201):
                resp.failure(f"Create failed: {resp.status_code}")
                return
            resp.success()

        # Patch
        patch_payload = {
            "metadata": {"labels": {"loadtest": "true"}},
        }
        self.client.patch(
            f"{base}/{name}",
            json=patch_payload,
            headers=self.headers,
            name="PATCH /agentic-sessions/:name",
        )

        # Delete
        with self.client.delete(
            f"{base}/{name}",
            headers=self.headers,
            name="DELETE /agentic-sessions/:name",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 204, 404):
                resp.success()
            else:
                resp.failure(f"Delete failed: {resp.status_code}")

    def on_stop(self):
        """Clean up any sessions that weren't deleted during the test."""
        base = f"/api/projects/{PROJECT_NAME}/agentic-sessions"
        for name in self.created_sessions:
            self.client.delete(
                f"{base}/{name}",
                headers=self.headers,
                name="DELETE /agentic-sessions/:name (cleanup)",
            )
