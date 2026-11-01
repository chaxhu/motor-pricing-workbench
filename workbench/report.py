"""Write the results of a pricing run as a chart, a markdown report and a JSON summary.

Why this module exists: the numbers are only useful if someone can read
them. The report puts everything a reviewer needs on one page: what was
compared, whether the numbers reconcile, what was rejected and why, the
impact on the portfolio and on each segment, and how long each step took.
summary.json holds the same headline numbers for other tools to read.

The data and rates are synthetic. The rates are illustrative assumptions,
not real Hastings Direct pricing.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import matplotlib

matplotlib.use("Agg")  # draw to files only, no window

import matplotlib.pyplot as plt  # noqa: E402  (must come after choosing the backend)
import pandas as pd  # noqa: E402

from workbench.compare import Check  # noqa: E402
from workbench.validate import ValidationResult  # noqa: E402

logger = logging.getLogger(__name__)

CHART_NAME = "pct_change_distribution.png"


def format_pounds(value: float, sign: bool = False) -> str:
    """Format money as £1,234.56, or +£1,234.56 / -£1,234.56 when sign is True."""
    text = f"£{abs(value):,.2f}"
    if value < 0:
        return "-" + text
    return ("+" + text) if sign else text


def plot_distribution(results: pd.DataFrame, path: str | Path) -> Path:
    """Save a histogram of the % change for the policies whose premium changed."""
    path = Path(path)
    affected = results.loc[results["direction"] != "no change", "pct_change"]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    if affected.empty:
        ax.text(0.5, 0.5, "No policies changed", ha="center", va="center", transform=ax.transAxes)
    else:
        ax.hist(affected, bins=40, color="#3b6ea5", edgecolor="white")
    ax.axvline(0, color="#444444", linewidth=1)
    ax.set_title(f"Premium change for the {len(affected):,} policies affected")
    ax.set_xlabel("Change in premium (%)")
    ax.set_ylabel("Number of policies")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    logger.info("Saved chart to %s", path)
    return path


def markdown_table(df: pd.DataFrame, formats: dict[str, Callable[[Any], str]] | None = None) -> str:
    """Turn a DataFrame into a markdown table. formats maps a column to a function that formats its values."""
    formats = formats or {}
    header = "| " + " | ".join(str(col) for col in df.columns) + " |"
    divider = "| " + " | ".join("---" for _ in df.columns) + " |"
    rows = [
        "| " + " | ".join(formats.get(col, _default_format)(value) for col, value in row.items()) + " |"
        for _, row in df.iterrows()
    ]
    return "\n".join([header, divider, *rows])


def _default_format(value: Any) -> str:
    """Whole numbers with commas, other numbers to 2 decimal places, anything else as text."""
    if hasattr(value, "item"):  # a NumPy number: use the plain Python value
        value = value.item()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    return f"{value:,}" if isinstance(value, int) else f"{value:,.2f}"


def _pct(value: float, sign: bool = True) -> str:
    return f"{value:+.2f}%" if sign else f"{value:.2f}%"


def write_report(
    out_dir: str | Path,
    *,
    current_version: str,
    proposed_version: str,
    validation: ValidationResult,
    summary: dict[str, Any],
    distribution: pd.DataFrame,
    segments: pd.DataFrame,
    checks: list[Check],
    timings: dict[str, float],
    chart_path: Path,
) -> Path:
    """Write report.md and summary.json to out_dir. Returns the path of report.md."""
    out_dir = Path(out_dir)
    passed = all(check.passed for check in checks)

    sections = [
        _header_section(current_version, proposed_version),
        _reconciliation_section(checks, passed),
        _input_section(validation),
        _impact_section(summary),
        _distribution_section(distribution, chart_path, out_dir),
        _segment_section(segments),
        _timing_section(timings),
    ]
    report_path = out_dir / "report.md"
    report_path.write_text("\n\n".join(sections) + "\n", encoding="utf-8")

    (out_dir / "summary.json").write_text(
        json.dumps(
            {
                "rates": {"current": current_version, "proposed": proposed_version},
                "summary": summary,
                "reconciliation_passed": passed,
                "checks": [asdict(check) for check in checks],
                "timings_seconds": {step: round(sec, 4) for step, sec in timings.items()},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("Wrote %s and summary.json", report_path)
    return report_path


def _header_section(current_version: str, proposed_version: str) -> str:
    return "\n".join([
        "# Motor Pricing Workbench: rate change impact",
        "",
        f"Run at {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        f"Compared rate table **{current_version}** with rate table **{proposed_version}**.",
        "",
        "> All data and rates here are synthetic. The rates are illustrative assumptions, "
        "not real Hastings Direct pricing.",
    ])


def _reconciliation_section(checks: list[Check], passed: bool) -> str:
    table = pd.DataFrame(
        {
            "check": [c.name for c in checks],
            "result": ["PASS" if c.passed else "FAIL" for c in checks],
            "detail": [c.detail for c in checks],
        }
    )
    return f"## Reconciliation: {'PASSED' if passed else 'FAILED'}\n\n{markdown_table(table)}"


def _input_section(validation: ValidationResult) -> str:
    n_valid, n_rejected = len(validation.valid), len(validation.rejected)
    lines = [
        "## Input",
        "",
        f"- Policies read: {n_valid + n_rejected:,}",
        f"- Valid and priced: {n_valid:,}",
        f"- Rejected: {n_rejected:,} ({validation.reject_rate:.2%}). "
        "Rejected policies are kept, with reasons, in rejected_policies.csv.",
    ]
    if n_rejected:
        counts = validation.reason_counts().rename_axis("reason").reset_index()
        lines += ["", "Rejection reasons (a policy can have more than one):", "", markdown_table(counts)]
    return "\n".join(lines)


def _impact_section(s: dict[str, Any]) -> str:
    return "\n".join([
        "## Portfolio impact",
        "",
        markdown_table(
            pd.DataFrame(
                [
                    ("Policies affected", f"{s['policies_affected']:,} of {s['policies']:,}"),
                    ("Premium goes up", f"{s['increases']:,}"),
                    ("Premium goes down", f"{s['decreases']:,}"),
                    ("Current total", format_pounds(s["current_total"])),
                    ("Proposed total", format_pounds(s["proposed_total"])),
                    ("Total change", f"{format_pounds(s['total_change'], sign=True)} ({_pct(s['total_change_pct'])})"),
                    ("Average change per affected policy",
                     f"{format_pounds(s['avg_change_affected'], sign=True)} ({_pct(s['avg_pct_change_affected'])})"),
                    ("Largest increase", _pct(s["max_increase_pct"])),
                    ("Largest decrease", _pct(s["max_decrease_pct"])),
                ],
                columns=["measure", "value"],
            )
        ),
    ])


def _distribution_section(distribution: pd.DataFrame, chart_path: Path, out_dir: Path) -> str:
    table = markdown_table(distribution, {"share": lambda v: f"{v:.1%}"})
    chart = chart_path.relative_to(out_dir).as_posix() if chart_path.is_relative_to(out_dir) else chart_path.as_posix()
    return "\n".join([
        "## How premiums change",
        "",
        table,
        "",
        f"![Distribution of premium changes]({chart})",
    ])


def _segment_section(segments: pd.DataFrame) -> str:
    money = lambda v: f"{v:,.2f}"  # noqa: E731
    pct = lambda v: f"{v:+.2f}%"  # noqa: E731
    formats = {
        "current_total": money, "proposed_total": money, "total_change": lambda v: f"{v:+,.2f}",
        "avg_pct_change": pct, "max_pct_change": pct,
    }
    return "\n".join([
        "## Impact by segment (from sql/impact_by_segment.sql)",
        "",
        "Totals are in pounds. Sorted by total change, largest first.",
        "",
        markdown_table(segments, formats),
    ])


def _timing_section(timings: dict[str, float]) -> str:
    table = pd.DataFrame({"step": list(timings), "seconds": [f"{sec:.3f}" for sec in timings.values()]})
    return f"## Run timings\n\n{markdown_table(table)}\n\nTotal: {sum(timings.values()):.3f} seconds (before writing this report)."
