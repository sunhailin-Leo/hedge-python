#!/usr/bin/env python3
"""Generate evaluation charts from benchmark results CSVs.

Two charts are produced when the corresponding CSV exists:

  * ``eval.png`` — single-framework (httpx) chart comparing four hedge
    configurations. Source: ``benchmark/results.csv`` produced by
    ``make bench-compare``.
  * ``eval_multi_framework.png`` — cross-framework chart with one subplot per
    framework (httpx / aiohttp / grpc), each showing No hedging vs Adaptive.
    Source: ``benchmark/results_multi.csv`` produced by ``make bench-multi``.

Usage::

    make bench-compare && make bench-multi
    uv run python benchmark/plot.py
"""

from __future__ import annotations

import csv
import os
import sys
from collections import OrderedDict

try:
    import matplotlib.pyplot as plt
except ImportError:
    print("matplotlib is required: pip install matplotlib", file=sys.stderr)
    sys.exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))

RESULTS_CSV = os.path.join(SCRIPT_DIR, "results.csv")
RESULTS_MULTI_CSV = os.path.join(SCRIPT_DIR, "results_multi.csv")

OUTPUT_PNG = os.path.join(PROJECT_ROOT, "eval.png")
OUTPUT_MULTI_PNG = os.path.join(PROJECT_ROOT, "eval_multi_framework.png")

CONFIG_COLORS = {
    "No hedging": "tab:blue",
    "Static 10ms": "tab:orange",
    "Static 50ms": "tab:green",
    "Adaptive (hedge)": "tab:red",
}

PERCENTILE_COLUMNS = ["p50", "p90", "p95", "p99", "p999"]


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------
def load_results(csv_path: str) -> list[dict[str, str]]:
    """Load benchmark results from CSV."""
    with open(csv_path, newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        return list(reader)


# ---------------------------------------------------------------------------
# Single-framework chart (configuration comparison)
# ---------------------------------------------------------------------------
def plot_latency_chart(rows: list[dict[str, str]], output_path: str) -> None:
    """Plot one line per configuration on a single axis."""
    x_positions = range(len(PERCENTILE_COLUMNS))
    fig, ax = plt.subplots(figsize=(10, 4))

    for row in rows:
        config_name = row["configuration"]
        values = [float(row[col]) for col in PERCENTILE_COLUMNS]
        color = CONFIG_COLORS.get(config_name, "gray")
        ax.plot(x_positions, values, marker="o", label=config_name, color=color, linewidth=2)

    ax.set_xticks(list(x_positions))
    ax.set_xticklabels(PERCENTILE_COLUMNS)
    ax.set_ylabel("Latency (ms)")
    ax.set_title("Tail latency: hedge-python benchmark with 5% stragglers")
    ax.legend(loc="upper left")
    ax.grid(True, color="lightgray", alpha=0.7)
    ax.set_facecolor("white")
    fig.patch.set_facecolor("white")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    print(f"Saved {output_path}")
    plt.close(fig)


def print_markdown_table(rows: list[dict[str, str]]) -> None:
    """Print single-framework results as a Markdown table."""
    header = "| Configuration        |  p50  |  p90  |   p95  |   p99  |  p999   | Overhead |"
    separator = "|----------------------|-------|-------|--------|--------|---------|----------|"
    print(f"\n{header}")
    print(separator)
    for row in rows:
        name = row["configuration"]
        overhead = row.get("overhead_pct", "0.0")
        values = [float(row[col]) for col in PERCENTILE_COLUMNS]
        print(
            f"| {name:<20s} "
            f"| {values[0]:5.1f} "
            f"| {values[1]:5.1f} "
            f"| {values[2]:6.1f} "
            f"| {values[3]:6.1f} "
            f"| {values[4]:7.1f} "
            f"| {float(overhead):7.1f}% |"
        )
    print()


# ---------------------------------------------------------------------------
# Multi-framework chart (one subplot per framework)
# ---------------------------------------------------------------------------
def _group_by_framework(
    rows: list[dict[str, str]],
) -> "OrderedDict[str, list[dict[str, str]]]":
    """Group rows by framework while preserving original ordering."""
    groups: OrderedDict[str, list[dict[str, str]]] = OrderedDict()
    for row in rows:
        groups.setdefault(row["framework"], []).append(row)
    return groups


def plot_multi_framework_chart(
    rows: list[dict[str, str]],
    output_path: str,
) -> None:
    """Render one subplot per framework, sharing a Y axis for easy comparison."""
    groups = _group_by_framework(rows)
    framework_names = list(groups.keys())
    if not framework_names:
        return

    fig, axes = plt.subplots(
        1,
        len(framework_names),
        figsize=(5 * len(framework_names), 4),
        sharey=True,
    )
    if len(framework_names) == 1:
        axes = [axes]

    x_positions = range(len(PERCENTILE_COLUMNS))

    for ax, framework in zip(axes, framework_names):
        for row in groups[framework]:
            config_name = row["configuration"]
            values = [float(row[col]) for col in PERCENTILE_COLUMNS]
            color = CONFIG_COLORS.get(config_name, "gray")
            ax.plot(
                x_positions,
                values,
                marker="o",
                label=config_name,
                color=color,
                linewidth=2,
            )
        ax.set_xticks(list(x_positions))
        ax.set_xticklabels(PERCENTILE_COLUMNS)
        ax.set_title(framework)
        ax.set_xlabel("Percentile")
        ax.grid(True, color="lightgray", alpha=0.7)
        ax.set_facecolor("white")
        ax.legend(loc="upper left", fontsize=9)

    axes[0].set_ylabel("Latency (ms)")
    fig.suptitle("Hedge effectiveness across frameworks (5% stragglers)")
    fig.patch.set_facecolor("white")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    print(f"Saved {output_path}")
    plt.close(fig)


def print_multi_framework_table(rows: list[dict[str, str]]) -> None:
    """Print multi-framework results as a Markdown table."""
    header = (
        "| Framework | Configuration       |  p50  |  p90  |   p95  |   p99  |  p999   | Overhead |"
    )
    separator = (
        "|-----------|---------------------|-------|-------|--------|--------|---------|----------|"
    )
    print(f"\n{header}")
    print(separator)
    for row in rows:
        framework = row.get("framework", "")
        name = row["configuration"]
        overhead = row.get("overhead_pct", "0.0")
        values = [float(row[col]) for col in PERCENTILE_COLUMNS]
        print(
            f"| {framework:<9s} "
            f"| {name:<19s} "
            f"| {values[0]:5.1f} "
            f"| {values[1]:5.1f} "
            f"| {values[2]:6.1f} "
            f"| {values[3]:6.1f} "
            f"| {values[4]:7.1f} "
            f"| {float(overhead):7.1f}% |"
        )
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    rendered_any = False

    if os.path.exists(RESULTS_CSV):
        rows = load_results(RESULTS_CSV)
        if rows:
            print(f"Loaded {len(rows)} configurations from {RESULTS_CSV}")
            print_markdown_table(rows)
            plot_latency_chart(rows, OUTPUT_PNG)
            rendered_any = True
        else:
            print(f"No data in {RESULTS_CSV}", file=sys.stderr)
    else:
        print(
            f"Skipping single-framework chart: {RESULTS_CSV} not found "
            "(run 'make bench-compare').",
            file=sys.stderr,
        )

    if os.path.exists(RESULTS_MULTI_CSV):
        rows = load_results(RESULTS_MULTI_CSV)
        if rows:
            print(f"Loaded {len(rows)} rows from {RESULTS_MULTI_CSV}")
            print_multi_framework_table(rows)
            plot_multi_framework_chart(rows, OUTPUT_MULTI_PNG)
            rendered_any = True
        else:
            print(f"No data in {RESULTS_MULTI_CSV}", file=sys.stderr)
    else:
        print(
            f"Skipping multi-framework chart: {RESULTS_MULTI_CSV} not found "
            "(run 'make bench-multi').",
            file=sys.stderr,
        )

    if not rendered_any:
        sys.exit(1)


if __name__ == "__main__":
    main()
