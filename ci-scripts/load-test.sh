#!/bin/bash

set -o nounset
set -o errexit
set -o pipefail

source "$(dirname "$0")/lib.sh"


SCENARIOS_DIR="tests/scenarios"

SCENARIO=$SCENARIOS_DIR/${SCENARIO:-"dummy"}

if [[ ! -f "$SCENARIO" ]]; then
    fatal "Scenario file not found: $SCENARIO"
fi

# Setup test scenario
info "Setup for $TEST_SCENARIO scenario"
[ -f $SCENARIO/setup.sh ] && source $SCENARIO/setup.sh
