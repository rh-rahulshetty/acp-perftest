#!/usr/bin/env python3
"""build-benchmark-json.py — Consolidate Locust CSV stats and metadata into a benchmark JSON.

Usage:
    python3 build-benchmark-json.py --artifacts-dir artifacts/session-crud --output benchmark.json
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime


def parse_locust_stats(csv_path: str) -> list[dict]:
    """Parse Locust stats CSV into a list of dicts."""
    if not os.path.isfile(csv_path):
        return []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        return [row for row in reader]


def parse_locust_failures(csv_path: str) -> list[dict]:
    """Parse Locust failures CSV."""
    if not os.path.isfile(csv_path):
        return []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        return [row for row in reader]


def build_benchmark(artifacts_dir: str) -> dict:
    """Build a consolidated benchmark dict from collected artifacts."""
    benchmark: dict = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "test_metadata": {},
        "locust_stats": [],
        "locust_failures": [],
        "monitoring": {},
    }

    # Test metadata
    metadata_path = os.path.join(artifacts_dir, "test-metadata.json")
    if os.path.isfile(metadata_path):
        with open(metadata_path) as f:
            benchmark["test_metadata"] = json.load(f)

    # Locust stats
    locust_dir = os.path.join(artifacts_dir, "locust")
    stats_path = os.path.join(locust_dir, "locust_stats.csv")
    benchmark["locust_stats"] = parse_locust_stats(stats_path)

    # Locust failures
    failures_path = os.path.join(locust_dir, "locust_failures.csv")
    benchmark["locust_failures"] = parse_locust_failures(failures_path)

    # OPL monitoring data (if collected)
    monitoring_data_path = os.path.join(artifacts_dir, "monitoring", "benchmark-data.json")
    if os.path.isfile(monitoring_data_path):
        with open(monitoring_data_path) as f:
            benchmark["monitoring"] = json.load(f)

    return benchmark


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

    # Print summary
    stats = benchmark.get("locust_stats", [])
    aggregated = [s for s in stats if s.get("Name") == "Aggregated"]
    if aggregated:
        agg = aggregated[0]
        print(f"  Requests: {agg.get('Request Count', 'N/A')}")
        print(f"  Failures: {agg.get('Failure Count', 'N/A')}")
        print(f"  Avg (ms): {agg.get('Average Response Time', 'N/A')}")
        print(f"  p99 (ms): {agg.get('99%', 'N/A')}")
        print(f"  RPS:      {agg.get('Requests/s', 'N/A')}")


if __name__ == "__main__":
    main()
