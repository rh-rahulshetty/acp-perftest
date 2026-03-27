# Performance Reporter Dashboard

Interactive dashboard for comparing performance test results across scenarios and trials.

## Setup

```bash
cd tools/reporter
pip install -r requirements.txt
```

## Running

```bash
python app.py
```

The dashboard opens at [http://127.0.0.1:8050](http://127.0.0.1:8050).

## Loading Data

1. Enter the path to a parent folder containing scenario directories in the **Scan & Load** input, or click **Browse** to navigate the filesystem.
2. Click **Scan & Load**.

Expected folder structure:

```
parent_folder/
  scenario_A/
    trial_1/
      benchmark.json
      monitoring/
        measurements_ambient_backend-api_cpu.csv
        ...
    trial_2/
      ...
  scenario_B/
    trial_1/
      ...
```

Each trial folder must contain a `benchmark.json` and a `monitoring/` directory with CSV files.

Example:

```
hack/session-listing/varying_session/
  20_session/
    trial1/
    trial2/
  50_session/
    trial1/
    trial2/
  200_session/
    trial1/
```

## Dashboard Tabs

- **Overview** -- Full metrics comparison table with search filter. Hover on any cell to see the metric name.
- **Ambient** -- ACP component resource usage (CPU, memory), session lifecycle, reconciliation metrics.
- **Cluster** -- Cluster-level CPU, memory, network, API server, etcd, pod/node counts.
- **Locust** -- Load test results: response times, RPS, error rates, worker resources.
- **Custom** -- Build your own dashboard panels. Panels can be saved and loaded across sessions.

## Screenshots

<!-- Add screenshots below -->

![Dashboard Overview](screenshots/overview.png)

![Cluster Tab](screenshots/cluster.png)

![Custom Dashboard](screenshots/custom.png)
