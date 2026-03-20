#!/bin/bash
# parse-duration.sh — Convert a duration string (e.g. "5m", "1h30m", "90s") to seconds.
# Usage: parse-duration.sh "5m"  →  300

set -o nounset
set -o errexit

INPUT="${1:-0s}"
TOTAL=0

# Extract hours, minutes, seconds
if [[ "$INPUT" =~ ([0-9]+)h ]]; then
    TOTAL=$((TOTAL + ${BASH_REMATCH[1]} * 3600))
fi
if [[ "$INPUT" =~ ([0-9]+)m ]]; then
    TOTAL=$((TOTAL + ${BASH_REMATCH[1]} * 60))
fi
if [[ "$INPUT" =~ ([0-9]+)s ]]; then
    TOTAL=$((TOTAL + ${BASH_REMATCH[1]}))
fi

# If it's just a plain number, treat as seconds
if [[ "$TOTAL" -eq 0 && "$INPUT" =~ ^[0-9]+$ ]]; then
    TOTAL="$INPUT"
fi

echo "$TOTAL"
