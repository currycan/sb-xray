#!/usr/bin/env python3
"""Render a clone-traffic trend chart from the accumulated traffic CSV.

Reads ``traffic/clones.csv`` (produced by sangonzal/repository-traffic-action,
schema: index ``_date`` plus ``total_clones`` / ``unique_clones``) and writes a
styled area+line chart to ``traffic/clones.png`` for embedding in the README.

Exits 0 on missing/empty data so the first scheduled run never fails the job.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.dates import AutoDateLocator, DateFormatter

CSV_PATH = Path("traffic/clones.csv")
OUT_PATH = Path("traffic/clones.png")

# 与 README 现有 badge 色系协调
COLOR_TOTAL = "#00B4D8"  # Docker Pulls badge 同色
COLOR_UNIQUE = "#9D4EDD"  # Xray-core badge 同色
COLOR_TEXT = "#2d3436"
COLOR_GRID = "#dfe6e9"


def load_rows() -> list[tuple[datetime, int, int]]:
    if not CSV_PATH.exists():
        print(f"[render_traffic] {CSV_PATH} not found; skipping chart render.")
        return []

    rows: list[tuple[datetime, int, int]] = []
    with CSV_PATH.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            raw_date = (row.get("_date") or "").strip()
            if not raw_date:
                continue
            try:
                day = datetime.strptime(raw_date[:10], "%Y-%m-%d")
                total = int(float(row.get("total_clones", 0) or 0))
                unique = int(float(row.get("unique_clones", 0) or 0))
            except (ValueError, TypeError):
                continue
            rows.append((day, total, unique))

    rows.sort(key=lambda r: r[0])
    return rows


def render(rows: list[tuple[datetime, int, int]]) -> None:
    days = [r[0] for r in rows]
    totals = [r[1] for r in rows]
    uniques = [r[2] for r in rows]

    fig, ax = plt.subplots(figsize=(9, 3.2), dpi=120)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    ax.fill_between(days, totals, color=COLOR_TOTAL, alpha=0.18, zorder=1)
    ax.plot(
        days,
        totals,
        color=COLOR_TOTAL,
        linewidth=2.2,
        marker="o",
        markersize=3,
        label="Total clones",
        zorder=3,
    )
    ax.plot(
        days,
        uniques,
        color=COLOR_UNIQUE,
        linewidth=2.0,
        marker="o",
        markersize=3,
        label="Unique cloners",
        zorder=2,
    )

    ax.set_title(
        "Repository Clone Traffic",
        color=COLOR_TEXT,
        fontsize=13,
        fontweight="bold",
        loc="left",
        pad=10,
    )
    ax.grid(True, color=COLOR_GRID, linewidth=0.8, alpha=0.7)
    ax.xaxis.set_major_locator(AutoDateLocator())
    ax.xaxis.set_major_formatter(DateFormatter("%m-%d"))
    ax.tick_params(colors=COLOR_TEXT, labelsize=9)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(COLOR_GRID)
    ax.set_ylim(bottom=0)
    ax.legend(frameon=False, fontsize=9, loc="upper left")

    fig.tight_layout()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PATH, bbox_inches="tight", facecolor="white")
    print(f"[render_traffic] wrote {OUT_PATH} ({len(rows)} data points).")


def main() -> None:
    rows = load_rows()
    if not rows:
        print("[render_traffic] no data to plot; exiting without chart.")
        return
    render(rows)


if __name__ == "__main__":
    main()
