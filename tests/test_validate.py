"""Tests for validation: every bad row is caught with the right reason, and no row is lost.

Why this file exists: validation is the guard in front of pricing. These tests
prove it lets clean data through untouched, catches each planted problem,
and keeps every row it turns away.
All rates are illustrative assumptions, not real Hastings Direct pricing.
"""

from __future__ import annotations

import pandas as pd
import pytest

from workbench.data import inject_errors
from workbench.rates import RateTable
from workbench.validate import SchemaError, validate_policies


def test_clean_data_has_no_rejections(policies: pd.DataFrame, current_rates: RateTable) -> None:
    result = validate_policies(policies, current_rates)
    assert len(result.rejected) == 0
    assert len(result.valid) == len(policies)
    assert result.reject_rate == 0.0


def test_all_planted_problems_are_caught(policies: pd.DataFrame, current_rates: RateTable) -> None:
    dirty = inject_errors(policies)
    result = validate_policies(dirty, current_rates)

    assert result.reason_counts().to_dict() == {
        "missing annual_mileage": 1,
        "annual_mileage out of range [1, 150000]": 1,
        "driving_experience exceeds driver_age - 17": 1,
        "current_premium does not match current rating table": 1,
        "driver_age out of range [17, 99]": 1,
        "duplicate policy_id": 2,
    }
    assert len(result.valid) + len(result.rejected) == len(dirty)


def test_duplicate_policy_id_rejects_both_copies(policies: pd.DataFrame, current_rates: RateTable) -> None:
    dirty = pd.concat([policies, policies.iloc[[5]]], ignore_index=True)
    result = validate_policies(dirty, current_rates)

    assert len(result.rejected) == 2
    assert (result.rejected["policy_id"] == policies.loc[5, "policy_id"]).all()
    assert (result.rejected["reasons"] == "duplicate policy_id").all()


def test_negative_premium_is_rejected(policies: pd.DataFrame, current_rates: RateTable) -> None:
    dirty = policies.copy()
    dirty.loc[0, "current_premium"] = -10.0
    result = validate_policies(dirty, current_rates)

    assert len(result.rejected) == 1
    assert result.rejected.loc[0, "reasons"] == "current_premium must be positive"


def test_missing_column_raises_schema_error(policies: pd.DataFrame, current_rates: RateTable) -> None:
    with pytest.raises(SchemaError, match="vehicle_age") as info:
        validate_policies(policies.drop(columns="vehicle_age"), current_rates)
    assert info.value.missing == ["vehicle_age"]


@pytest.mark.parametrize(("offset", "rejected"), [(0.005, False), (1.00, True)])
def test_premium_tolerance(
    offset: float, rejected: bool, policies: pd.DataFrame, current_rates: RateTable
) -> None:
    dirty = policies.copy()
    dirty.loc[0, "current_premium"] += offset
    result = validate_policies(dirty, current_rates)

    assert (dirty.loc[0, "policy_id"] in set(result.rejected["policy_id"])) == rejected
    if rejected:
        assert result.rejected.loc[0, "reasons"] == "current_premium does not match current rating table"
    else:
        assert len(result.rejected) == 0
