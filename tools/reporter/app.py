"""Performance Reporter Dashboard - Dash application.

Usage:
    pip install -r requirements.txt
    python app.py
"""

import json
import os
import uuid

import dash
import dash_ag_grid as dag
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Input, Output, State, ALL, MATCH, callback_context, dcc, html

from data_loader import (
    STAT_OPTIONS,
    average_benchmark_stats_across_trials,
    average_csv_metric_across_trials,
    get_all_available_metrics,
    get_all_benchmark_metric_keys,
    get_metric_categories,
    format_bytes,
    get_metric_full_name,
    is_bytes_metric,
    get_scenarios_data,
    load_csv_metric_per_trial,
    load_state,
    load_test_metadata,
    prettify_metric_name,
    save_state,
    scan_parent_folder,
    validate_trial_path,
)

# Default starting directory for the folder browser
_DEFAULT_BROWSE_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "hack")
)

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.FLATLY],
    suppress_callback_exceptions=True,
)
app.title = "Performance Reporter"

# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------

import re

def _natural_sort_key(s):
    """Sort key that handles embedded numbers: '20' < '50' < '200' < '500'."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]


def get_ordered_scenarios(state):
    """Return scenarios in user-defined order (falls back to natural sort)."""
    trials = state.get("trials", [])
    all_scenarios = set(t["scenario"] for t in trials)
    user_order = state.get("scenario_order", [])
    # Start with user-defined order, then append any new scenarios not yet ordered
    ordered = [s for s in user_order if s in all_scenarios]
    remaining = sorted(all_scenarios - set(ordered), key=_natural_sort_key)
    return ordered + remaining


def make_trial_list_item(trial):
    return dbc.ListGroupItem(
        [
            html.Div(
                [
                    html.Strong(trial["label"]),
                    html.Span(
                        f"  [{trial['scenario']}]",
                        className="text-muted ms-2",
                    ),
                ],
            ),
            html.Small(trial["path"], className="text-muted d-block text-truncate"),
        ],
        className="py-2",
    )


def build_trial_list(state):
    """Build the dynamic trial list + scenario reorder controls."""
    trials = state.get("trials", [])
    scenarios = get_ordered_scenarios(state)

    children = []

    if scenarios:
        # Scenario reorder controls
        children.append(html.H6("Scenario Order", className="mt-2"))
        children.append(html.Small(
            "Use arrows to reorder how scenarios appear in charts.",
            className="text-muted d-block mb-2",
        ))
        order_items = []
        for i, scenario in enumerate(scenarios):
            trial_count = sum(1 for t in trials if t["scenario"] == scenario)
            order_items.append(
                dbc.ListGroupItem(
                    dbc.Row(
                        [
                            dbc.Col(
                                [
                                    html.Span(f"{i + 1}. ", className="text-muted me-1"),
                                    html.Strong(scenario),
                                    html.Small(
                                        f"  ({trial_count} trial{'s' if trial_count != 1 else ''})",
                                        className="text-muted ms-1",
                                    ),
                                ],
                                className="d-flex align-items-center",
                            ),
                            dbc.Col(
                                dbc.ButtonGroup(
                                    [
                                        dbc.Button(
                                            "\u25b2",
                                            id={"type": "btn-scenario-up", "index": i},
                                            color="light",
                                            size="sm",
                                            disabled=(i == 0),
                                        ),
                                        dbc.Button(
                                            "\u25bc",
                                            id={"type": "btn-scenario-down", "index": i},
                                            color="light",
                                            size="sm",
                                            disabled=(i == len(scenarios) - 1),
                                        ),
                                    ],
                                    size="sm",
                                ),
                                width="auto",
                            ),
                        ],
                        align="center",
                        className="g-0",
                    ),
                    className="py-1 px-2",
                )
            )
        children.append(dbc.ListGroup(order_items, flush=True, className="mb-3"))

        children.append(html.Hr())

        # Trial details per scenario
        children.append(html.H6("Trial Details"))
        for scenario in scenarios:
            scenario_trials = [t for t in trials if t["scenario"] == scenario]
            children.append(
                html.Div(
                    [
                        html.Div(
                            [
                                dbc.Badge(scenario, color="info", className="me-1"),
                                html.Small(
                                    f"{len(scenario_trials)} trial(s)",
                                    className="text-muted",
                                ),
                            ],
                            className="mb-1",
                        ),
                        dbc.ListGroup(
                            [make_trial_list_item(t) for t in scenario_trials],
                            flush=True,
                        ),
                    ],
                    className="mb-2",
                )
            )
    else:
        children.append(
            html.P("No trials loaded. Scan a parent folder above.", className="text-muted small")
        )

    return children


def build_static_sidebar():
    """Build the sidebar layout once (static form + dynamic trial list placeholder)."""
    return dbc.Card(
        [
            dbc.CardHeader(html.H5("Data Management", className="mb-0")),
            dbc.CardBody(
                [
                    # --- Scan folder form ---
                    html.H6("Load Data"),
                    html.Small(
                        "Point to a parent folder containing scenario subdirectories, "
                        "each with trial folders inside.",
                        className="text-muted d-block mb-2",
                    ),
                    dbc.InputGroup(
                        [
                            dbc.Input(
                                id="input-parent-path",
                                placeholder="Path to parent folder...",
                                type="text",
                                size="sm",
                            ),
                            dbc.Button(
                                "Browse",
                                id="btn-open-browser",
                                color="secondary",
                                size="sm",
                                outline=True,
                            ),
                        ],
                        className="mb-2",
                        size="sm",
                    ),
                    dbc.Button(
                        "Scan & Load",
                        id="btn-scan-folder",
                        color="primary",
                        size="sm",
                        className="w-100 mb-2",
                    ),
                    html.Div(id="scan-feedback", className="mb-3"),
                    html.Hr(),
                    # --- Discovered trials (dynamic, updated by callback) ---
                    html.H6("Loaded Trials"),
                    html.Div(id="trial-list"),
                    html.Hr(),
                    dbc.Button(
                        "Clear All",
                        id="btn-clear-trials",
                        color="danger",
                        size="sm",
                        outline=True,
                        className="w-100",
                    ),
                ],
            ),
        ],
        className="mb-3",
    )


# ---------------------------------------------------------------------------
# Chart helpers
# ---------------------------------------------------------------------------

SCENARIO_COLORS = ["#3498db", "#e74c3c", "#2ecc71", "#f39c12", "#9b59b6", "#1abc9c", "#e67e22", "#8e44ad"]

# Common layout settings to prevent title/legend overlap
_CHART_LAYOUT = dict(
    height=370,
    margin=dict(l=50, r=20, t=60, b=40),
    legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
    title_x=0.5,
)


def _no_data_alert():
    return dbc.Alert("No trial data registered. Scan a folder in the sidebar.", color="warning")


def _get_scenario_stats(state):
    """Return (scenario_names, scenarios_data, scenario_stats) tuple."""
    scenarios_data = get_scenarios_data(state)
    scenario_names = get_ordered_scenarios(state)
    scenario_stats = {}
    for name in scenario_names:
        if name in scenarios_data:
            paths = scenarios_data[name]["trial_paths"]
            scenario_stats[name] = average_benchmark_stats_across_trials(paths)
    return scenario_names, scenarios_data, scenario_stats


def _metric_subtitle(metric_key):
    """Build a subtitle string showing the full metric name."""
    full_name = get_metric_full_name(metric_key)
    if full_name != metric_key:
        return f"<span style='font-size:10px;color:#888'>{full_name}</span>"
    return ""


def _build_avg_bar_fig(title, metric_key, scenario_names, scenario_stats, y_label="Value"):
    """Build a bar chart figure showing mean value per scenario. Returns go.Figure."""
    fig = go.Figure()
    values = [scenario_stats.get(sn, {}).get(metric_key, {}).get("mean", 0) for sn in scenario_names]
    bytes_mode = is_bytes_metric(metric_key) and any(abs(v) >= 1024 for v in values)
    text_labels = [format_bytes(v) for v in values] if bytes_mode else [f"{v:.4f}" for v in values]
    fig.add_trace(go.Bar(
        x=scenario_names, y=values,
        marker_color=SCENARIO_COLORS[:len(scenario_names)],
        text=text_labels,
        textposition="outside",
        hovertemplate=("%{x}<br>" + ("%{customdata}" if bytes_mode else "%{y:.4f}") + "<extra></extra>"),
        customdata=text_labels if bytes_mode else None,
    ))
    subtitle = _metric_subtitle(metric_key)
    fig.update_layout(
        title={"text": f"{title}<br>{subtitle}" if subtitle else title, "x": 0.5},
        xaxis_title="Scenario", yaxis_title=y_label,
        showlegend=False,
        height=_CHART_LAYOUT["height"],
        margin=dict(l=50, r=20, t=90 if subtitle else 60, b=40),
    )
    return fig


def _make_avg_bar(title, metric_key, scenario_names, scenario_stats, y_label="Value"):
    """Simple bar chart showing mean value per scenario."""
    return dcc.Graph(figure=_build_avg_bar_fig(title, metric_key, scenario_names, scenario_stats, y_label))


def _build_ts_line_fig(title, csv_metric, scenario_names, scenarios_data, y_label="Value"):
    """Build a time-series line chart figure. Returns go.Figure."""
    fig = go.Figure()
    bytes_mode = is_bytes_metric(csv_metric)
    any_large = False
    color_idx = 0
    for sname in scenario_names:
        if sname not in scenarios_data:
            continue
        trials = scenarios_data[sname]["trials"]
        color = SCENARIO_COLORS[color_idx % len(SCENARIO_COLORS)]
        multi_trial = len(trials) > 1
        for j, trial in enumerate(trials):
            df = load_csv_metric_per_trial(trial["path"], csv_metric)
            if df.empty:
                continue
            if bytes_mode and df["value"].abs().max() >= 1024:
                any_large = True
            label = f"{sname} / {trial['label']}" if multi_trial else sname
            hover_text = [format_bytes(v) for v in df["value"]] if bytes_mode else None
            fig.add_trace(go.Scatter(
                x=df["elapsed_seconds"], y=df["value"], mode="lines", name=label,
                line=dict(color=color, dash=["solid", "dash", "dot", "dashdot"][j % 4]),
                legendgroup=sname,
                customdata=hover_text,
                hovertemplate=("%{fullData.name}<br>%{x}s: %{customdata}<extra></extra>") if bytes_mode else None,
            ))
        color_idx += 1
    bytes_mode = bytes_mode and any_large
    subtitle = _metric_subtitle(csv_metric)
    layout = dict(**_CHART_LAYOUT)
    if subtitle:
        layout["title"] = {"text": f"{title}<br>{subtitle}", "x": 0.5}
        layout["margin"] = dict(l=50, r=20, t=90, b=40)
    else:
        layout["title"] = title
    fig.update_layout(xaxis_title="Elapsed (s)", yaxis_title=y_label, **layout)
    return fig


def _make_ts_line(title, csv_metric, scenario_names, scenarios_data, y_label="Value"):
    """Time-series line chart showing each trial as a separate line."""
    return dcc.Graph(figure=_build_ts_line_fig(title, csv_metric, scenario_names, scenarios_data, y_label))


def _format_table_value(val, bytes_mode):
    """Format a value for table display."""
    if val is None:
        return ""
    if bytes_mode and abs(val) >= 1024:
        return format_bytes(val)
    return f"{val:.6f}"


def _make_comparison_table(metric_keys, scenario_names, scenario_stats, table_id):
    """AG Grid table: metrics as rows, one mean column per scenario.

    Hovering on any cell in a row shows the full dotted metric name as a tooltip.
    Byte-valued metrics are shown in human-readable format.
    """
    row_data = []
    for key in metric_keys:
        pretty = prettify_metric_name(key.replace(".", "_"))
        full_name = get_metric_full_name(key)
        vals = {}
        for sname in scenario_names:
            vals[sname] = scenario_stats.get(sname, {}).get(key, {}).get("mean", None)
        bytes_mode = is_bytes_metric(key) and any(
            v is not None and abs(v) >= 1024 for v in vals.values()
        )
        row = {"metric": pretty, "metric_key": full_name}
        for sname in scenario_names:
            row[sname] = _format_table_value(vals[sname], bytes_mode)
        row_data.append(row)

    col_defs = [
        {"field": "metric", "headerName": "Metric", "pinned": "left", "width": 280,
         "filter": True, "sortable": True,
         "tooltipField": "metric_key"},
        {"field": "metric_key", "hide": True},
    ] + [
        {"field": sname, "headerName": sname, "width": 150,
         "filter": True, "sortable": True,
         "tooltipField": "metric_key"}
        for sname in scenario_names
    ]

    return dag.AgGrid(
        id=table_id,
        rowData=row_data,
        columnDefs=col_defs,
        defaultColDef={"resizable": True, "sortable": True, "filter": True},
        dashGridOptions={
            "domLayout": "autoHeight",
            "pagination": True,
            "paginationPageSize": 25,
            "tooltipShowDelay": 300,
        },
        style={"width": "100%"},
        className="ag-theme-alpine",
    )


# ---------------------------------------------------------------------------
# Fixed dashboard builders
# ---------------------------------------------------------------------------

def build_overview_dashboard(state):
    """Overview: full metrics comparison table (mean only) across all scenarios."""
    scenarios_data = get_scenarios_data(state)
    if not scenarios_data:
        return _no_data_alert()

    scenario_names, _, scenario_stats = _get_scenario_stats(state)

    all_keys = set()
    for stats in scenario_stats.values():
        all_keys.update(stats.keys())
    all_keys = sorted(all_keys)

    row_data = []
    for key in all_keys:
        full_name = get_metric_full_name(key)
        vals = {}
        for sname in scenario_names:
            vals[sname] = scenario_stats.get(sname, {}).get(key, {}).get("mean", None)
        bytes_mode = is_bytes_metric(key) and any(
            v is not None and abs(v) >= 1024 for v in vals.values()
        )
        row = {
            "metric_pretty": prettify_metric_name(key.replace(".", "_")),
            "metric_key": full_name,
        }
        for sname in scenario_names:
            row[sname] = _format_table_value(vals[sname], bytes_mode)
        row_data.append(row)

    col_defs = [
        {"field": "metric_pretty", "headerName": "Metric", "pinned": "left", "width": 300,
         "filter": True, "sortable": True,
         "tooltipField": "metric_key"},
        {"field": "metric_key", "hide": True},
    ] + [
        {"field": sname, "headerName": sname, "width": 150,
         "filter": True, "sortable": True,
         "tooltipField": "metric_key"}
        for sname in scenario_names
    ]

    search_input = dbc.Input(
        id="overview-search",
        placeholder="Search metrics...",
        type="text",
        debounce=True,
        className="mb-3",
        style={"maxWidth": "400px"},
    )

    overview_table = dag.AgGrid(
        id="overview-table",
        rowData=row_data,
        columnDefs=col_defs,
        defaultColDef={"resizable": True, "sortable": True, "filter": True},
        dashGridOptions={
            "domLayout": "autoHeight",
            "pagination": True,
            "paginationPageSize": 25,
            "tooltipShowDelay": 300,
        },
        style={"width": "100%"},
        className="ag-theme-alpine",
    )

    export_btn = dbc.Button(
        "Export Report", id="btn-export-open", color="primary", size="sm", className="ms-3",
    )
    export_zip_btn = dbc.Button(
        "Export Artifacts (ZIP)", id="btn-export-zip", color="secondary", size="sm", className="ms-2",
    )

    return html.Div(
        [
            html.Div(
                [
                    html.H4("Overview - All Metrics Comparison", className="mb-3 d-inline"),
                    export_btn,
                    export_zip_btn,
                ],
                className="d-flex align-items-center mb-3",
            ),
            html.P("Mean values across scenarios (averaged across trials per scenario)."),
            search_input,
            overview_table,
        ]
    )


def build_ambient_dashboard(state):
    """Ambient platform metrics: component resource usage, sessions, latency."""
    scenarios_data = get_scenarios_data(state)
    if not scenarios_data:
        return _no_data_alert()

    scenario_names, scenarios_data, scenario_stats = _get_scenario_stats(state)

    sections = []

    # --- Section 1: Namespace totals ---
    sections.append(html.H5("Namespace Resource Totals", className="mt-3 mb-2"))
    sections.append(dbc.Row([
        dbc.Col(_make_avg_bar(
            "Namespace CPU Total", "ambient.namespace.cpu_total",
            scenario_names, scenario_stats, "CPU (cores)"), md=6),
        dbc.Col(_make_avg_bar(
            "Namespace Memory Total", "ambient.namespace.memory_total",
            scenario_names, scenario_stats, "Memory (bytes)"), md=6),
    ]))

    # --- Section 2: Per-component CPU & Memory ---
    components = [
        ("agentic-operator", "Agentic Operator"),
        ("ambient-api-server", "API Server"),
        ("backend-api", "Backend API"),
        ("frontend", "Frontend"),
        ("postgresql", "PostgreSQL"),
        ("minio", "MinIO"),
    ]

    sections.append(html.H5("Component Resource Usage", className="mt-4 mb-2"))
    sections.append(html.P("Mean values across test duration, per scenario.", className="text-muted"))

    component_charts = []
    for comp_key, comp_name in components:
        cpu_key = f"ambient.{comp_key}.cpu"
        mem_key = f"ambient.{comp_key}.memory"
        has_data = any(cpu_key in scenario_stats.get(s, {}) for s in scenario_names)
        if has_data:
            component_charts.append(dbc.Col(
                _make_avg_bar(f"{comp_name} CPU", cpu_key, scenario_names, scenario_stats, "CPU (cores)"), md=6))
            component_charts.append(dbc.Col(
                _make_avg_bar(f"{comp_name} Memory", mem_key, scenario_names, scenario_stats, "Memory (bytes)"), md=6))
    sections.append(dbc.Row(component_charts))

    # --- Section 3: Session Startup & Image Pull Latency ---
    sections.append(html.H5("Session Startup Latency", className="mt-4 mb-2"))
    sections.append(dbc.Row([
        dbc.Col(_make_avg_bar(
            "Session Startup Duration (mean)", "ambient.session_startup.duration_avg",
            scenario_names, scenario_stats, "Duration (s)"), md=6),
        dbc.Col(_make_avg_bar(
            "Image Pull Duration (mean)", "ambient.image_pull.duration_avg",
            scenario_names, scenario_stats, "Duration (s)"), md=6),
    ]))

    # Time-series for startup latency (per-trial lines)
    sections.append(dbc.Row([
        dbc.Col(_make_ts_line(
            "Session Startup Avg (over time)", "measurements_ambient_session_startup_duration_avg",
            scenario_names, scenarios_data, "Duration (s)"), md=6),
        dbc.Col(_make_ts_line(
            "Session Startup P99 (over time)", "measurements_ambient_session_startup_duration_p99",
            scenario_names, scenarios_data, "Duration (s)"), md=6),
    ]))

    # --- Section 4: Reconciliation ---
    sections.append(html.H5("Operator Reconciliation", className="mt-4 mb-2"))
    sections.append(dbc.Row([
        dbc.Col(_make_avg_bar(
            "Reconcile Duration (mean)", "ambient.reconcile.duration_avg",
            scenario_names, scenario_stats, "Duration (s)"), md=6),
        dbc.Col(_make_ts_line(
            "Reconcile Rate (over time)", "measurements_ambient_reconcile_rate",
            scenario_names, scenarios_data, "ops/s"), md=6),
    ]))

    # --- Section 5: Sessions state ---
    sections.append(html.H5("Session Counts", className="mt-4 mb-2"))
    sections.append(dbc.Row([
        dbc.Col(_make_ts_line(
            "Active Sessions", "measurements_ambient_sessions_active",
            scenario_names, scenarios_data, "Count"), md=6),
        dbc.Col(_make_ts_line(
            "Pending Sessions", "measurements_ambient_sessions_pending",
            scenario_names, scenarios_data, "Count"), md=6),
    ]))

    # --- Section 6: Component resource table ---
    comp_metric_keys = []
    for comp_key, _ in components:
        for suffix in ["cpu", "memory", "restarts"]:
            k = f"ambient.{comp_key}.{suffix}"
            if any(k in scenario_stats.get(s, {}) for s in scenario_names):
                comp_metric_keys.append(k)

    sections.append(html.H5("Component Resource Table", className="mt-4 mb-2"))
    sections.append(_make_comparison_table(
        comp_metric_keys, scenario_names, scenario_stats, "ambient-resource-table",
    ))

    return html.Div(sections)


def build_cluster_dashboard(state):
    """Cluster-level metrics: CPU, memory, network, API server, etcd."""
    scenarios_data = get_scenarios_data(state)
    if not scenarios_data:
        return _no_data_alert()

    scenario_names, scenarios_data, scenario_stats = _get_scenario_stats(state)

    sections = [html.H4("Cluster Metrics", className="mb-3")]

    # --- Cluster resource usage (avg bars) ---
    sections.append(html.H5("Cluster Resource Usage", className="mt-2 mb-2"))
    sections.append(dbc.Row([
        dbc.Col(_make_avg_bar(
            "Cluster CPU Usage Rate", "cluster.cpu_usage_rate",
            scenario_names, scenario_stats, "CPU (cores)"), md=6),
        dbc.Col(_make_avg_bar(
            "Cluster Memory RSS", "cluster.memory_rss_total",
            scenario_names, scenario_stats, "Memory (bytes)"), md=6),
    ]))
    sections.append(dbc.Row([
        dbc.Col(_make_avg_bar(
            "Network Throughput", "cluster.network_bytes_total",
            scenario_names, scenario_stats, "Bytes"), md=6),
        dbc.Col(_make_avg_bar(
            "Disk Throughput", "cluster.disk_throughput_total",
            scenario_names, scenario_stats, "Bytes"), md=6),
    ]))

    # --- Time-series (per-trial lines) ---
    sections.append(html.H5("Cluster Resources Over Time", className="mt-4 mb-2"))
    sections.append(dbc.Row([
        dbc.Col(_make_ts_line(
            "CPU Usage Rate", "measurements_cluster_cpu_usage_rate",
            scenario_names, scenarios_data, "CPU (cores)"), md=6),
        dbc.Col(_make_ts_line(
            "Memory RSS Total", "measurements_cluster_memory_rss_total",
            scenario_names, scenarios_data, "Bytes"), md=6),
    ]))
    sections.append(dbc.Row([
        dbc.Col(_make_ts_line(
            "Worker Avg CPU %", "measurements_cluster_workers_avg_cpu_pct",
            scenario_names, scenarios_data, "%"), md=6),
        dbc.Col(_make_ts_line(
            "Running Pods on Workers", "measurements_cluster_running_pods_on_workers",
            scenario_names, scenarios_data, "Count"), md=6),
    ]))

    # --- API Server & etcd ---
    sections.append(html.H5("API Server & etcd", className="mt-4 mb-2"))
    sections.append(dbc.Row([
        dbc.Col(_make_ts_line(
            "API Server Request Rate", "measurements_apiserver_request_total_rate",
            scenario_names, scenarios_data, "req/s"), md=6),
        dbc.Col(_make_ts_line(
            "etcd Request Duration Avg", "measurements_etcd_request_duration_avg",
            scenario_names, scenarios_data, "Duration (s)"), md=6),
    ]))

    # --- Pod & Node counts ---
    sections.append(html.H5("Pod & Node Counts", className="mt-4 mb-2"))
    sections.append(dbc.Row([
        dbc.Col(_make_avg_bar(
            "Avg Worker Node Count", "cluster.worker_node_count",
            scenario_names, scenario_stats, "Count"), md=6),
        dbc.Col(_make_avg_bar(
            "Avg Pod Count", "cluster.pod_count",
            scenario_names, scenario_stats, "Count"), md=6),
    ]))
    sections.append(dbc.Row([
        dbc.Col(_make_ts_line(
            "Cluster Pod Count", "measurements_cluster_pod_count",
            scenario_names, scenarios_data, "Count"), md=6),
        dbc.Col(_make_ts_line(
            "Worker Node Count", "measurements_cluster_worker_node_count",
            scenario_names, scenarios_data, "Count"), md=6),
    ]))

    # --- Summary table ---
    cluster_keys = [k for k in sorted(scenario_stats.get(scenario_names[0], {}).keys())
                    if k.startswith(("cluster.", "apiserver.", "etcd."))]
    sections.append(html.H5("Cluster Metrics Table", className="mt-4 mb-2"))
    sections.append(_make_comparison_table(
        cluster_keys, scenario_names, scenario_stats, "cluster-metrics-table",
    ))

    return html.Div(sections)


def build_locust_dashboard(state):
    """Locust load test results: response times, throughput, errors, resource usage."""
    scenarios_data = get_scenarios_data(state)
    if not scenarios_data:
        return _no_data_alert()

    scenario_names, scenarios_data, scenario_stats = _get_scenario_stats(state)

    sections = [html.H4("Locust Load Test Results", className="mb-3")]

    # --- Response time & throughput ---
    sections.append(html.H5("Response Time & Throughput", className="mt-2 mb-2"))
    sections.append(dbc.Row([
        dbc.Col(_make_ts_line(
            "Avg Response Time", "results_locust_requests_avg_response_time_total",
            scenario_names, scenarios_data, "ms"), md=6),
        dbc.Col(_make_ts_line(
            "Requests Per Second", "results_locust_requests_current_rps_total",
            scenario_names, scenarios_data, "req/s"), md=6),
    ]))

    # --- Errors ---
    sections.append(html.H5("Errors & Failures", className="mt-4 mb-2"))
    sections.append(dbc.Row([
        dbc.Col(_make_ts_line(
            "Fail Ratio", "results_locust_requests_fail_ratio",
            scenario_names, scenarios_data, "Ratio"), md=6),
        dbc.Col(_make_ts_line(
            "Failures Per Second", "results_locust_requests_current_fail_per_sec_total",
            scenario_names, scenarios_data, "fails/s"), md=6),
    ]))

    # --- Cumulative counts ---
    sections.append(html.H5("Cumulative Counts", className="mt-4 mb-2"))
    sections.append(dbc.Row([
        dbc.Col(_make_ts_line(
            "Total Requests", "results_locust_requests_num_requests_total",
            scenario_names, scenarios_data, "Count"), md=6),
        dbc.Col(_make_ts_line(
            "Total Failures", "results_locust_requests_num_failures_total",
            scenario_names, scenarios_data, "Count"), md=6),
    ]))

    # --- Endpoint-specific ---
    sections.append(html.H5("Endpoint: GET /agentic-sessions/list", className="mt-4 mb-2"))
    sections.append(dbc.Row([
        dbc.Col(_make_ts_line(
            "Endpoint Avg Response Time",
            "results_GET_agentic-sessions_list_locust_requests_avg_response_time",
            scenario_names, scenarios_data, "ms"), md=6),
        dbc.Col(_make_ts_line(
            "Endpoint RPS",
            "results_GET_agentic-sessions_list_locust_requests_current_rps",
            scenario_names, scenarios_data, "req/s"), md=6),
    ]))

    # --- Locust worker resources ---
    sections.append(html.H5("Locust Worker Resources", className="mt-4 mb-2"))
    sections.append(dbc.Row([
        dbc.Col(_make_avg_bar(
            "Workers CPU", "locust.workers.cpu",
            scenario_names, scenario_stats, "CPU (cores)"), md=6),
        dbc.Col(_make_avg_bar(
            "Workers Memory", "locust.workers.memory",
            scenario_names, scenario_stats, "Memory (bytes)"), md=6),
    ]))

    # --- Users ramp-up ---
    sections.append(dbc.Row([
        dbc.Col(_make_ts_line(
            "Active Users", "results_locust_users",
            scenario_names, scenarios_data, "Users"), md=6),
        dbc.Col(_make_ts_line(
            "Avg Content Length", "results_locust_requests_avg_content_length_total",
            scenario_names, scenarios_data, "Bytes"), md=6),
    ], className="mt-2"))

    return html.Div(sections)


# ---------------------------------------------------------------------------
# Custom dashboard panel builder
# ---------------------------------------------------------------------------

def build_custom_dashboard_tab(state):
    """Build the custom dashboard tab with panel creation controls."""
    scenarios_data = get_scenarios_data(state)
    scenario_options = [{"label": s, "value": s} for s in get_ordered_scenarios(state)]

    csv_metrics = get_all_available_metrics(state)
    benchmark_keys = get_all_benchmark_metric_keys(state)

    csv_categories = get_metric_categories(csv_metrics)
    csv_options = []
    for cat in sorted(csv_categories.keys()):
        for m in csv_categories[cat]:
            csv_options.append({"label": f"[{cat}] {prettify_metric_name(m)}", "value": m})

    benchmark_options = [{"label": k, "value": k} for k in benchmark_keys]

    saved_dashboards = state.get("custom_dashboards", [])
    saved_options = [{"label": d["name"], "value": i} for i, d in enumerate(saved_dashboards)]

    return html.Div(
        [
            html.H4("Custom Dashboard", className="mb-3"),
            dbc.Row(
                [
                    # Load saved dashboard
                    dbc.Col(
                        [
                            dbc.InputGroup(
                                [
                                    dbc.Select(
                                        id="select-saved-dashboard",
                                        options=saved_options,
                                        placeholder="Load saved dashboard...",
                                    ),
                                    dbc.Button("Load", id="btn-load-dashboard", color="secondary", size="sm"),
                                ],
                                size="sm",
                            ),
                        ],
                        md=4,
                    ),
                    # Save current dashboard
                    dbc.Col(
                        [
                            dbc.InputGroup(
                                [
                                    dbc.Input(id="input-dashboard-name", placeholder="Dashboard name...", size="sm"),
                                    dbc.Button("Save", id="btn-save-dashboard", color="success", size="sm"),
                                ],
                                size="sm",
                            ),
                        ],
                        md=4,
                    ),
                    dbc.Col(
                        dbc.Button(
                            "Delete Saved Dashboard",
                            id="btn-delete-dashboard",
                            color="danger",
                            size="sm",
                            outline=True,
                        ),
                        md=2,
                    ),
                ],
                className="mb-3",
            ),
            html.Div(id="dashboard-save-feedback"),
            html.Hr(),
            # --- Add panel controls ---
            html.H5("Add Panel"),
            dbc.Row(
                [
                    dbc.Col(
                        [
                            dbc.Label("Data Source", size="sm"),
                            dbc.Select(
                                id="select-data-source",
                                options=[
                                    {"label": "Time-Series (CSV)", "value": "csv"},
                                    {"label": "Aggregated Stats (Benchmark)", "value": "benchmark"},
                                ],
                                value="csv",
                            ),
                        ],
                        md=2,
                    ),
                    dbc.Col(
                        [
                            dbc.Label("Chart Type", size="sm"),
                            dbc.Select(
                                id="select-chart-type",
                                options=[
                                    {"label": "Line Plot", "value": "line"},
                                    {"label": "Bar Chart", "value": "bar"},
                                    {"label": "Grouped Bar", "value": "grouped_bar"},
                                    {"label": "Table", "value": "table"},
                                ],
                                value="line",
                            ),
                        ],
                        md=2,
                    ),
                    dbc.Col(
                        [
                            dbc.Label("Metric(s)", size="sm"),
                            dcc.Dropdown(
                                id="select-metrics",
                                options=csv_options,
                                multi=True,
                                placeholder="Select metric(s)...",
                            ),
                        ],
                        md=3,
                    ),
                    dbc.Col(
                        [
                            dbc.Label("Scenarios", size="sm"),
                            dcc.Dropdown(
                                id="select-scenarios",
                                options=scenario_options,
                                multi=True,
                                value=[s["value"] for s in scenario_options],
                                placeholder="Select scenarios...",
                            ),
                        ],
                        md=2,
                    ),
                    dbc.Col(
                        [
                            dbc.Label("Stat Field", size="sm"),
                            dbc.Select(
                                id="select-stat-field",
                                options=STAT_OPTIONS,
                                value="mean",
                            ),
                        ],
                        md=2,
                    ),
                ],
                className="mb-3",
            ),
            dbc.Row(
                dbc.Col(
                    dbc.Button(
                        "+ Add Panel",
                        id="btn-add-panel",
                        color="primary",
                        className="me-2",
                    ),
                ),
            ),
            html.Hr(),
            # --- Dynamic panels container ---
            html.Div(id="custom-panels-container", children=[]),
            # Hidden stores
            dcc.Store(id="store-panels", data=[]),
            dcc.Store(id="store-benchmark-options", data=benchmark_options),
            dcc.Store(id="store-csv-options", data=csv_options),
        ]
    )


# ---------------------------------------------------------------------------
# Main layout
# ---------------------------------------------------------------------------

def build_folder_browser_modal():
    """Build the folder browser modal with navigation controls."""
    return dbc.Modal(
        [
            dbc.ModalHeader(dbc.ModalTitle("Browse for Trial Folder")),
            dbc.ModalBody(
                [
                    # Current path display + up button
                    dbc.InputGroup(
                        [
                            dbc.Button(
                                "\u2191 Up",
                                id="btn-browse-up",
                                color="light",
                                size="sm",
                            ),
                            dbc.Input(
                                id="input-browse-path",
                                value=_DEFAULT_BROWSE_ROOT,
                                size="sm",
                                debounce=True,
                            ),
                            dbc.Button(
                                "Go",
                                id="btn-browse-go",
                                color="primary",
                                size="sm",
                            ),
                        ],
                        className="mb-3",
                        size="sm",
                    ),
                    # Validation indicator for current folder
                    html.Div(id="browse-validation", className="mb-2"),
                    # Folder listing
                    html.Div(
                        id="browse-folder-list",
                        style={
                            "maxHeight": "400px",
                            "overflowY": "auto",
                            "border": "1px solid #dee2e6",
                            "borderRadius": "4px",
                        },
                    ),
                ],
            ),
            dbc.ModalFooter(
                [
                    dbc.Button(
                        "Select This Folder",
                        id="btn-browse-select",
                        color="primary",
                    ),
                    dbc.Button(
                        "Cancel",
                        id="btn-browse-cancel",
                        color="secondary",
                        outline=True,
                    ),
                ]
            ),
        ],
        id="modal-folder-browser",
        size="lg",
        is_open=False,
    )


def build_export_modal():
    """Modal for configuring report export."""
    return dbc.Modal(
        [
            dbc.ModalHeader(dbc.ModalTitle("Export Report")),
            dbc.ModalBody([
                dbc.Label("Report Title"),
                dbc.Input(
                    id="export-title",
                    value="Performance Test Report",
                    className="mb-3",
                ),
                dbc.Label("Description (Markdown)"),
                dbc.Textarea(
                    id="export-description",
                    placeholder="## Summary\n\nBrief description of the test...\n\n- Key finding 1\n- Key finding 2",
                    style={"height": "200px", "fontFamily": "monospace"},
                    className="mb-3",
                ),
                dbc.Label("Format"),
                dbc.Select(
                    id="export-format",
                    options=[
                        {"label": "Word Document (.docx)", "value": "docx"},
                        {"label": "PDF (.pdf)", "value": "pdf"},
                    ],
                    value="docx",
                    className="mb-3",
                ),
            ]),
            dbc.ModalFooter([
                dbc.Button("Cancel", id="btn-export-cancel", color="secondary", className="me-2"),
                dbc.Button("Export", id="btn-export-run", color="primary"),
            ]),
        ],
        id="export-modal",
        is_open=False,
        size="lg",
    )


app.layout = dbc.Container(
    [
        dcc.Store(id="store-state", data={"trials": [], "custom_dashboards": [], "scenario_order": []}),
        dcc.Store(id="store-browse-path", data=_DEFAULT_BROWSE_ROOT),
        dcc.Download(id="download-report"),
        dcc.Download(id="download-zip"),
        build_folder_browser_modal(),
        build_export_modal(),
        dbc.Row(
            [
                dbc.Col(
                    build_static_sidebar(),
                    md=3,
                    className="pe-0",
                ),
                dbc.Col(
                    [
                        html.H2("Performance Reporter", className="mb-3 mt-3"),
                        dbc.Tabs(
                            [
                                dbc.Tab(label="Overview", tab_id="tab-overview"),
                                dbc.Tab(label="Ambient", tab_id="tab-ambient"),
                                dbc.Tab(label="Cluster", tab_id="tab-cluster"),
                                dbc.Tab(label="Locust", tab_id="tab-locust"),
                                dbc.Tab(label="Custom", tab_id="tab-custom"),
                            ],
                            id="main-tabs",
                            active_tab="tab-overview",
                        ),
                        html.Div(id="tab-content", className="mt-3 pb-5"),
                    ],
                    md=9,
                ),
            ],
            className="mt-2",
        ),
    ],
    fluid=True,
)


# ---------------------------------------------------------------------------
# Callbacks — folder browser
# ---------------------------------------------------------------------------

def _list_subdirs(path):
    """List immediate subdirectories of a path, sorted alphabetically."""
    try:
        entries = []
        for entry in sorted(os.scandir(path), key=lambda e: e.name.lower()):
            if entry.is_dir() and not entry.name.startswith("."):
                entries.append(entry)
        return entries
    except PermissionError:
        return []


@app.callback(
    Output("modal-folder-browser", "is_open"),
    Output("input-browse-path", "value"),
    Input("btn-open-browser", "n_clicks"),
    Input("btn-browse-cancel", "n_clicks"),
    Input("btn-browse-select", "n_clicks"),
    State("modal-folder-browser", "is_open"),
    State("store-browse-path", "data"),
    prevent_initial_call=True,
)
def toggle_browser_modal(open_clicks, cancel_clicks, select_clicks, is_open, browse_path):
    trigger = callback_context.triggered_id
    if trigger == "btn-open-browser":
        return True, browse_path or _DEFAULT_BROWSE_ROOT
    return False, dash.no_update


@app.callback(
    Output("store-browse-path", "data"),
    Output("browse-folder-list", "children"),
    Output("browse-validation", "children"),
    Input("input-browse-path", "value"),
    Input("btn-browse-go", "n_clicks"),
    Input("btn-browse-up", "n_clicks"),
    Input({"type": "btn-browse-folder", "index": ALL}, "n_clicks"),
    State("store-browse-path", "data"),
    prevent_initial_call=True,
)
def navigate_browser(typed_path, go_clicks, up_clicks, folder_clicks, current_path):
    trigger = callback_context.triggered_id

    # Determine target path
    if trigger == "btn-browse-up":
        target = os.path.dirname(current_path or "/")
    elif trigger == "btn-browse-go" or trigger == "input-browse-path":
        target = typed_path or current_path
    elif isinstance(trigger, dict) and trigger.get("type") == "btn-browse-folder":
        # Clicked a subfolder
        if folder_clicks and any(folder_clicks):
            idx = trigger["index"]
            subdirs = _list_subdirs(current_path)
            if 0 <= idx < len(subdirs):
                target = subdirs[idx].path
            else:
                target = current_path
        else:
            return dash.no_update, dash.no_update, dash.no_update
    else:
        target = current_path

    target = os.path.abspath(target)

    if not os.path.isdir(target):
        return (
            current_path,
            html.P("Directory not found.", className="text-danger p-3"),
            "",
        )

    # Check if current folder looks like a valid parent (has scenario/trial structure)
    discovered, disc_msg = scan_parent_folder(target)
    validation_badge = ""
    if discovered:
        validation_badge = dbc.Alert(
            disc_msg,
            color="success",
            className="py-1 px-2 mb-0",
        )
    else:
        # Maybe it's a single scenario dir or trial dir
        valid, msg = validate_trial_path(target)
        if valid:
            validation_badge = dbc.Alert(
                f"This is a trial folder (not a parent). Navigate up one or two levels.",
                color="info",
                className="py-1 px-2 mb-0",
            )

    # Build folder listing
    subdirs = _list_subdirs(target)
    if not subdirs:
        folder_list = html.P(
            "No subdirectories found.",
            className="text-muted p-3",
        )
    else:
        items = []
        for i, entry in enumerate(subdirs):
            # Check what kind of folder this is
            sub_valid, _ = validate_trial_path(entry.path)
            sub_discovered, _ = scan_parent_folder(entry.path)
            badge = ""
            if sub_discovered:
                n = len(sub_discovered)
                badge = dbc.Badge(f"{n} trial(s)", color="info", className="ms-2")
            elif sub_valid:
                badge = dbc.Badge("trial", color="success", className="ms-2")

            items.append(
                dbc.ListGroupItem(
                    [
                        html.Span("\U0001f4c1 ", style={"fontSize": "1.1em"}),
                        html.Span(entry.name),
                        badge,
                    ],
                    id={"type": "btn-browse-folder", "index": i},
                    action=True,
                    className="py-2",
                    style={"cursor": "pointer"},
                )
            )
        folder_list = dbc.ListGroup(items, flush=True)

    return target, folder_list, validation_badge


@app.callback(
    Output("input-parent-path", "value", allow_duplicate=True),
    Output("modal-folder-browser", "is_open", allow_duplicate=True),
    Input("btn-browse-select", "n_clicks"),
    State("store-browse-path", "data"),
    prevent_initial_call=True,
)
def select_folder(n_clicks, browse_path):
    if not browse_path:
        return dash.no_update, dash.no_update
    return browse_path, False


# ---------------------------------------------------------------------------
# Callbacks — sidebar & tabs
# ---------------------------------------------------------------------------

@app.callback(
    Output("trial-list", "children"),
    Input("store-state", "data"),
)
def render_trial_list(state):
    return build_trial_list(state)


@app.callback(
    Output("store-state", "data", allow_duplicate=True),
    Input({"type": "btn-scenario-up", "index": ALL}, "n_clicks"),
    Input({"type": "btn-scenario-down", "index": ALL}, "n_clicks"),
    State("store-state", "data"),
    prevent_initial_call=True,
)
def reorder_scenarios(up_clicks, down_clicks, state):
    trigger = callback_context.triggered_id
    if not trigger or not isinstance(trigger, dict):
        return dash.no_update

    idx = trigger["index"]
    scenarios = get_ordered_scenarios(state)

    if trigger["type"] == "btn-scenario-up" and up_clicks and any(up_clicks):
        if 0 < idx < len(scenarios):
            scenarios[idx], scenarios[idx - 1] = scenarios[idx - 1], scenarios[idx]
    elif trigger["type"] == "btn-scenario-down" and down_clicks and any(down_clicks):
        if 0 <= idx < len(scenarios) - 1:
            scenarios[idx], scenarios[idx + 1] = scenarios[idx + 1], scenarios[idx]
    else:
        return dash.no_update

    state["scenario_order"] = scenarios
    return state


@app.callback(
    Output("store-state", "data", allow_duplicate=True),
    Output("scan-feedback", "children"),
    Input("btn-scan-folder", "n_clicks"),
    State("input-parent-path", "value"),
    State("store-state", "data"),
    prevent_initial_call=True,
)
def scan_folder(n_clicks, parent_path, state):
    if not parent_path:
        return dash.no_update, dbc.Alert(
            "Enter or browse to a parent folder first.", color="warning", duration=3000,
        )

    discovered, msg = scan_parent_folder(parent_path)
    if not discovered:
        return dash.no_update, dbc.Alert(msg, color="danger", duration=4000)

    # Replace all trials with the discovered ones, adding IDs and metadata
    existing_paths = {t["path"] for t in state.get("trials", [])}
    new_trials = []
    added = 0
    for trial_info in discovered:
        trial = {
            "id": str(uuid.uuid4()),
            "path": trial_info["path"],
            "label": trial_info["label"],
            "scenario": trial_info["scenario"],
        }
        try:
            meta = load_test_metadata(trial_info["path"])
            trial["metadata"] = {
                "run_id": meta.get("run_id_short", ""),
                "users": meta.get("users", ""),
                "run_time": meta.get("run_time", ""),
                "sessions_to_create": meta.get("env", {}).get("SESSIONS_TO_CREATE", ""),
            }
        except Exception:
            trial["metadata"] = {}
        new_trials.append(trial)
        if trial_info["path"] not in existing_paths:
            added += 1

    state["trials"] = new_trials
    # Set default scenario order using natural sort
    scenarios = sorted(set(t["scenario"] for t in new_trials), key=_natural_sort_key)
    state["scenario_order"] = scenarios

    return state, dbc.Alert(
        f"Loaded {len(new_trials)} trial(s) across {len(scenarios)} scenario(s): "
        f"{', '.join(scenarios)}",
        color="success",
        duration=5000,
    )


@app.callback(
    Output("store-state", "data", allow_duplicate=True),
    Input("btn-clear-trials", "n_clicks"),
    State("store-state", "data"),
    prevent_initial_call=True,
)
def clear_trials(n_clicks, state):
    state["trials"] = []
    state["scenario_order"] = []
    return state


@app.callback(
    Output("tab-content", "children"),
    Input("main-tabs", "active_tab"),
    Input("store-state", "data"),
)
def render_tab(active_tab, state):
    if active_tab == "tab-overview":
        return build_overview_dashboard(state)
    elif active_tab == "tab-ambient":
        return build_ambient_dashboard(state)
    elif active_tab == "tab-cluster":
        return build_cluster_dashboard(state)
    elif active_tab == "tab-locust":
        return build_locust_dashboard(state)
    elif active_tab == "tab-custom":
        return build_custom_dashboard_tab(state)
    return html.P("Select a tab.")


@app.callback(
    Output("overview-table", "dashGridOptions"),
    Input("overview-search", "value"),
    prevent_initial_call=True,
)
def filter_overview_table(search_value):
    return {
        "domLayout": "autoHeight",
        "pagination": True,
        "paginationPageSize": 25,
        "tooltipShowDelay": 300,
        "quickFilterText": search_value or "",
    }


# ---------------------------------------------------------------------------
# Callbacks — export report
# ---------------------------------------------------------------------------

@app.callback(
    Output("export-modal", "is_open"),
    Input("btn-export-open", "n_clicks"),
    Input("btn-export-cancel", "n_clicks"),
    Input("btn-export-run", "n_clicks"),
    State("export-modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_export_modal(open_clicks, cancel_clicks, run_clicks, is_open):
    if not callback_context.triggered_id:
        return dash.no_update
    trigger = callback_context.triggered_id
    if trigger == "btn-export-open" and open_clicks:
        return True
    if trigger in ("btn-export-cancel", "btn-export-run") and (cancel_clicks or run_clicks):
        return False
    return dash.no_update


@app.callback(
    Output("download-report", "data"),
    Input("btn-export-run", "n_clicks"),
    State("export-title", "value"),
    State("export-description", "value"),
    State("export-format", "value"),
    State("store-state", "data"),
    prevent_initial_call=True,
)
def export_report(n_clicks, title, description, fmt, state):
    if not n_clicks:
        return dash.no_update
    from exporter import export_docx, export_pdf

    scenario_names, scenarios_data, scenario_stats = _get_scenario_stats(state)

    all_keys = set()
    for stats in scenario_stats.values():
        all_keys.update(stats.keys())
    all_keys = sorted(all_keys)

    row_data = []
    for key in all_keys:
        full_name = get_metric_full_name(key)
        vals = {}
        for sname in scenario_names:
            vals[sname] = scenario_stats.get(sname, {}).get(key, {}).get("mean", None)
        bytes_mode = is_bytes_metric(key) and any(
            v is not None and abs(v) >= 1024 for v in vals.values()
        )
        row = {"metric_pretty": prettify_metric_name(key.replace(".", "_")), "metric_key": full_name}
        for sname in scenario_names:
            row[sname] = _format_table_value(vals[sname], bytes_mode)
        row_data.append(row)

    # Build chart figures and convert to PNG bytes
    chart_sections = _build_export_charts(scenario_names, scenarios_data, scenario_stats)

    title = title or "Performance Test Report"
    description = description or ""

    if fmt == "pdf":
        content = export_pdf(title, description, row_data, scenario_names, chart_sections)
        filename = "performance_report.pdf"
    else:
        content = export_docx(title, description, row_data, scenario_names, chart_sections)
        filename = "performance_report.docx"

    return dcc.send_bytes(content, filename)


@app.callback(
    Output("download-zip", "data"),
    Input("btn-export-zip", "n_clicks"),
    State("store-state", "data"),
    prevent_initial_call=True,
)
def export_zip_artifacts(n_clicks, state):
    if not n_clicks:
        return dash.no_update
    from exporter import export_zip

    scenario_names, scenarios_data, scenario_stats = _get_scenario_stats(state)

    all_keys = set()
    for stats in scenario_stats.values():
        all_keys.update(stats.keys())
    all_keys = sorted(all_keys)

    row_data = []
    for key in all_keys:
        full_name = get_metric_full_name(key)
        vals = {}
        for sname in scenario_names:
            vals[sname] = scenario_stats.get(sname, {}).get(key, {}).get("mean", None)
        bytes_mode = is_bytes_metric(key) and any(
            v is not None and abs(v) >= 1024 for v in vals.values()
        )
        row = {"metric_pretty": prettify_metric_name(key.replace(".", "_")), "metric_key": full_name}
        for sname in scenario_names:
            row[sname] = _format_table_value(vals[sname], bytes_mode)
        row_data.append(row)

    chart_sections = _build_export_charts(scenario_names, scenarios_data, scenario_stats)
    content = export_zip(row_data, scenario_names, chart_sections)
    return dcc.send_bytes(content, "performance_artifacts.zip")


def _fig_to_png(fig, width=700, height=400):
    """Convert a plotly figure to PNG bytes."""
    return fig.to_image(format="png", width=width, height=height)


def _add_bar(charts, title, key, scenario_names, scenario_stats, ylabel):
    """Add a bar chart if data exists for at least one scenario."""
    if any(scenario_stats.get(s, {}).get(key) for s in scenario_names):
        fig = _build_avg_bar_fig(title, key, scenario_names, scenario_stats, ylabel)
        charts.append((title, _fig_to_png(fig)))


def _add_line(charts, title, csv_metric, scenario_names, scenarios_data, ylabel):
    """Add a line chart if it has any traces."""
    fig = _build_ts_line_fig(title, csv_metric, scenario_names, scenarios_data, ylabel)
    if fig.data:
        charts.append((title, _fig_to_png(fig)))


def _build_export_charts(scenario_names, scenarios_data, scenario_stats):
    """Build all chart figures for export, grouped by section.

    Returns list of (section_title, [(chart_title, png_bytes), ...]).
    """
    sections = []

    # --- Ambient ---
    ambient_charts = []

    # Namespace totals
    _add_bar(ambient_charts, "Namespace CPU Total", "ambient.namespace.cpu_total",
             scenario_names, scenario_stats, "CPU (cores)")
    _add_bar(ambient_charts, "Namespace Memory Total", "ambient.namespace.memory_total",
             scenario_names, scenario_stats, "Memory (bytes)")

    # Per-component CPU & Memory
    components = [
        ("backend-api", "Backend Api"), ("public-api", "Public Api"),
        ("agentic-operator", "Agentic Operator"), ("ambient-api-server", "Ambient Api Server"),
        ("postgresql", "PostgreSQL"), ("minio", "MinIO"),
        ("frontend", "Frontend"), ("otel-collector", "Otel Collector"),
        ("unleash", "Unleash"),
    ]
    for comp_key, comp_name in components:
        _add_bar(ambient_charts, f"{comp_name} CPU", f"ambient.{comp_key}.cpu",
                 scenario_names, scenario_stats, "CPU (cores)")
        _add_bar(ambient_charts, f"{comp_name} Memory", f"ambient.{comp_key}.memory",
                 scenario_names, scenario_stats, "Memory (bytes)")

    # Session startup & image pull
    _add_bar(ambient_charts, "Session Startup Duration Avg", "ambient.session_startup.duration_avg",
             scenario_names, scenario_stats, "Duration (s)")
    _add_bar(ambient_charts, "Image Pull Duration Avg", "ambient.image_pull.duration_avg",
             scenario_names, scenario_stats, "Duration (s)")
    _add_line(ambient_charts, "Session Startup Avg Over Time", "measurements_ambient_session_startup_duration_avg",
              scenario_names, scenarios_data, "Duration (s)")
    _add_line(ambient_charts, "Session Startup P99 Over Time", "measurements_ambient_session_startup_duration_p99",
              scenario_names, scenarios_data, "Duration (s)")

    # Reconciliation
    _add_bar(ambient_charts, "Reconcile Duration Avg", "ambient.reconcile.duration_avg",
             scenario_names, scenario_stats, "Duration (s)")
    _add_line(ambient_charts, "Reconcile Rate Over Time", "measurements_ambient_reconcile_rate",
              scenario_names, scenarios_data, "ops/s")

    # Session counts
    _add_line(ambient_charts, "Active Sessions", "measurements_ambient_sessions_active",
              scenario_names, scenarios_data, "Count")
    _add_line(ambient_charts, "Pending Sessions", "measurements_ambient_sessions_pending",
              scenario_names, scenarios_data, "Count")

    if ambient_charts:
        sections.append(("Ambient Platform Metrics", ambient_charts))

    # --- Cluster ---
    cluster_charts = []

    # Resource bars
    _add_bar(cluster_charts, "Cluster CPU Usage Rate", "cluster.cpu_usage_rate",
             scenario_names, scenario_stats, "CPU (cores)")
    _add_bar(cluster_charts, "Cluster Memory RSS", "cluster.memory_rss_total",
             scenario_names, scenario_stats, "Memory (bytes)")
    _add_bar(cluster_charts, "Network Throughput", "cluster.network_bytes_total",
             scenario_names, scenario_stats, "Bytes")
    _add_bar(cluster_charts, "Disk Throughput", "cluster.disk_throughput_total",
             scenario_names, scenario_stats, "Bytes")

    # Resource time-series
    _add_line(cluster_charts, "CPU Usage Over Time", "measurements_cluster_cpu_usage_rate",
              scenario_names, scenarios_data, "CPU (cores)")
    _add_line(cluster_charts, "Memory RSS Over Time", "measurements_cluster_memory_rss_total",
              scenario_names, scenarios_data, "Bytes")
    _add_line(cluster_charts, "Worker Avg CPU Pct", "measurements_cluster_workers_avg_cpu_pct",
              scenario_names, scenarios_data, "%")
    _add_line(cluster_charts, "Running Pods on Workers", "measurements_cluster_running_pods_on_workers",
              scenario_names, scenarios_data, "Count")

    # API server & etcd
    _add_line(cluster_charts, "API Server Request Rate", "measurements_apiserver_request_total_rate",
              scenario_names, scenarios_data, "req/s")
    _add_line(cluster_charts, "etcd Request Duration Avg", "measurements_etcd_request_duration_avg",
              scenario_names, scenarios_data, "Duration (s)")

    # Pod & node counts
    _add_bar(cluster_charts, "Avg Worker Node Count", "cluster.worker_node_count",
             scenario_names, scenario_stats, "Count")
    _add_bar(cluster_charts, "Avg Pod Count", "cluster.pod_count",
             scenario_names, scenario_stats, "Count")
    _add_line(cluster_charts, "Cluster Pod Count Over Time", "measurements_cluster_pod_count",
              scenario_names, scenarios_data, "Count")
    _add_line(cluster_charts, "Worker Node Count Over Time", "measurements_cluster_worker_node_count",
              scenario_names, scenarios_data, "Count")

    if cluster_charts:
        sections.append(("Cluster Metrics", cluster_charts))

    # --- Locust ---
    locust_charts = []

    # Response time & throughput
    _add_line(locust_charts, "Avg Response Time", "results_locust_requests_avg_response_time_total",
              scenario_names, scenarios_data, "ms")
    _add_line(locust_charts, "Requests Per Second", "results_locust_requests_current_rps_total",
              scenario_names, scenarios_data, "req/s")

    # Errors
    _add_line(locust_charts, "Fail Ratio", "results_locust_requests_fail_ratio",
              scenario_names, scenarios_data, "Ratio")
    _add_line(locust_charts, "Failures Per Second", "results_locust_requests_current_fail_per_sec_total",
              scenario_names, scenarios_data, "fails/s")

    # Cumulative
    _add_line(locust_charts, "Total Requests", "results_locust_requests_num_requests_total",
              scenario_names, scenarios_data, "Count")
    _add_line(locust_charts, "Total Failures", "results_locust_requests_num_failures_total",
              scenario_names, scenarios_data, "Count")

    # Endpoint-specific
    _add_line(locust_charts, "Endpoint Avg Response Time",
              "results_GET_agentic-sessions_list_locust_requests_avg_response_time",
              scenario_names, scenarios_data, "ms")
    _add_line(locust_charts, "Endpoint RPS",
              "results_GET_agentic-sessions_list_locust_requests_current_rps",
              scenario_names, scenarios_data, "req/s")

    # Worker resources
    _add_bar(locust_charts, "Workers CPU", "locust.workers.cpu",
             scenario_names, scenario_stats, "CPU (cores)")
    _add_bar(locust_charts, "Workers Memory", "locust.workers.memory",
             scenario_names, scenario_stats, "Memory (bytes)")

    # Users & content
    _add_line(locust_charts, "Active Users", "results_locust_users",
              scenario_names, scenarios_data, "Users")
    _add_line(locust_charts, "Avg Content Length", "results_locust_requests_avg_content_length_total",
              scenario_names, scenarios_data, "Bytes")

    if locust_charts:
        sections.append(("Locust Load Test Metrics", locust_charts))

    return sections


@app.callback(
    Output("select-metrics", "options"),
    Input("select-data-source", "value"),
    State("store-benchmark-options", "data"),
    State("store-csv-options", "data"),
)
def update_metric_options(data_source, benchmark_opts, csv_opts):
    if data_source == "benchmark":
        return benchmark_opts or []
    return csv_opts or []


def render_panel(panel_config, state):
    """Render a single panel (chart or table) from its configuration."""
    panel_id = panel_config["id"]
    chart_type = panel_config["chart_type"]
    metrics = panel_config["metrics"]
    scenarios = panel_config["scenarios"]
    data_source = panel_config["data_source"]
    stat_field = panel_config.get("stat_field", "mean")

    scenarios_data = get_scenarios_data(state)
    colors = ["#3498db", "#e74c3c", "#2ecc71", "#f39c12", "#9b59b6", "#1abc9c", "#e67e22", "#8e44ad"]

    if data_source == "csv":
        if chart_type == "table":
            return _render_csv_table(metrics, scenarios, scenarios_data, panel_id)
        elif chart_type == "line":
            return _render_csv_line(metrics, scenarios, scenarios_data, colors)
        elif chart_type in ("bar", "grouped_bar"):
            return _render_csv_bar(metrics, scenarios, scenarios_data, stat_field, colors, chart_type)
    else:  # benchmark
        if chart_type == "table":
            return _render_benchmark_table(metrics, scenarios, scenarios_data, stat_field, panel_id)
        elif chart_type in ("bar", "grouped_bar"):
            return _render_benchmark_bar(metrics, scenarios, scenarios_data, stat_field, colors, chart_type)
        elif chart_type == "line":
            # For benchmark data, show bar chart since there's no time series
            return _render_benchmark_bar(metrics, scenarios, scenarios_data, stat_field, colors, "grouped_bar")

    return html.P("Unsupported panel configuration.")


def _render_csv_line(metrics, scenarios, scenarios_data, colors):
    fig = go.Figure()
    for metric in metrics:
        for i, sname in enumerate(scenarios):
            if sname not in scenarios_data:
                continue
            paths = scenarios_data[sname]["trial_paths"]
            df = average_csv_metric_across_trials(paths, metric)
            if df.empty:
                continue
            label = f"{sname} - {prettify_metric_name(metric)}" if len(metrics) > 1 else sname
            fig.add_trace(go.Scatter(
                x=df["elapsed_seconds"],
                y=df["value"],
                mode="lines",
                name=label,
                line=dict(color=colors[i % len(colors)]),
            ))
    title = ", ".join(prettify_metric_name(m) for m in metrics)
    fig.update_layout(
        title=title,
        xaxis_title="Elapsed Seconds",
        yaxis_title="Value",
        height=400,
        margin=dict(l=50, r=20, t=40, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    return dcc.Graph(figure=fig)


def _render_csv_bar(metrics, scenarios, scenarios_data, stat_field, colors, chart_type):
    """For CSV data, compute stats on the fly and show as bar chart."""
    fig = go.Figure()
    for j, metric in enumerate(metrics):
        values = []
        for sname in scenarios:
            if sname not in scenarios_data:
                values.append(0)
                continue
            paths = scenarios_data[sname]["trial_paths"]
            df = average_csv_metric_across_trials(paths, metric)
            if df.empty:
                values.append(0)
            else:
                values.append(df["value"].mean())
        name = prettify_metric_name(metric) if len(metrics) > 1 else "Mean"
        fig.add_trace(go.Bar(
            x=scenarios,
            y=values,
            name=name,
            marker_color=colors[j % len(colors)],
        ))
    title = ", ".join(prettify_metric_name(m) for m in metrics)
    fig.update_layout(
        title=title,
        xaxis_title="Scenario",
        yaxis_title="Value",
        barmode="group" if chart_type == "grouped_bar" else "stack",
        height=400,
        margin=dict(l=50, r=20, t=40, b=40),
    )
    return dcc.Graph(figure=fig)


def _render_csv_table(metrics, scenarios, scenarios_data, panel_id):
    """Render CSV time-series data as a table with stats computed per scenario."""
    stat_fields = ["mean", "min", "max", "median"]
    row_data = []
    for metric in metrics:
        for sname in scenarios:
            if sname not in scenarios_data:
                continue
            paths = scenarios_data[sname]["trial_paths"]
            df = average_csv_metric_across_trials(paths, metric)
            if df.empty:
                continue
            row = {
                "metric": prettify_metric_name(metric),
                "scenario": sname,
                "mean": round(df["value"].mean(), 6),
                "min": round(df["value"].min(), 6),
                "max": round(df["value"].max(), 6),
                "median": round(df["value"].median(), 6),
                "std": round(df["value"].std(), 6),
                "samples": len(df),
            }
            row_data.append(row)

    col_defs = [
        {"field": "metric", "headerName": "Metric", "width": 250, "filter": True},
        {"field": "scenario", "headerName": "Scenario", "width": 130, "filter": True},
        {"field": "mean", "headerName": "Mean", "width": 130},
        {"field": "min", "headerName": "Min", "width": 130},
        {"field": "max", "headerName": "Max", "width": 130},
        {"field": "median", "headerName": "Median", "width": 130},
        {"field": "std", "headerName": "Std Dev", "width": 130},
        {"field": "samples", "headerName": "Samples", "width": 100},
    ]

    return dag.AgGrid(
        rowData=row_data,
        columnDefs=col_defs,
        defaultColDef={"resizable": True, "sortable": True, "filter": True},
        dashGridOptions={"domLayout": "autoHeight"},
        style={"width": "100%"},
        className="ag-theme-alpine",
    )


def _render_benchmark_bar(metrics, scenarios, scenarios_data, stat_field, colors, chart_type):
    fig = go.Figure()
    for j, metric in enumerate(metrics):
        values = []
        for sname in scenarios:
            if sname not in scenarios_data:
                values.append(0)
                continue
            paths = scenarios_data[sname]["trial_paths"]
            stats = average_benchmark_stats_across_trials(paths)
            val = stats.get(metric, {}).get(stat_field, 0)
            values.append(val)
        name = metric.split(".")[-1] if len(metrics) > 1 else stat_field
        fig.add_trace(go.Bar(
            x=scenarios,
            y=values,
            name=name,
            marker_color=colors[j % len(colors)],
        ))
    title = ", ".join(m.split(".")[-1] for m in metrics)
    fig.update_layout(
        title=f"{title} ({stat_field})",
        xaxis_title="Scenario",
        yaxis_title=stat_field,
        barmode="group" if chart_type == "grouped_bar" else "stack",
        height=400,
        margin=dict(l=50, r=20, t=40, b=40),
    )
    return dcc.Graph(figure=fig)


def _render_benchmark_table(metrics, scenarios, scenarios_data, stat_field, panel_id):
    """Render benchmark stats as a multi-column table (scenarios as columns)."""
    stat_fields_to_show = ["mean", "median", "min", "max", "percentile95", "percentile99", "stdev"]
    row_data = []
    for metric in metrics:
        row = {"metric": metric}
        for sname in scenarios:
            if sname not in scenarios_data:
                continue
            paths = scenarios_data[sname]["trial_paths"]
            stats = average_benchmark_stats_across_trials(paths)
            val = stats.get(metric, {}).get(stat_field, None)
            row[sname] = round(val, 6) if val is not None else None
        row_data.append(row)

    col_defs = [
        {"field": "metric", "headerName": "Metric", "width": 300, "filter": True, "pinned": "left"},
    ] + [
        {"field": sname, "headerName": sname, "width": 150, "filter": "agNumberColumnFilter"}
        for sname in scenarios
        if sname in scenarios_data
    ]

    return dag.AgGrid(
        rowData=row_data,
        columnDefs=col_defs,
        defaultColDef={"resizable": True, "sortable": True, "filter": True},
        dashGridOptions={"domLayout": "autoHeight"},
        style={"width": "100%"},
        className="ag-theme-alpine",
    )


@app.callback(
    Output("store-panels", "data", allow_duplicate=True),
    Output("custom-panels-container", "children", allow_duplicate=True),
    Input("btn-add-panel", "n_clicks"),
    State("select-data-source", "value"),
    State("select-chart-type", "value"),
    State("select-metrics", "value"),
    State("select-scenarios", "value"),
    State("select-stat-field", "value"),
    State("store-panels", "data"),
    State("store-state", "data"),
    prevent_initial_call=True,
)
def add_panel(n_clicks, data_source, chart_type, metrics, scenarios, stat_field, panels, state):
    if not metrics or not scenarios:
        return dash.no_update, dash.no_update

    if isinstance(metrics, str):
        metrics = [metrics]
    if isinstance(scenarios, str):
        scenarios = [scenarios]

    panel_config = {
        "id": str(uuid.uuid4()),
        "data_source": data_source,
        "chart_type": chart_type,
        "metrics": metrics,
        "scenarios": scenarios,
        "stat_field": stat_field,
    }
    panels.append(panel_config)

    # Render all panels
    children = _render_all_panels(panels, state)
    return panels, children


def _render_all_panels(panels, state):
    children = []
    for i, panel in enumerate(panels):
        panel_content = render_panel(panel, state)
        card = dbc.Card(
            [
                dbc.CardHeader(
                    dbc.Row(
                        [
                            dbc.Col(
                                html.Small(
                                    f"{panel['chart_type'].upper()} | {panel['data_source']} | "
                                    f"{', '.join(prettify_metric_name(m) for m in panel['metrics'])}",
                                    className="text-muted",
                                ),
                            ),
                            dbc.Col(
                                dbc.Button(
                                    "X",
                                    id={"type": "btn-remove-panel", "index": i},
                                    color="danger",
                                    size="sm",
                                    outline=True,
                                ),
                                width="auto",
                            ),
                        ],
                        align="center",
                    ),
                ),
                dbc.CardBody(panel_content),
            ],
            className="mb-3",
        )
        children.append(card)
    return children


@app.callback(
    Output("store-panels", "data", allow_duplicate=True),
    Output("custom-panels-container", "children", allow_duplicate=True),
    Input({"type": "btn-remove-panel", "index": ALL}, "n_clicks"),
    State("store-panels", "data"),
    State("store-state", "data"),
    prevent_initial_call=True,
)
def remove_panel(n_clicks_list, panels, state):
    if not callback_context.triggered_id or not any(n_clicks_list):
        return dash.no_update, dash.no_update

    idx = callback_context.triggered_id["index"]
    if 0 <= idx < len(panels):
        panels.pop(idx)

    children = _render_all_panels(panels, state)
    return panels, children


@app.callback(
    Output("dashboard-save-feedback", "children"),
    Output("store-state", "data", allow_duplicate=True),
    Input("btn-save-dashboard", "n_clicks"),
    State("input-dashboard-name", "value"),
    State("store-panels", "data"),
    State("store-state", "data"),
    prevent_initial_call=True,
)
def save_dashboard(n_clicks, name, panels, state):
    if not name:
        return dbc.Alert("Enter a dashboard name.", color="warning", duration=3000), dash.no_update
    if not panels:
        return dbc.Alert("No panels to save.", color="warning", duration=3000), dash.no_update

    dashboard = {"name": name, "panels": panels}
    state.setdefault("custom_dashboards", [])

    # Update existing or append
    found = False
    for i, d in enumerate(state["custom_dashboards"]):
        if d["name"] == name:
            state["custom_dashboards"][i] = dashboard
            found = True
            break
    if not found:
        state["custom_dashboards"].append(dashboard)

    save_state(state)
    return dbc.Alert(f"Dashboard '{name}' saved.", color="success", duration=3000), state


@app.callback(
    Output("store-panels", "data", allow_duplicate=True),
    Output("custom-panels-container", "children", allow_duplicate=True),
    Output("dashboard-save-feedback", "children", allow_duplicate=True),
    Input("btn-load-dashboard", "n_clicks"),
    State("select-saved-dashboard", "value"),
    State("store-state", "data"),
    prevent_initial_call=True,
)
def load_dashboard(n_clicks, selected_idx, state):
    if selected_idx is None:
        return dash.no_update, dash.no_update, dbc.Alert("Select a dashboard.", color="warning", duration=3000)

    idx = int(selected_idx)
    dashboards = state.get("custom_dashboards", [])
    if idx >= len(dashboards):
        return dash.no_update, dash.no_update, dbc.Alert("Dashboard not found.", color="danger", duration=3000)

    dashboard = dashboards[idx]
    panels = dashboard["panels"]
    children = _render_all_panels(panels, state)
    return panels, children, dbc.Alert(f"Loaded: {dashboard['name']}", color="success", duration=3000)


@app.callback(
    Output("store-state", "data", allow_duplicate=True),
    Output("dashboard-save-feedback", "children", allow_duplicate=True),
    Input("btn-delete-dashboard", "n_clicks"),
    State("select-saved-dashboard", "value"),
    State("store-state", "data"),
    prevent_initial_call=True,
)
def delete_dashboard(n_clicks, selected_idx, state):
    if selected_idx is None:
        return dash.no_update, dbc.Alert("Select a dashboard first.", color="warning", duration=3000)

    idx = int(selected_idx)
    dashboards = state.get("custom_dashboards", [])
    if idx < len(dashboards):
        name = dashboards[idx]["name"]
        dashboards.pop(idx)
        state["custom_dashboards"] = dashboards
        save_state(state)
        return state, dbc.Alert(f"Deleted: {name}", color="info", duration=3000)

    return dash.no_update, dbc.Alert("Dashboard not found.", color="danger", duration=3000)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=8050)
