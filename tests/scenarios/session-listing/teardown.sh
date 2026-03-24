#!/bin/bash
# teardown.sh — Post-test cleanup for session-listing scenario.
#
# Sourced by ci-scripts/load-test.sh after the test completes.

PROJECT_NAME="${PROJECT_NAME:-session-listing}"
SESSION_CREATION_TIMEOUT="${SESSION_CREATION_TIMEOUT:-300}"

wait_for_agenticsessions "$PROJECT_NAME" "$SESSION_CREATION_TIMEOUT"
save_agenticsessions "$PROJECT_NAME" "$RUN_ARTIFACTS/agenticsessions"

info "Deleting sessions for project $PROJECT_NAME"
oc delete agenticsessions -n "$PROJECT_NAME" --all

info "session-listing teardown complete"
