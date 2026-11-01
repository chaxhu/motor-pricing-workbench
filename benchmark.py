"""Time the two pricing engines and validation on portfolios of different sizes.

Why this script exists: the rule is measure, don't guess. The vectorised
engine is meant to be much faster than the row-by-row reference engine, and
this script shows by how much, and at what portfolio size it starts to
matter. It also times validation, to show which step is now the slowest.

Before timing anything it checks the two engines give exactly the same
premiums, because a fast wrong answer is worthless.

Run from the project root:
    python benchmark.py --sizes 1000 10000 100000 --repeats 5

Results are printed and written to outputs/benchmark_results.md, which git
ignores, so a quick run never changes the committed results. Add --save to
also update docs/benchmark_results.md, the copy the README quotes.

The data and rates are synthetic; the rates are illustrative, not real
Hastings Direct pricing.
"""

from __future__ import annotations

import argparse
import os
import platform
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from workbench.data import generate_policies
from workbench.pricing import price_portfolio, price_row_by_row
from workbench.rates import load_rate_table
from workbench.validate import validate_policies

PROJECT_ROOT = Path(__file__).resolve().parent
RESULTS_PATH = PROJECT_ROOT / "outputs" / "benchmark_results.md"
# Only written with --save, because the README quotes this file.
SAVED_RESULTS_PATH = PROJECT_ROOT / "docs" / "benchmark_results.md"
SEED = 0
# Row-by-row is slow, so above this size it is timed once instead of best of N.
ROW_BY_ROW_SINGLE_RUN_ABOVE = 100_000


def best_time(func: Callable[[], object], repeats: int) -> float:
    """Run func repeats times and return the fastest time in seconds."""
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        func()
        times.append(time.perf_counter() - start)
    return min(times)


def benchmark_size(n: int, repeats: int) -> dict[str, float]:
    """Generate n policies, check the engines agree, then time each step."""
    current = load_rate_table(PROJECT_ROOT / "config" / "rates_current.yaml")
    policies = generate_policies(n, current, seed=SEED)

    fast = price_portfolio(policies, current).to_numpy()
    slow = price_row_by_row(policies, current).to_numpy()
    differ = int((fast != slow).sum())
    assert differ == 0, f"engines disagree on {differ} of {n} policies; not timing a wrong answer"

    row_repeats = 1 if n > ROW_BY_ROW_SINGLE_RUN_ABOVE else repeats
    return {
        "policies": n,
        "row_by_row": best_time(lambda: price_row_by_row(policies, current), row_repeats),
        "vectorised": best_time(lambda: price_portfolio(policies, current), repeats),
        "validation": best_time(lambda: validate_policies(policies, current), repeats),
        "row_repeats": row_repeats,
    }


def write_results(rows: list[dict[str, float]], repeats: int, command: str, paths: list[Path]) -> None:
    """Write the results table, with the machine details and the command used, to each of paths."""
    lines = [
        "# Benchmark results",
        "",
        f"Run on {datetime.now():%Y-%m-%d %H:%M} with `{command}`.",
        "",
        f"- Machine: {platform.system()} {platform.release()}, {platform.machine()}, "
        f"{platform.processor() or 'unknown processor'}, {os.cpu_count()} logical CPUs",
        f"- Python {platform.python_version()}, pandas {pd.__version__}, NumPy {np.__version__}",
        f"- Times are the best of {repeats} runs, except row-by-row above "
        f"{ROW_BY_ROW_SINGLE_RUN_ABOVE:,} policies, which is run once.",
        "- Both engines were checked to give identical premiums before timing.",
        "- Synthetic data, illustrative rates (not real Hastings Direct pricing).",
        "",
        "| Policies | Row-by-row (s) | Vectorised (s) | Speed-up | Validation (s) |",
        "| ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in rows:
        lines.append(
            f"| {r['policies']:,} | {r['row_by_row']:.4f} | {r['vectorised']:.4f} "
            f"| {r['row_by_row'] / r['vectorised']:,.0f}x | {r['validation']:.4f} |"
        )
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Time the pricing engines and validation.")
    parser.add_argument("--sizes", type=int, nargs="+", default=[1_000, 10_000, 100_000])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument(
        "--save", action="store_true",
        help="also update docs/benchmark_results.md, the results the README quotes",
    )
    args = parser.parse_args(argv)

    print(f"Python {platform.python_version()} on {platform.system()} {platform.release()}")
    rows = []
    for n in args.sizes:
        r = benchmark_size(n, args.repeats)
        rows.append(r)
        print(
            f"{n:>10,} policies | row-by-row {r['row_by_row']:8.4f}s | vectorised {r['vectorised']:7.4f}s "
            f"| speed-up {r['row_by_row'] / r['vectorised']:6,.0f}x | validation {r['validation']:7.4f}s"
        )
    command = f"python benchmark.py --sizes {' '.join(map(str, args.sizes))} --repeats {args.repeats}"
    command += " --save" if args.save else ""
    paths = [RESULTS_PATH, SAVED_RESULTS_PATH] if args.save else [RESULTS_PATH]
    write_results(rows, args.repeats, command, paths)
    for path in paths:
        print(f"Wrote {path.relative_to(PROJECT_ROOT)}")
    if not args.save:
        print("docs/benchmark_results.md not changed (use --save to update it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
