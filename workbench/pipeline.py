"""Run the whole workbench from the command line: python -m workbench.pipeline

Why this module exists: it joins every step into one repeatable run, so a
rate change can be evaluated with a single command and the same inputs
always give the same report. The steps are: load rates, load or generate
policies, validate, compare current and proposed premiums, summarise,
reconcile, and write the outputs.

Exit codes:
    0  success
    1  too many rejected rows (reject rate above --max-reject-rate)
    2  reconciliation failed (outputs are still written so the failure can be read)
    3  bad input or config (rate table, missing columns, missing file, or a
       value that cannot be priced)

All rates are illustrative assumptions, not real Hastings Direct pricing.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pandas as pd

from workbench.compare import (
    change_distribution,
    compare_premiums,
    portfolio_summary,
    reconcile,
    segment_impact,
)
from workbench.data import generate_policies, inject_errors
from workbench.pricing import PricingError
from workbench.rates import RateTableError, load_rate_table
from workbench.report import CHART_NAME, format_pounds, plot_distribution, write_report
from workbench.validate import SchemaError, validate_policies

logger = logging.getLogger("workbench.pipeline")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EXIT_OK = 0
EXIT_TOO_MANY_REJECTS = 1
EXIT_RECONCILIATION_FAILED = 2
EXIT_BAD_INPUT = 3

# Errors that mean the inputs or config are wrong, rather than a bug in the code.
BAD_INPUT_ERRORS = (
    RateTableError, SchemaError, FileNotFoundError, PricingError,
    pd.errors.EmptyDataError, pd.errors.ParserError,
)


@contextmanager
def timed(step: str, timings: dict[str, float]) -> Iterator[None]:
    """Time the code inside the with block, store it under step, and log it."""
    start = time.perf_counter()
    yield
    timings[step] = time.perf_counter() - start
    logger.info("Step '%s' took %.3f s", step, timings[step])


class _ArgumentParser(argparse.ArgumentParser):
    """argparse exits with 2 on a bad argument, which would look like a failed
    reconciliation. A bad argument is bad input, so exit with 3 instead."""

    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        self.exit(EXIT_BAD_INPUT, f"{self.prog}: error: {message}\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _ArgumentParser(
        prog="python -m workbench.pipeline",
        description="Evaluate a proposed motor pricing change on a portfolio (illustrative rates only).",
    )
    parser.add_argument("--input", type=Path, help="policies CSV to read; if left out, policies are generated")
    parser.add_argument("--policies", type=_positive_int, default=10_000, help="how many policies to generate (default 10000)")
    parser.add_argument("--seed", type=int, default=42, help="random seed for generated policies (default 42)")
    parser.add_argument("--inject-errors", action="store_true", help="plant six known errors in the policies")
    parser.add_argument("--current", type=Path, default=PROJECT_ROOT / "config" / "rates_current.yaml")
    parser.add_argument("--proposed", type=Path, default=PROJECT_ROOT / "config" / "rates_proposed.yaml")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "outputs", help="output folder (default outputs/)")
    parser.add_argument("--max-reject-rate", type=_share, default=0.05, help="stop if more than this share is rejected (default 0.05)")
    return parser.parse_args(argv)


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {value}")
    return value


def _share(text: str) -> float:
    value = float(text)
    if not 0 <= value <= 1:
        raise argparse.ArgumentTypeError(f"must be between 0 and 1, got {value}")
    return value


def run(args: argparse.Namespace) -> int:
    """Run every step and return an exit code."""
    timings: dict[str, float] = {}
    args.out.mkdir(parents=True, exist_ok=True)

    with timed("load rates", timings):
        current = load_rate_table(args.current)
        proposed = load_rate_table(args.proposed)

    with timed("load data", timings):
        if args.input:
            policies = pd.read_csv(args.input, dtype={"policy_id": str})
            logger.info("Read %d policies from %s", len(policies), args.input)
        else:
            policies = generate_policies(args.policies, current, seed=args.seed)
        if args.inject_errors:
            policies = inject_errors(policies)

    with timed("validate", timings):
        validation = validate_policies(policies, current)

    with timed("write rejected", timings):
        validation.rejected.to_csv(args.out / "rejected_policies.csv", index=False)

    if validation.reject_rate > args.max_reject_rate:
        logger.error(
            "Stopping: %.2f%% of policies rejected, above the %.2f%% limit. See %s",
            100 * validation.reject_rate, 100 * args.max_reject_rate, args.out / "rejected_policies.csv",
        )
        return EXIT_TOO_MANY_REJECTS

    with timed("price and compare", timings):
        results = compare_premiums(validation.valid, proposed)
    with timed("summarise", timings):
        summary = portfolio_summary(results)
    with timed("distribution", timings):
        distribution = change_distribution(results)
    with timed("SQL segments", timings):
        segments = segment_impact(results)
    with timed("reconcile", timings):
        checks = reconcile(results, summary, segments, len(validation.valid))  # logs PASS/FAIL per check

    with timed("write outputs", timings):
        results.to_csv(args.out / "policy_results.csv", index=False)
        segments.to_csv(args.out / "segment_impact.csv", index=False)
        chart_path = plot_distribution(results, args.out / CHART_NAME)

    report_path = write_report(
        args.out,
        current_version=current.version,
        proposed_version=proposed.version,
        validation=validation,
        summary=summary,
        distribution=distribution,
        segments=segments,
        checks=checks,
        timings=timings,
        chart_path=chart_path,
    )

    # %-style logging cannot add thousands separators, so build those texts first.
    change = format_pounds(summary["total_change"], sign=True)
    affected = f"{summary['policies_affected']:,}"
    logger.info(
        "Total change %s (%+.2f%%), %s policies affected. Report: %s",
        change, summary["total_change_pct"], affected, report_path,
    )

    if not all(check.passed for check in checks):
        logger.error("Reconciliation FAILED. Do not use these numbers until the failed checks are explained.")
        return EXIT_RECONCILIATION_FAILED
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    args = parse_args(argv)
    try:
        return run(args)
    except BAD_INPUT_ERRORS as exc:
        logger.error("Bad input or config: %s", exc)
        return EXIT_BAD_INPUT


if __name__ == "__main__":
    sys.exit(main())
