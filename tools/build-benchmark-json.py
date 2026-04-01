#!/usr/bin/env python3
"""build-benchmark-json.py — Consolidate test metadata and monitoring data into a benchmark JSON.

Usage:
    python3 build-benchmark-json.py --artifacts-dir artifacts/session-crud --output benchmark.json
"""

import argparse
import json
import os
import sys
from datetime import datetime


def build_benchmark(artifacts_dir: str) -> dict:
    """Build a consolidated benchmark dict from collected artifacts."""
    benchmark: dict = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "test_metadata": {},
        "monitoring": {},
    }

    # Test metadata
    metadata_path = os.path.join(artifacts_dir, "test-metadata.json")
    if os.path.isfile(metadata_path):
        with open(metadata_path) as f:
            benchmark["test_metadata"] = json.load(f)

    # OPL monitoring data (if collected)
    monitoring_data_path = os.path.join(artifacts_dir, "monitoring", "benchmark-data.json")
    if os.path.isfile(monitoring_data_path):
        with open(monitoring_data_path) as f:
            benchmark["monitoring"] = json.load(f)

    # Fallback: if percentile50 is missing (e.g. data collected without the
    # status_data_wrapper.py monkey-patch), derive it from median.  Not
    # identical to the interpolated percentile, but close enough as a safety net.
    _inject_percentile50(benchmark.get("monitoring", {}))

    return benchmark


def _inject_percentile50(monitoring: dict) -> None:
    """Walk monitoring dict and copy 'median' to 'percentile50' where missing."""
    for value in monitoring.values():
        if isinstance(value, dict):
            if "median" in value and "percentile50" not in value:
                value["percentile50"] = value["median"]
            else:
                _inject_percentile50(value)


def main():
    parser = argparse.ArgumentParser(description="Build benchmark JSON from load test artifacts")
    parser.add_argument("--artifacts-dir", required=True, help="Path to scenario artifacts directory")
    parser.add_argument("--output", required=True, help="Output benchmark JSON path")
    args = parser.parse_args()

    if not os.path.isdir(args.artifacts_dir):
        print(f"ERROR: artifacts directory not found: {args.artifacts_dir}", file=sys.stderr)
        sys.exit(1)

    benchmark = build_benchmark(args.artifacts_dir)

    with open(args.output, "w") as f:
        json.dump(benchmark, f, indent=2)

    print(f"Benchmark JSON written to {args.output}")


if __name__ == "__main__":
    main()
