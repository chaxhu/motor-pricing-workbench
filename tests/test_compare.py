"""Tests for comparison, reconciliation and the full pipeline run.

Why this file exists: it checks the rate change moves exactly the policies
it should and no others, that reconciliation passes when the numbers add up
and fails when they do not, and that one command produces every output.
All rates are illustrative assumptions, not real Hastings Direct pricing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from workbench.compare import (
    Check,
    compare_premiums,
    explain_policy,
    largest_changes,
    portfolio_summary,
    reconcile,
    segment_impact,
)
from workbench.pipeline import EXIT_OK, EXIT_TOO_MANY_REJECTS, main
from workbench.rates import RateTable

EXPECTED_OUTPUTS = [
    "report.md",
    "summary.json",
    "policy_results.csv",
    "rejected_policies.csv",
    "segment_impact.csv",
    "pct_change_distribution.png",
]


def _compare(policies: pd.DataFrame, table: RateTable) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    """Run the comparison steps the pipeline runs, returning results, summary and segments."""
    results = compare_premiums(policies, table)
    return results, portfolio_summary(results), segment_impact(results)


def test_current_rates_change_nothing(policies: pd.DataFrame, current_rates: RateTable) -> None:
    results, summary, _ = _compare(policies, current_rates)

    assert summary["policies_affected"] == 0
    assert summary["total_change"] == 0
    assert (results["premium_change"] == 0).all()


def test_only_targeted_policies_change(policies: pd.DataFrame, proposed_rates: RateTable) -> None:
    results, _, _ = _compare(policies, proposed_rates)

    targeted = (
        results["vehicle_age"].between(8, 14)
        | (results["annual_mileage"] >= 15000)
        | (results["claims_last_5y"] == 1)
    )
    changed = results["premium_change"] != 0
    assert targeted.any() and (~targeted).any(), "test data should contain both groups"
    assert changed[targeted].all(), f"{int((~changed[targeted]).sum())} targeted policies did not change"
    assert not changed[~targeted].any(), f"{int(changed[~targeted].sum())} untargeted policies changed"


def test_reconciliation_passes(policies: pd.DataFrame, proposed_rates: RateTable) -> None:
    results, summary, segments = _compare(policies, proposed_rates)
    checks = reconcile(results, summary, segments, len(policies))

    assert len(checks) == 6
    assert all(c.passed for c in checks), _failures(checks)


def test_dropped_row_fails_reconciliation(policies: pd.DataFrame, proposed_rates: RateTable) -> None:
    results, summary, segments = _compare(policies, proposed_rates)
    checks = reconcile(results.iloc[1:], summary, segments, len(policies))

    failed = {c.name for c in checks if not c.passed}
    assert "row count preserved" in failed


def test_pipeline_end_to_end(tmp_path: Path) -> None:
    code = main(["--policies", "500", "--out", str(tmp_path), "--inject-errors"])

    assert code == EXIT_OK
    missing = [name for name in EXPECTED_OUTPUTS if not (tmp_path / name).is_file()]
    assert not missing, f"missing outputs: {missing}"


def test_pipeline_stops_when_too_many_rejected(tmp_path: Path) -> None:
    # 7 of 51 rows rejected (13.7%), above the default 5% limit.
    code = main(["--policies", "50", "--out", str(tmp_path), "--inject-errors"])

    assert code == EXIT_TOO_MANY_REJECTS
    assert (tmp_path / "rejected_policies.csv").is_file()
    assert not (tmp_path / "report.md").exists()


def _failures(checks: list[Check]) -> str:
    return "; ".join(f"{c.name}: {c.detail}" for c in checks if not c.passed)


def test_explain_policy_matches_hand_worked_premiums(
    one_policy: dict[str, Any], current_rates: RateTable, proposed_rates: RateTable
) -> None:
    table = explain_policy(pd.DataFrame([one_policy]), current_rates, proposed_rates).set_index("step")

    assert table.loc["vehicle_age", ["current", "proposed"]].tolist() == [0.95, 0.92]
    assert table.loc["annual_mileage", "value"] == "16,000"
    assert table.loc["premium", ["current", "proposed"]].tolist() == [1234.41, 1363.36]  # see test_pricing.py
    assert table.index[table["changed"]].tolist() == ["vehicle_age", "annual_mileage", "claims_last_5y", "premium"]


def test_largest_changes_picks_the_biggest_rise_and_fall(policies: pd.DataFrame, proposed_rates: RateTable) -> None:
    results = compare_premiums(policies, proposed_rates)
    moves = largest_changes(results)

    assert moves["rise"]["premium_change"] == results["premium_change"].max() > 0
    assert moves["fall"]["premium_change"] == results["premium_change"].min() < 0


def test_largest_changes_is_none_when_nothing_moves(policies: pd.DataFrame, current_rates: RateTable) -> None:
    moves = largest_changes(compare_premiums(policies, current_rates))
    assert moves == {"rise": None, "fall": None}
