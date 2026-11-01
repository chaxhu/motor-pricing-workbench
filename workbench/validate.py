"""Check every policy before it may be priced, and record why any are turned away.

Why this module exists: think of it as passport control. Every policy is
checked at the border before it reaches pricing. Policies that pass go
through. Policies that fail are kept in a separate list with every reason
they failed, so nothing is ever dropped silently and the report can show
exactly what was set aside and why.

Without this step, some bad rows crash pricing (a missing mileage), and
others are priced wrongly without any warning (a duplicated policy counted
twice, or a stored premium that no longer matches the rates).

The rates used for reconciliation are illustrative assumptions, not real
Hastings Direct pricing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from workbench.data import POLICY_COLUMNS
from workbench.pricing import price_portfolio
from workbench.rates import RateTable

logger = logging.getLogger(__name__)

# Inclusive limits for each rating input. driver_age stops at 99 because the
# rate table's top age band is [75, 100), and driving_experience stops at
# 99 - 17 = 82 to match. Everything that passes here can be priced.
RANGES: dict[str, tuple[int, int]] = {
    "driver_age": (17, 99),
    "driving_experience": (0, 82),
    "vehicle_age": (0, 50),
    "annual_mileage": (1, 150_000),
    "claims_last_5y": (0, 20),
}
INTEGER_COLUMNS: list[str] = list(RANGES)

# How far a stored premium may be from the re-priced premium, in pounds.
PREMIUM_TOLERANCE = 0.01

REASON_SEPARATOR = "; "


class SchemaError(ValueError):
    """Raised when required columns are missing. The whole file is unusable, so this is fatal."""


@dataclass
class ValidationResult:
    """The outcome of validation: policies that passed, and policies turned away with reasons."""

    valid: pd.DataFrame
    rejected: pd.DataFrame

    @property
    def reject_rate(self) -> float:
        """Share of all policies that were rejected, between 0 and 1."""
        total = len(self.valid) + len(self.rejected)
        return len(self.rejected) / total if total else 0.0

    def reason_counts(self) -> pd.Series:
        """How often each reason occurred. A row with two reasons counts once for each."""
        reasons = self.rejected["reasons"].str.split(REASON_SEPARATOR).explode()
        return reasons.value_counts().rename("count")


def validate_policies(df: pd.DataFrame, current_rates: RateTable) -> ValidationResult:
    """Check every policy and split them into valid and rejected.

    Every reason a row fails is recorded, not just the first. Rows that pass
    all the data checks are then re-priced on the current rates, and any
    whose stored premium does not match are rejected too.
    """
    _check_schema(df)
    original = df.reset_index(drop=True)
    numeric = _coerce_numbers(original)
    reasons = pd.Series("", index=original.index, dtype=object)

    _check_missing(original, reasons)
    _check_duplicates(original, reasons)
    passed = {col: _check_range(original, numeric, col, reasons) for col in INTEGER_COLUMNS}
    _check_premium(original, numeric, reasons)
    _check_experience(numeric, passed, reasons)
    _check_premium_matches_rates(numeric, current_rates, reasons)

    result = _split(original, numeric, reasons)
    logger.info(
        "Validated %d policies: %d valid, %d rejected (%.2f%%)",
        len(original), len(result.valid), len(result.rejected), 100 * result.reject_rate,
    )
    return result


def _check_schema(df: pd.DataFrame) -> None:
    """Raise SchemaError if any required column is missing."""
    missing = [col for col in POLICY_COLUMNS if col not in df.columns]
    if missing:
        raise SchemaError(f"policies are missing required columns {missing}")


def _coerce_numbers(original: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with the numeric columns as numbers. Anything unreadable becomes NaN."""
    numeric = original.copy()
    for col in [*INTEGER_COLUMNS, "current_premium"]:
        numeric[col] = pd.to_numeric(original[col], errors="coerce")
    return numeric


def _add_reason(reasons: pd.Series, mask: pd.Series, text: str) -> None:
    """Append a reason to every row where mask is True."""
    reasons.loc[mask] = reasons.loc[mask] + text + REASON_SEPARATOR


def _check_missing(original: pd.DataFrame, reasons: pd.Series) -> None:
    """Flag every empty value."""
    for col in POLICY_COLUMNS:
        _add_reason(reasons, original[col].isna(), f"missing {col}")


def _check_duplicates(original: pd.DataFrame, reasons: pd.Series) -> None:
    """Flag every copy of a repeated policy_id, since we cannot tell which copy is right."""
    ids = original["policy_id"]
    _add_reason(reasons, ids.notna() & ids.duplicated(keep=False), "duplicate policy_id")


def _check_range(
    original: pd.DataFrame, numeric: pd.DataFrame, col: str, reasons: pd.Series
) -> pd.Series:
    """Flag values that are not numbers, out of range, or not whole numbers.

    Returns a mask of rows where this column passed, for checks that compare columns.
    """
    lo, hi = RANGES[col]
    present = original[col].notna()
    value = numeric[col]
    is_number = value.notna()
    in_range = value.between(lo, hi)
    whole = value % 1 == 0

    _add_reason(reasons, present & ~is_number, f"{col} is not a number")
    _add_reason(reasons, is_number & ~in_range, f"{col} out of range [{lo}, {hi}]")
    # A later cast to int would quietly turn 30.5 into 30, so catch it here.
    _add_reason(reasons, in_range & ~whole, f"{col} must be a whole number")
    return in_range & whole


def _check_premium(original: pd.DataFrame, numeric: pd.DataFrame, reasons: pd.Series) -> None:
    """Flag stored premiums that are not numbers or are not positive."""
    premium = numeric["current_premium"]
    _add_reason(reasons, original["current_premium"].notna() & premium.isna(), "current_premium is not a number")
    _add_reason(reasons, premium <= 0, "current_premium must be positive")


def _check_experience(numeric: pd.DataFrame, passed: dict[str, pd.Series], reasons: pd.Series) -> None:
    """Flag drivers licensed for longer than possible since age 17.

    Only checked where both ages are valid, so one bad driver_age gives one reason, not two.
    """
    both_ok = passed["driver_age"] & passed["driving_experience"]
    too_long = numeric["driving_experience"] > numeric["driver_age"] - 17
    _add_reason(reasons, both_ok & too_long, "driving_experience exceeds driver_age - 17")


def _check_premium_matches_rates(numeric: pd.DataFrame, current_rates: RateTable, reasons: pd.Series) -> None:
    """Re-price the rows that passed every other check and flag stored premiums that differ."""
    clean = reasons == ""
    repriced = price_portfolio(numeric.loc[clean], current_rates)
    gap = (repriced - numeric.loc[clean, "current_premium"]).abs()
    mismatch = pd.Series(False, index=numeric.index)
    mismatch.loc[gap.index] = gap > PREMIUM_TOLERANCE
    _add_reason(reasons, mismatch, "current_premium does not match current rating table")


def _split(original: pd.DataFrame, numeric: pd.DataFrame, reasons: pd.Series) -> ValidationResult:
    """Build the valid and rejected tables, both with a fresh 0..n-1 index."""
    bad = reasons != ""

    rejected = original.loc[bad].copy()
    rejected["reasons"] = reasons.loc[bad].str.removesuffix(REASON_SEPARATOR)

    valid = numeric.loc[~bad].copy()
    valid[INTEGER_COLUMNS] = valid[INTEGER_COLUMNS].astype("int64")

    return ValidationResult(
        valid=valid.reset_index(drop=True),
        rejected=rejected.reset_index(drop=True),
    )
