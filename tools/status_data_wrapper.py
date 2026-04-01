#!/usr/bin/env python3
"""Wrapper around opl.status_data:main that monkey-patches data_stats()
to include percentile50 in the computed statistics.

OPL's data_stats() computes percentile25/75/90/95/99/999 but not
percentile50.  Rather than forking OPL we patch the function at import
time so the benchmark JSON contains a true p50 (computed with the same
linear-interpolation method used for all other percentiles).

Usage:
    Replace calls to ``status_data.py`` with ``python3 tools/status_data_wrapper.py``
    (all CLI arguments are forwarded unchanged).
"""

import opl.data as _opl_data
from opl.status_data import main

_original_data_stats = _opl_data.data_stats


def _patched_data_stats(data):
    stats = _original_data_stats(data)
    if "percentile25" in stats and "percentile50" not in stats:
        stats["percentile50"] = _opl_data.percentile(data[:], 50)
    return stats


_opl_data.data_stats = _patched_data_stats

if __name__ == "__main__":
    main()
