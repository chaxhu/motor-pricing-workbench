"""Tests for run_evaluation, the shared core of the CLI and the Streamlit app.

Why this file exists: the app and the command line must always show the
same numbers. These tests run both on the same policies and require the
summary, checks, segments and rejected rows to match exactly.
All rates are illustrative assumptions, not real Hastings Direct pricing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from workbench.compare import Check
from workbench.data import generate_policies, inject_errors
from workbench.pipeline import EXIT_OK, main, run_evaluation
from workbench.rates import RateTable
from workbench.report import CHART_NAME, render_report, write_report


def test_run_evaluation_matches_cli(tmp_path: Path, current_rates: RateTable, proposed_rates: RateTable) -> None:
    assert main(["--policies", "500", "--seed", "42", "--out", str(tmp_path), "--inject-errors"]) == EXIT_OK
    cli = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))

    policies = inject_errors(generate_policies(500, current_rates, seed=42))
    evaluation = run_evaluation(policies, current_rates, proposed_rates)

    assert not evaluation.stopped
    assert evaluation.summary == cli["summary"]
    assert evaluation.checks == [Check(**c) for c in cli["checks"]]
    assert evaluation.reconciliation_passed == cli["reconciliation_passed"]
    pd.testing.assert_frame_equal(
        evaluation.segments, pd.read_csv(tmp_path / "segment_impact.csv"), check_exact=True
    )
    cli_rejected = pd.read_csv(tmp_path / "rejected_policies.csv", dtype={"policy_id": str})
    # Compare as lists: reading the CSV back gives a string dtype where memory has object.
    columns = ["policy_id", "reasons"]
    assert evaluation.validation.rejected[columns].values.tolist() == cli_rejected[columns].values.tolist()


def test_run_evaluation_stops_when_too_many_rejected(current_rates: RateTable, proposed_rates: RateTable) -> None:
    # 7 of 51 rows rejected (13.7%), above the default 5% limit.
    policies = inject_errors(generate_policies(50, current_rates, seed=42))
    evaluation = run_evaluation(policies, current_rates, proposed_rates)

    assert evaluation.stopped
    assert not evaluation.reconciliation_passed
    assert len(evaluation.validation.rejected) == 7
    assert evaluation.results is None and evaluation.summary is None and evaluation.checks == []


def test_render_report_matches_written_report(tmp_path: Path, current_rates: RateTable, proposed_rates: RateTable) -> None:
    evaluation = run_evaluation(generate_policies(500, current_rates, seed=42), current_rates, proposed_rates)
    parts = dict(
        current_version=current_rates.version,
        proposed_version=proposed_rates.version,
        validation=evaluation.validation,
        summary=evaluation.summary,
        distribution=evaluation.distribution,
        segments=evaluation.segments,
        checks=evaluation.checks,
        timings=evaluation.timings,
    )
    written = write_report(tmp_path, **parts, chart_path=tmp_path / CHART_NAME).read_text(encoding="utf-8")
    rendered = render_report(**parts, chart_link=CHART_NAME)

    def without_run_time(text: str) -> list[str]:
        return [line for line in text.splitlines() if not line.startswith("Run at ")]

    assert without_run_time(rendered) == without_run_time(written)
