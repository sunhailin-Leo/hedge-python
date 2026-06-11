#!/usr/bin/env python3
"""Generate before/after performance comparison charts from optimization results.

Produces two outputs:
  * ``eval_optimization.png`` — bar chart comparing baseline vs optimized for each operation
  * ``eval_optimization_summary.png`` — summary chart showing improvement percentages

Usage::

    uv run python benchmark/plot_optimization.py
"""

from __future__ import annotations

import csv
import os
import sys

try:
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
except ImportError:
    print("matplotlib is required: pip install matplotlib", file=sys.stderr)
    sys.exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))

RESULTS_CSV = os.path.join(SCRIPT_DIR, "results_optimization.csv")
OUTPUT_BAR_PNG = os.path.join(PROJECT_ROOT, "eval_optimization.png")
OUTPUT_SUMMARY_PNG = os.path.join(PROJECT_ROOT, "eval_optimization_summary.png")


def load_results(csv_path: str) -> list[dict[str, str]]:
    with open(csv_path, newline="") as csvfile:
        return list(csv.DictReader(csvfile))


def plot_before_after_bars(rows: list[dict[str, str]], output_path: str) -> None:
    """Grouped bar chart: baseline vs optimized for each operation."""
    # Filter to operations with meaningful change (exclude ~0% changes)
    significant = [r for r in rows if abs(float(r["change_pct"])) > 2.0]

    labels = [f"{r['component']}\n{r['operation']}" for r in significant]
    baselines = [float(r["baseline_ns"]) for r in significant]
    optimized = [float(r["optimized_ns"]) for r in significant]
    changes = [float(r["change_pct"]) for r in significant]

    x = range(len(labels))
    bar_width = 0.35

    fig, ax = plt.subplots(figsize=(14, 6))

    bars_base = ax.bar(
        [i - bar_width / 2 for i in x],
        baselines,
        bar_width,
        label="Before (baseline)",
        color="#E74C3C",
        alpha=0.85,
        edgecolor="white",
        linewidth=0.5,
    )
    bars_opt = ax.bar(
        [i + bar_width / 2 for i in x],
        optimized,
        bar_width,
        label="After (optimized)",
        color="#2ECC71",
        alpha=0.85,
        edgecolor="white",
        linewidth=0.5,
    )

    # Add change percentage labels on top of optimized bars
    for i, (bar_opt, change) in enumerate(zip(bars_opt, changes)):
        label_color = "#27AE60" if change < 0 else "#E74C3C"
        sign = "" if change < 0 else "+"
        ax.text(
            bar_opt.get_x() + bar_opt.get_width() / 2,
            bar_opt.get_height() + max(baselines) * 0.02,
            f"{sign}{change:.1f}%",
            ha="center",
            va="bottom",
            fontsize=10,
            fontweight="bold",
            color=label_color,
        )

    ax.set_ylabel("Time (ns)", fontsize=12)
    ax.set_title(
        "hedge-python Optimization: Before vs After",
        fontsize=14,
        fontweight="bold",
        pad=15,
    )
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=9, ha="center")
    ax.legend(fontsize=11, loc="upper right")
    ax.grid(axis="y", color="lightgray", alpha=0.5)
    ax.set_facecolor("#FAFAFA")
    fig.patch.set_facecolor("white")
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved {output_path}")
    plt.close(fig)


def plot_improvement_summary(rows: list[dict[str, str]], output_path: str) -> None:
    """Horizontal bar chart showing improvement percentages."""
    # Only show items with actual improvement
    improved = [r for r in rows if float(r["change_pct"]) < -1.0]
    # Sort by improvement magnitude
    improved.sort(key=lambda r: float(r["change_pct"]))

    labels = [f"{r['component']} — {r['operation']}" for r in improved]
    changes = [abs(float(r["change_pct"])) for r in improved]
    opt_names = [r["optimization"] for r in improved]

    fig, ax = plt.subplots(figsize=(12, 5))

    colors = []
    for change in changes:
        if change >= 60:
            colors.append("#27AE60")
        elif change >= 30:
            colors.append("#F39C12")
        else:
            colors.append("#3498DB")

    bars = ax.barh(range(len(labels)), changes, color=colors, alpha=0.85, edgecolor="white")

    # Add value labels and optimization names
    for i, (bar, change, opt_name) in enumerate(zip(bars, changes, opt_names)):
        ax.text(
            bar.get_width() + 1,
            bar.get_y() + bar.get_height() / 2,
            f" -{change:.1f}%  ({opt_name})",
            ha="left",
            va="center",
            fontsize=9,
            color="#333333",
        )

    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=10)
    ax.set_xlabel("Performance Improvement (%)", fontsize=12)
    ax.set_title(
        "hedge-python Optimization Impact Summary",
        fontsize=14,
        fontweight="bold",
        pad=15,
    )
    ax.set_xlim(0, max(changes) * 1.45)
    ax.invert_yaxis()
    ax.grid(axis="x", color="lightgray", alpha=0.5)
    ax.set_facecolor("#FAFAFA")
    fig.patch.set_facecolor("white")
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # Legend for color coding
    from matplotlib.patches import Patch

    legend_elements = [
        Patch(facecolor="#27AE60", alpha=0.85, label="Major (≥60%)"),
        Patch(facecolor="#F39C12", alpha=0.85, label="Moderate (30-59%)"),
        Patch(facecolor="#3498DB", alpha=0.85, label="Minor (<30%)"),
    ]
    ax.legend(handles=legend_elements, loc="lower right", fontsize=9)

    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved {output_path}")
    plt.close(fig)


def print_markdown_table(rows: list[dict[str, str]]) -> None:
    """Print optimization results as a Markdown table."""
    header = "| Component | Operation | Baseline (ns) | Optimized (ns) | Change | Optimization |"
    separator = "|-----------|-----------|---------------|----------------|--------|--------------|"
    print(f"\n{header}")
    print(separator)
    for row in rows:
        change = float(row["change_pct"])
        sign = "" if change < 0 else "+"
        emoji = "✅" if change < -5 else ("⚠️" if change > 5 else "➖")
        print(
            f"| {row['component']:<9s} "
            f"| {row['operation']:<25s} "
            f"| {float(row['baseline_ns']):>13.1f} "
            f"| {float(row['optimized_ns']):>14.1f} "
            f"| {sign}{change:.1f}% {emoji} "
            f"| {row['optimization']} |"
        )
    print()


def main() -> None:
    if not os.path.exists(RESULTS_CSV):
        print(f"Error: {RESULTS_CSV} not found", file=sys.stderr)
        sys.exit(1)

    rows = load_results(RESULTS_CSV)
    if not rows:
        print("No data found", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(rows)} optimization results")
    print_markdown_table(rows)
    plot_before_after_bars(rows, OUTPUT_BAR_PNG)
    plot_improvement_summary(rows, OUTPUT_SUMMARY_PNG)
    print("\nDone! Generated 2 charts.")


if __name__ == "__main__":
    main()
