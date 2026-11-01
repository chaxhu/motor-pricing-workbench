"""Run the whole workbench from the command line: python -m workbench.pipeline

Why this module exists: it joins every step into one repeatable run, so a
rate change can be evaluated with a single command and the same inputs
always give the same report. The steps are: load rates, load or generate
policies, validate, compare current and proposed premiums, summarise,
reconcile, and write the outputs.

run_evaluation does the validate, compare, summarise and reconcile steps
and returns everything in one EvaluationResult, without writing any files.
The command line run and the Streamlit app both call it, so they always
give the same numbers.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Iterator

import pandas as pd

from workbench.compare import (
    Check,
    change_distribution,
    compare_premiums,
    portfolio_summary,
    reconcile,
    segment_impact,
)
from workbench.data import generate_policies, inject_errors
from workbench.pricing import PricingError
from workbench.rates import RateTable, RateTableError, load_rate_table
from workbench.report import CHART_NAME, format_pounds, plot_distribution, write_report
from workbench.validate import SchemaError, ValidationResult, validate_policies

logger = logging.getLogger("workbench.pipeline")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CURRENT_RATES = PROJECT_ROOT / "config" / "rates_current.yaml"
DEFAULT_PROPOSED_RATES = PROJECT_ROOT / "config" / "rates_proposed.yaml"
DEFAULT_MAX_REJECT_RATE = 0.05

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
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT_RATES)
    parser.add_argument("--proposed", type=Path, default=DEFAULT_PROPOSED_RATES)
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "outputs", help="output folder (default outputs/)")
    parser.add_argument("--max-reject-rate", type=_share, default=DEFAULT_MAX_REJECT_RATE, help="stop if more than this share is rejected (default 0.05)")
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


def read_policies(source: str | Path | IO) -> pd.DataFrame:
    """Read a policies CSV from a path or an open file. policy_id is kept as text, so IDs like 007 survive."""
    return pd.read_csv(source, dtype={"policy_id": str})


@dataclass
class EvaluationResult:
    """Everything one evaluation produces.

    If too many rows were rejected, stopped is True and only validation and
    timings are filled in. The other fields stay None or empty, because
    nothing was priced.
    """

    validation: ValidationResult
    max_reject_rate: float
    stopped: bool
    results: pd.DataFrame | None = None
    summary: dict[str, Any] | None = None
    distribution: pd.DataFrame | None = None
    segments: pd.DataFrame | None = None
    checks: list[Check] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def reconciliation_passed(self) -> bool:
        """True only if the run was not stopped and every check passed."""
        return not self.stopped and all(check.passed for check in self.checks)


def run_evaluation(
    policies: pd.DataFrame,
    current: RateTable,
    proposed: RateTable,
    max_reject_rate: float = DEFAULT_MAX_REJECT_RATE,
) -> EvaluationResult:
    """Validate, price, compare, summarise and reconcile, without writing any files.

    Stops after validation if more than max_reject_rate of the rows are rejected.
    """
    timings: dict[str, float] = {}
    with timed("validate", timings):
        validation = validate_policies(policies, current)

    if validation.reject_rate > max_reject_rate:
        return EvaluationResult(validation, max_reject_rate, stopped=True, timings=timings)

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

    return EvaluationResult(
        validation, max_reject_rate, stopped=False,
        results=results, summary=summary, distribution=distribution,
        segments=segments, checks=checks, timings=timings,
    )


def run(args: argparse.Namespace) -> int:
    """Run every step and return an exit code."""
    timings: dict[str, float] = {}
    args.out.mkdir(parents=True, exist_ok=True)

    with timed("load rates", timings):
        current = load_rate_table(args.current)
        proposed = load_rate_table(args.proposed)

    with timed("load data", timings):
        if args.input:
            policies = read_policies(args.input)
            logger.info("Read %d policies from %s", len(policies), args.input)
        else:
            policies = generate_policies(args.policies, current, seed=args.seed)
        if args.inject_errors:
            policies = inject_errors(policies)

    evaluation = run_evaluation(policies, current, proposed, args.max_reject_rate)
    validation = evaluation.validation
    # Add "validate" now so the report lists it before "write rejected", as it always has.
    timings["validate"] = evaluation.timings["validate"]

    with timed("write rejected", timings):
        validation.rejected.to_csv(args.out / "rejected_policies.csv", index=False)

    if evaluation.stopped:
        logger.error(
            "Stopping: %.2f%% of policies rejected, above the %.2f%% limit. See %s",
            100 * validation.reject_rate, 100 * args.max_reject_rate, args.out / "rejected_policies.csv",
        )
        return EXIT_TOO_MANY_REJECTS

    # update() keeps "validate" where it already is and adds the later steps after it.
    timings.update(evaluation.timings)
    results, summary, segments = evaluation.results, evaluation.summary, evaluation.segments

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
        distribution=evaluation.distribution,
        segments=segments,
        checks=evaluation.checks,
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

    if not evaluation.reconciliation_passed:
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
