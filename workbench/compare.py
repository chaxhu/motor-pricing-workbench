"""Compare current and proposed premiums, then check the answer adds up.

Why this module exists: comparing answers "what changes?" for each policy,
for the portfolio, and for each customer segment. Reconciling answers "can
we trust the answer?". It works like balancing a till at the end of the day:
the same money is counted in different ways (policy by policy, as a
portfolio total, and by segment in SQL), and every count must agree before
the numbers go in a report.

All rates and factors are illustrative assumptions, not real Hastings
Direct pricing.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from workbench.pricing import price_breakdown, price_portfolio, round_pence
from workbench.rates import RATING_FACTORS, RateTable

logger = logging.getLogger(__name__)

SQL_PATH = Path(__file__).resolve().parent.parent / "sql" / "impact_by_segment.sql"

AGE_BINS = [17, 25, 35, 60, 75, 121]
AGE_LABELS = ["17-24", "25-34", "35-59", "60-74", "75+"]
MILEAGE_BINS = [0, 5000, 10000, 15000, 1_000_000]
MILEAGE_LABELS = ["<5k", "5k-10k", "10k-15k", "15k+"]
# pct_change is in percent, so 5 means 5%.
CHANGE_BINS = [-np.inf, -5, -0.005, 0.005, 5, 10, np.inf]
CHANGE_LABELS = ["down >5%", "down 0-5%", "no change", "up 0-5%", "up 5-10%", "up >10%"]

SEGMENT_COLUMNS = [
    "age_band", "mileage_band", "direction",
    "current_premium", "proposed_premium", "premium_change", "pct_change",
]

PENNY = 0.01
# Allows for floating point noise when a gap is exactly one penny.
FLOAT_SLACK = 1e-9


# ---------------------------------------------------------------- compare

def compare_premiums(policies: pd.DataFrame, proposed: RateTable) -> pd.DataFrame:
    """Price valid policies on the proposed rates and describe each policy's change.

    Expects validated policies, whose current_premium already matches the
    current rates. Adds proposed_premium, premium_change, pct_change,
    direction, age_band, mileage_band and change_bucket.
    """
    out = policies.copy()
    out["proposed_premium"] = price_portfolio(out, proposed)
    out["premium_change"] = round_pence(out["proposed_premium"] - out["current_premium"])
    out["pct_change"] = np.round(out["premium_change"] / out["current_premium"] * 100, 4)
    out["direction"] = np.select(
        [out["premium_change"] > 0, out["premium_change"] < 0, out["premium_change"] == 0],
        ["increase", "decrease", "no change"],
        default=None,  # only if premium_change is missing; reconcile() catches it
    )
    out["age_band"] = pd.cut(out["driver_age"], AGE_BINS, labels=AGE_LABELS, right=False)
    out["mileage_band"] = pd.cut(out["annual_mileage"], MILEAGE_BINS, labels=MILEAGE_LABELS, right=False)
    out["change_bucket"] = pd.cut(out["pct_change"], CHANGE_BINS, labels=CHANGE_LABELS)
    logger.info("Compared %d policies on '%s' rates", len(out), proposed.version)
    return out


def portfolio_summary(results: pd.DataFrame) -> dict[str, Any]:
    """Headline numbers for the whole portfolio.

    Averages and maximums over an empty group (for example no decreases) are 0.0.
    total_change is worked out from the two totals, not by adding up the
    policy changes, so reconcile() can compare the two routes.
    """
    change = results["premium_change"]
    pct = results["pct_change"]
    affected = results["direction"] != "no change"
    current_total = _money(results["current_premium"].sum())
    proposed_total = _money(results["proposed_premium"].sum())
    total_change = _money(proposed_total - current_total)

    return {
        "policies": len(results),
        "policies_affected": int(affected.sum()),
        "increases": int((results["direction"] == "increase").sum()),
        "decreases": int((results["direction"] == "decrease").sum()),
        "current_total": current_total,
        "proposed_total": proposed_total,
        "total_change": total_change,
        "total_change_pct": _pct(total_change / current_total * 100) if current_total else 0.0,
        "avg_change_all": _money(change.mean()) if len(results) else 0.0,
        "avg_change_affected": _money(change[affected].mean()) if affected.any() else 0.0,
        "avg_pct_change_affected": _pct(pct[affected].mean()) if affected.any() else 0.0,
        "max_increase_pct": _pct(pct[pct > 0].max()) if (pct > 0).any() else 0.0,
        "max_decrease_pct": _pct(pct[pct < 0].min()) if (pct < 0).any() else 0.0,
    }


def change_distribution(results: pd.DataFrame) -> pd.DataFrame:
    """Count and share of policies in each change bucket, in bucket order, including empty buckets."""
    counts = results["change_bucket"].value_counts(sort=False).reindex(CHANGE_LABELS, fill_value=0)
    total = len(results)
    return pd.DataFrame(
        {
            "change_bucket": CHANGE_LABELS,
            "count": counts.to_numpy(),
            "share": np.round(counts.to_numpy() / total, 4) if total else 0.0,
        }
    )


def segment_impact(results: pd.DataFrame) -> pd.DataFrame:
    """Run sql/impact_by_segment.sql over the results in an in-memory SQLite database."""
    table = results[SEGMENT_COLUMNS].copy()
    for col in ["age_band", "mileage_band"]:
        table[col] = table[col].astype(str)

    query = SQL_PATH.read_text(encoding="utf-8")
    with closing(sqlite3.connect(":memory:")) as conn:
        table.to_sql("results", conn, index=False)
        return pd.read_sql_query(query, conn)


def largest_changes(results: pd.DataFrame) -> dict[str, pd.Series | None]:
    """The policy with the largest rise and the one with the largest fall, in pounds.

    Pounds rather than percent, because only a few distinct % changes exist,
    so many policies tie on the largest %. Any tie goes to the lowest
    policy_id, so the answer is always the same. A value is None when no
    policy rises (or falls).
    """
    rises = results[results["premium_change"] > 0]
    falls = results[results["premium_change"] < 0]
    return {
        "rise": _first(rises.sort_values(["premium_change", "policy_id"], ascending=[False, True])),
        "fall": _first(falls.sort_values(["premium_change", "policy_id"], ascending=[True, True])),
    }


def _first(df: pd.DataFrame) -> pd.Series | None:
    return None if df.empty else df.iloc[0]


def explain_policy(policy: pd.DataFrame, current: RateTable, proposed: RateTable) -> pd.DataFrame:
    """Show how one policy's premium is built on each rate table, factor by factor.

    policy is a one-row DataFrame. Uses price_breakdown, so the factors and
    premiums are exactly the ones pricing used. Rows: the base premium, one
    per rating factor, then the final premium. Columns: step, value (the
    policy's own value for that factor), current, proposed and changed.
    """
    if len(policy) != 1:
        raise ValueError(f"expected one policy, got {len(policy)} rows")
    cur = price_breakdown(policy, current).iloc[0]
    prop = price_breakdown(policy, proposed).iloc[0]

    rows = [("base premium", "", current.base_premium, proposed.base_premium)]
    rows += [
        (name, f"{policy[name].iloc[0]:,}", cur[f"f_{name}"], prop[f"f_{name}"])
        for name in RATING_FACTORS
    ]
    rows += [("premium", "", cur["premium"], prop["premium"])]
    out = pd.DataFrame(rows, columns=["step", "value", "current", "proposed"])
    out["changed"] = out["current"] != out["proposed"]
    return out


def _money(value: float) -> float:
    """Round a money amount with the same half-up rule as pricing."""
    return float(round_pence(value))


def _pct(value: float) -> float:
    """Round a percentage to 4 decimal places."""
    return float(np.round(value, 4))


# -------------------------------------------------------------- reconcile

@dataclass(frozen=True)
class Check:
    """One reconciliation check: what was checked, whether it passed, and the numbers behind it."""

    name: str
    passed: bool
    detail: str


def reconcile(
    results: pd.DataFrame,
    summary: dict[str, Any],
    segments: pd.DataFrame,
    n_valid_input: int,
) -> list[Check]:
    """Run six checks that the comparison adds up. Returns them all, passed or not."""
    checks = [
        _check_row_count(results, summary, n_valid_input),
        _check_policy_changes_sum(results, summary),
        _check_segment_totals(segments, summary),
        _check_segment_counts(segments, summary),
        _check_directions(results),
        _check_proposed_premiums(results),
    ]
    for check in checks:
        level = logging.INFO if check.passed else logging.WARNING
        logger.log(level, "%s: %s (%s)", "PASS" if check.passed else "FAIL", check.name, check.detail)
    return checks


def _within(a: float, b: float, allowed: float) -> bool:
    """True if a and b differ by no more than allowed."""
    return abs(a - b) <= allowed + FLOAT_SLACK


def _check_row_count(results: pd.DataFrame, summary: dict[str, Any], n_valid_input: int) -> Check:
    rows, in_summary = len(results), summary["policies"]
    return Check(
        "row count preserved",
        rows == n_valid_input == in_summary,
        f"valid input {n_valid_input}, results {rows}, summary {in_summary}",
    )


def _check_policy_changes_sum(results: pd.DataFrame, summary: dict[str, Any]) -> Check:
    added_up = float(results["premium_change"].sum())
    return Check(
        "sum of policy changes equals portfolio change",
        _within(added_up, summary["total_change"], PENNY),
        f"policy changes add up to {added_up:.2f}, portfolio change {summary['total_change']:.2f}, allowed 0.01",
    )


def _check_segment_totals(segments: pd.DataFrame, summary: dict[str, Any]) -> Check:
    # Each segment total is rounded to 2dp in SQL, so allow 1p per segment.
    allowed = PENNY * len(segments)
    # "+ 0.0" turns -0.0 into 0.0, so a tiny float gap never prints as "-0.00".
    gaps = {
        col: round(float(segments[col].sum()) - summary[col], 6) + 0.0
        for col in ["current_total", "proposed_total", "total_change"]
    }
    return Check(
        "SQL segment totals equal pandas totals",
        all(abs(gap) <= allowed + FLOAT_SLACK for gap in gaps.values()),
        ", ".join(f"{col} gap {gap:+.2f}" for col, gap in gaps.items())
        + f", allowed {allowed:.2f} ({len(segments)} segments)",
    )


def _check_segment_counts(segments: pd.DataFrame, summary: dict[str, Any]) -> Check:
    in_segments = int(segments["policies"].sum())
    return Check(
        "segment policy counts add up to the total",
        in_segments == summary["policies"],
        f"segments {in_segments}, total {summary['policies']}",
    )


def _check_directions(results: pd.DataFrame) -> Check:
    counts = results["direction"].value_counts()
    up, down, same = (int(counts.get(d, 0)) for d in ["increase", "decrease", "no change"])
    return Check(
        "increases + decreases + unchanged = total",
        up + down + same == len(results),
        f"{up} + {down} + {same} = {up + down + same}, total {len(results)}",
    )


def _check_proposed_premiums(results: pd.DataFrame) -> Check:
    proposed = results["proposed_premium"]
    missing = int(proposed.isna().sum())
    not_positive = int((proposed <= 0).sum())
    return Check(
        "no missing or non-positive proposed premiums",
        missing == 0 and not_positive == 0,
        f"{missing} missing, {not_positive} zero or negative",
    )
