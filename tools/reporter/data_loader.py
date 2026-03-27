"""Data loading and processing utilities for the performance reporter dashboard."""

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


STATE_FILE = os.path.join(os.path.dirname(__file__), "dashboard_state.json")
GLOBAL_CONFIG = os.path.join(os.path.dirname(__file__), "..", "..", "config", "cluster_read_config.yaml")
SCENARIOS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "tests", "scenarios")

# Metric lookup: maps dotted key -> {name, query, step}
_METRIC_LOOKUP = None
# CSV column header cache: csv_stem -> dotted metric name
_CSV_HEADER_CACHE = {}


def _load_config_entries(config_path):
    """Load entries from a single cluster_read_config.yaml file."""
    path = os.path.abspath(config_path)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        entries = yaml.safe_load(f)
    return entries if isinstance(entries, list) else []


def get_metric_lookup():
    """Build lookup from all config files (global + scenario-specific).

    Loads the global cluster_read_config.yaml and any scenario-specific configs
    found under tests/scenarios/*/cluster_read_config.yaml.

    Keys are dotted metric names (e.g. 'measurements.ambient.agentic-operator.cpu').
    Values are dicts with 'name', 'query', 'step'.

    Also indexes by:
      - benchmark key (without 'measurements.' / 'results.' prefix)
      - CSV filename stem (underscores instead of dots)
    """
    global _METRIC_LOOKUP
    if _METRIC_LOOKUP is not None:
        return _METRIC_LOOKUP

    _METRIC_LOOKUP = {}

    # Load global config
    all_entries = _load_config_entries(GLOBAL_CONFIG)

    # Load scenario-specific configs
    scenarios_path = os.path.abspath(SCENARIOS_DIR)
    if os.path.isdir(scenarios_path):
        for scenario_dir in sorted(Path(scenarios_path).iterdir()):
            if not scenario_dir.is_dir():
                continue
            config = scenario_dir / "cluster_read_config.yaml"
            if config.exists():
                all_entries.extend(_load_config_entries(str(config)))

    for entry in all_entries:
        name = entry.get("name", "")
        query = entry.get("monitoring_query", entry.get("command", ""))
        step = entry.get("monitoring_step", "")
        info = {"name": name, "query": query.strip() if isinstance(query, str) else str(query), "step": step}

        # Index by full dotted name
        _METRIC_LOOKUP[name] = info

        # Index by benchmark key (strip 'measurements.' or 'results.' prefix)
        for prefix in ["measurements.", "results."]:
            if name.startswith(prefix):
                short = name[len(prefix):]
                _METRIC_LOOKUP[short] = info
                break

        # Index by CSV filename stem (dots -> underscores)
        csv_stem = name.replace(".", "_")
        _METRIC_LOOKUP[csv_stem] = info

    return _METRIC_LOOKUP


def get_metric_query(metric_key):
    """Look up the Prometheus query for a metric key.

    Accepts dotted keys, benchmark keys, or CSV filename stems.
    Also tries the dotted name from CSV header cache if direct lookup fails.
    Returns the query string, or empty string if not found.
    """
    lookup = get_metric_lookup()
    info = lookup.get(metric_key, {})
    if info:
        return info.get("query", "")

    # Try via CSV header cache → dotted name → config lookup
    dotted = _CSV_HEADER_CACHE.get(metric_key, "")
    if dotted:
        info = lookup.get(dotted, {})
        return info.get("query", "")

    return ""


def read_csv_metric_name(csv_path):
    """Read the second column header from a CSV file to get the dotted metric name.

    CSV files use the dotted metric name as the column header (e.g.
    'measurements.ambient.backend-api.cpu'), which is the most reliable
    source for the canonical metric name.
    """
    try:
        with open(csv_path) as f:
            header = f.readline().strip()
        cols = header.split(",")
        if len(cols) >= 2:
            return cols[1]
    except Exception:
        pass
    return ""


def _cache_csv_headers(trial_path):
    """Read all CSV column headers in a trial's monitoring/ dir and cache them."""
    monitoring = Path(trial_path) / "monitoring"
    if not monitoring.is_dir():
        return
    for csv_file in monitoring.glob("*.csv"):
        stem = csv_file.stem
        if stem not in _CSV_HEADER_CACHE:
            dotted = read_csv_metric_name(str(csv_file))
            if dotted:
                _CSV_HEADER_CACHE[stem] = dotted


def get_metric_full_name(metric_key):
    """Look up the full dotted metric name for a key.

    Resolution order:
      1. Config lookup (global + scenario-specific cluster_read_config.yaml)
      2. CSV column header cache (populated when trials are loaded)
      3. Falls back to the key itself
    """
    lookup = get_metric_lookup()
    info = lookup.get(metric_key, {})
    if info:
        return info.get("name", metric_key)

    # Try CSV header cache
    if metric_key in _CSV_HEADER_CACHE:
        return _CSV_HEADER_CACHE[metric_key]

    return metric_key


def reset_metric_lookup():
    """Reset the metric lookup cache so it gets rebuilt on next access.

    Call this when loading new trial data that may come from different scenarios.
    """
    global _METRIC_LOOKUP
    _METRIC_LOOKUP = None


def load_state():
    """Load persisted dashboard state (registered trials, custom dashboards)."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"trials": [], "custom_dashboards": []}


def save_state(state):
    """Persist dashboard state to disk."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def validate_trial_path(path):
    """Check that a trial folder contains the expected data files.
    Returns (is_valid, message).
    """
    p = Path(path)
    if not p.is_dir():
        return False, f"Directory does not exist: {path}"
    benchmark = p / "benchmark.json"
    monitoring = p / "monitoring"
    if not benchmark.exists():
        return False, "Missing benchmark.json"
    if not monitoring.is_dir():
        return False, "Missing monitoring/ directory"
    csvs = list(monitoring.glob("*.csv"))
    if not csvs:
        return False, "No CSV files found in monitoring/"
    return True, f"Valid trial: {len(csvs)} CSV metrics found"


def scan_parent_folder(parent_path):
    """Auto-discover scenarios and trials from a parent folder structure.

    Expected structure:
        parent_path/
            scenario_A/          <- scenario name derived from folder name
                trial_1/         <- trial, must contain benchmark.json + monitoring/
                trial_2/
            scenario_B/
                trial_1/

    If a scenario folder is itself a valid trial (no nested trial dirs), it is
    treated as a single-trial scenario.

    Returns list of dicts: [{"path": ..., "label": ..., "scenario": ...}, ...]
    """
    parent = Path(parent_path)
    if not parent.is_dir():
        return [], f"Not a directory: {parent_path}"

    trials = []
    for scenario_dir in sorted(parent.iterdir()):
        if not scenario_dir.is_dir() or scenario_dir.name.startswith("."):
            continue
        scenario_name = scenario_dir.name

        # Check if the scenario dir itself is a valid trial
        valid, _ = validate_trial_path(str(scenario_dir))
        if valid:
            trials.append({
                "path": str(scenario_dir),
                "label": scenario_dir.name,
                "scenario": scenario_name,
            })
            continue

        # Otherwise scan subdirs as trials
        for trial_dir in sorted(scenario_dir.iterdir()):
            if not trial_dir.is_dir() or trial_dir.name.startswith("."):
                continue
            valid, _ = validate_trial_path(str(trial_dir))
            if valid:
                trials.append({
                    "path": str(trial_dir),
                    "label": trial_dir.name,
                    "scenario": scenario_name,
                })

    if not trials:
        return [], "No valid trials found in any subdirectory"

    # Cache CSV column headers for metric name resolution
    for trial in trials:
        _cache_csv_headers(trial["path"])

    scenarios = set(t["scenario"] for t in trials)
    return trials, f"Found {len(trials)} trial(s) across {len(scenarios)} scenario(s)"


def load_benchmark(trial_path):
    """Load benchmark.json and return the monitoring measurements dict."""
    benchmark_file = Path(trial_path) / "benchmark.json"
    with open(benchmark_file) as f:
        data = json.load(f)
    return data


def load_test_metadata(trial_path):
    """Load test metadata from benchmark.json."""
    data = load_benchmark(trial_path)
    return data.get("test_metadata", {})


def flatten_benchmark_measurements(measurements, prefix=""):
    """Flatten nested measurement dict into {dotted.key: {stat: value}} pairs.

    Example output:
        {
            "ambient.agentic-operator.cpu": {"mean": 0.07, "p95": 0.14, ...},
            "ambient.agentic-operator.memory": {"mean": 136155293, ...},
        }
    """
    result = {}
    for key, value in measurements.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            # Check if this is a leaf stats node (has "mean" key)
            if "mean" in value and "samples" in value:
                result[full_key] = value
            else:
                result.update(flatten_benchmark_measurements(value, full_key))
    return result


def get_benchmark_stats(trial_path):
    """Load and flatten benchmark stats for a trial."""
    data = load_benchmark(trial_path)
    measurements = data.get("monitoring", {}).get("measurements", {})
    return flatten_benchmark_measurements(measurements)


def list_csv_metrics(trial_path):
    """List all available CSV metric names from the monitoring folder."""
    monitoring = Path(trial_path) / "monitoring"
    metrics = []
    for csv_file in sorted(monitoring.glob("*.csv")):
        name = csv_file.stem  # e.g. "measurements_ambient_agentic-operator_cpu"
        metrics.append(name)
    return metrics


def load_csv_metric(trial_path, metric_name):
    """Load a single CSV metric file and return a DataFrame with columns [timestamp, value].

    The metric_name should be the filename stem (without .csv).
    """
    csv_path = Path(trial_path) / "monitoring" / f"{metric_name}.csv"
    if not csv_path.exists():
        return pd.DataFrame(columns=["timestamp", "value"])
    df = pd.read_csv(csv_path)
    if len(df.columns) == 2:
        df.columns = ["timestamp", "value"]
    return df


def load_csv_metric_per_trial(trial_path, metric_name):
    """Load a CSV metric and return DataFrame with [elapsed_seconds, value].

    Unlike average_csv_metric_across_trials, this returns data for a single trial
    with timestamps converted to elapsed seconds from the start.
    """
    df = load_csv_metric(trial_path, metric_name)
    if df.empty:
        return df
    df = df.copy()
    df["elapsed_seconds"] = df["timestamp"] - df["timestamp"].iloc[0]
    return df[["elapsed_seconds", "value"]]


def get_metric_categories(metric_names):
    """Group metric names into categories for easier navigation.

    Returns dict like:
        {
            "Ambient Components": ["measurements_ambient_agentic-operator_cpu", ...],
            "Cluster": ["measurements_cluster_cpu_usage_rate", ...],
            ...
        }
    """
    categories = {}
    for name in metric_names:
        if name.startswith("results_GET_"):
            cat = "Endpoint Results"
        elif name.startswith("results_locust_"):
            cat = "Locust Results"
        elif name.startswith("measurements_locust_"):
            cat = "Locust Resources"
        elif name.startswith("measurements_cluster_") or name.startswith("measurements_apiserver_") or name.startswith("measurements_etcd_"):
            cat = "Cluster"
        elif name.startswith("measurements_ambient_session"):
            cat = "Sessions"
        elif name.startswith("measurements_ambient_reconcile"):
            cat = "Reconciliation"
        elif name.startswith("measurements_ambient_image_pull") or name.startswith("measurements_ambient_project_image"):
            cat = "Image Pull"
        elif name.startswith("measurements_ambient_project_session"):
            cat = "Project Sessions"
        elif name.startswith("measurements_ambient_project_"):
            cat = "Project Metrics"
        elif name.startswith("measurements_ambient_namespace_"):
            cat = "Namespace Totals"
        elif name.startswith("measurements_ambient_"):
            cat = "Ambient Components"
        else:
            cat = "Other"
        categories.setdefault(cat, []).append(name)
    return categories


def prettify_metric_name(metric_name):
    """Convert a metric filename stem to a human-readable label.

    Example: measurements_ambient_agentic-operator_cpu -> Agentic Operator CPU
    """
    name = metric_name
    for prefix in ["measurements_ambient_", "measurements_", "results_locust_", "results_GET_agentic-sessions_list_locust_", "results_"]:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name.replace("_", " ").replace("-", " ").title()


def average_benchmark_stats_across_trials(trial_paths):
    """Average benchmark statistics across multiple trials.

    Returns the same flattened dict structure but with averaged values.
    """
    all_stats = []
    for path in trial_paths:
        try:
            stats = get_benchmark_stats(path)
            all_stats.append(stats)
        except Exception:
            continue

    if not all_stats:
        return {}
    if len(all_stats) == 1:
        return all_stats[0]

    # Get union of all metric keys
    all_keys = set()
    for stats in all_stats:
        all_keys.update(stats.keys())

    averaged = {}
    stat_fields = [
        "min", "max", "mean", "median", "stdev",
        "percentile25", "percentile75", "percentile90",
        "percentile95", "percentile99", "percentile999",
        "iqr", "range", "samples", "sum",
        "non_zero_mean", "non_zero_median",
    ]

    for key in sorted(all_keys):
        values_per_stat = {sf: [] for sf in stat_fields}
        for stats in all_stats:
            if key in stats:
                for sf in stat_fields:
                    if sf in stats[key] and stats[key][sf] is not None:
                        values_per_stat[sf].append(stats[key][sf])

        averaged[key] = {}
        for sf in stat_fields:
            if values_per_stat[sf]:
                averaged[key][sf] = sum(values_per_stat[sf]) / len(values_per_stat[sf])
            else:
                averaged[key][sf] = 0.0

    return averaged


def average_csv_metric_across_trials(trial_paths, metric_name):
    """Load a CSV metric from multiple trials, align by elapsed time, and average.

    Returns a DataFrame with columns [elapsed_seconds, value] where value is the
    mean across trials at each time point.
    """
    dfs = []
    for path in trial_paths:
        df = load_csv_metric(path, metric_name)
        if df.empty:
            continue
        # Convert to elapsed seconds from start
        df["elapsed_seconds"] = df["timestamp"] - df["timestamp"].iloc[0]
        df = df[["elapsed_seconds", "value"]].copy()
        dfs.append(df)

    if not dfs:
        return pd.DataFrame(columns=["elapsed_seconds", "value"])
    if len(dfs) == 1:
        return dfs[0]

    # Interpolate all to a common time grid
    max_elapsed = max(df["elapsed_seconds"].max() for df in dfs)
    common_times = np.arange(0, max_elapsed + 1, 15)  # 15-second intervals

    interpolated = []
    for df in dfs:
        if len(df) < 2:
            continue
        interp_values = np.interp(common_times, df["elapsed_seconds"].values, df["value"].values)
        interpolated.append(interp_values)

    if not interpolated:
        return dfs[0]

    avg_values = np.mean(interpolated, axis=0)
    return pd.DataFrame({"elapsed_seconds": common_times, "value": avg_values})


def get_scenarios_data(state):
    """From the state, group trials by scenario and return structured data.

    Returns:
        {
            "scenario_tag": {
                "trials": [{"id": ..., "path": ..., "label": ...}, ...],
                "trial_paths": [path1, path2, ...],
            }
        }
    """
    scenarios = {}
    for trial in state.get("trials", []):
        scenario = trial["scenario"]
        if scenario not in scenarios:
            scenarios[scenario] = {"trials": [], "trial_paths": []}
        scenarios[scenario]["trials"].append(trial)
        scenarios[scenario]["trial_paths"].append(trial["path"])
    return scenarios


def get_all_available_metrics(state):
    """Discover all CSV metric names available across all registered trials."""
    all_metrics = set()
    for trial in state.get("trials", []):
        try:
            metrics = list_csv_metrics(trial["path"])
            all_metrics.update(metrics)
        except Exception:
            continue
    return sorted(all_metrics)


def get_all_benchmark_metric_keys(state):
    """Discover all benchmark stat keys across all registered trials."""
    all_keys = set()
    for trial in state.get("trials", []):
        try:
            stats = get_benchmark_stats(trial["path"])
            all_keys.update(stats.keys())
        except Exception:
            continue
    return sorted(all_keys)


# Metric keys (benchmark-style, without measurements./results. prefix) that
# represent byte values.  Used to decide whether to apply human-readable
# formatting.  The check also verifies the actual value is large enough to
# look like bytes (> 1024) to avoid false positives.
BYTES_METRIC_KEYWORDS = [
    "memory",
    "memory_total",
    "memory_rss_total",
    "network_bytes_total",
    "network_throughput",
    "disk_throughput_total",
    "db_total_size_bytes",
    "db_in_use_bytes",
    "content_length",
    "avg_content_length",
]


def is_bytes_metric(metric_key):
    """Check if a metric key represents a byte-valued metric.

    Matches against BYTES_METRIC_KEYWORDS by checking if the metric key
    ends with or contains any of the keywords.
    """
    key_lower = metric_key.lower().replace(".", "_")
    for kw in BYTES_METRIC_KEYWORDS:
        if key_lower.endswith(kw) or f"_{kw}_" in key_lower:
            return True
    return False


def format_bytes(value):
    """Format a byte value into human-readable string (KB, MB, GB, etc.).

    Only applies formatting if value looks like bytes (>= 1024).
    Returns the original number formatted normally otherwise.
    """
    if value is None:
        return ""
    if abs(value) < 1024:
        return f"{value:.2f} B"
    for unit in ["KB", "MB", "GB", "TB"]:
        value /= 1024
        if abs(value) < 1024:
            return f"{value:.2f} {unit}"
    return f"{value:.2f} PB"


STAT_OPTIONS = [
    {"label": "Mean", "value": "mean"},
    {"label": "Median", "value": "median"},
    {"label": "Min", "value": "min"},
    {"label": "Max", "value": "max"},
    {"label": "P25", "value": "percentile25"},
    {"label": "P75", "value": "percentile75"},
    {"label": "P90", "value": "percentile90"},
    {"label": "P95", "value": "percentile95"},
    {"label": "P99", "value": "percentile99"},
    {"label": "P99.9", "value": "percentile999"},
    {"label": "Std Dev", "value": "stdev"},
    {"label": "IQR", "value": "iqr"},
]
