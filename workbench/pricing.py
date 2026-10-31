"""Turn policies into premiums, using two engines that must always agree.

Why this module exists: pricing is the heart of the workbench, so we want it
to be both fast and trustworthy. Those two goals pull in different directions,
so there are two engines:

- The vectorised engine (price_breakdown, price_portfolio) prices the whole
  portfolio at once with NumPy. It is fast, but the band lookup is clever
  enough that a bug could hide in it.
- The reference engine (price_one, price_row_by_row) prices one policy at a
  time with plain Python loops. It is slow, but simple enough to check by eye
  against the premium formula.

Tests price the same policies with both and require the answers to match
exactly, to the penny. If they ever differ, one of them is wrong.

Rounding note: the vectorised engine rounds with np.round and the reference
engine with Python's round. They can disagree on rare half-penny values. This
is a known difference that the engine comparison is meant to catch.

All rates and factors are illustrative assumptions, not real Hastings Direct
pricing.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from workbench.rates import RATING_FACTORS, RateTable

logger = logging.getLogger(__name__)

MAX_EXAMPLES = 5


class PricingError(ValueError):
    """Raised when a policy cannot be priced, for example a value outside every band."""


def factor_lookup(values: Any, table: RateTable, name: str) -> np.ndarray:
    """Return the factor for each value, using the bands for one rating factor.

    A value v falls in the band where lower <= v < upper. np.searchsorted with
    side="right" counts how many band edges are <= v, so subtracting 1 gives
    the band index. Values below the first edge get -1, and values at or above
    the last upper edge get len(bands), so both are caught as out of range.
    """
    x = np.asarray(values, dtype=float)
    edges = np.asarray(table.edges(name), dtype=float)
    factors = np.asarray(table.values(name), dtype=float)

    idx = np.searchsorted(edges, x, side="right") - 1
    bad = np.isnan(x) | (idx < 0) | (idx >= len(factors))
    if bad.any():
        examples = x[bad][:MAX_EXAMPLES].tolist()
        raise PricingError(
            f"{name}: {int(bad.sum())} value(s) outside every band "
            f"[{edges[0]:g}, {edges[-1]:g}), e.g. {examples}"
        )
    return factors[idx]


def price_breakdown(policies: pd.DataFrame, table: RateTable) -> pd.DataFrame:
    """Price every policy and show each factor used, so a premium can be explained.

    Returns policy_id, one f_<factor> column per rating factor, and premium.
    """
    _require_columns(policies)
    out = pd.DataFrame({"policy_id": policies["policy_id"]}, index=policies.index)

    premium = np.full(len(policies), table.base_premium, dtype=float)
    for name in RATING_FACTORS:
        factor = factor_lookup(policies[name], table, name)
        out[f"f_{name}"] = factor
        premium = premium * factor

    out["premium"] = np.round(premium, 2)
    logger.debug("Priced %d policies on '%s' rates (vectorised)", len(out), table.version)
    return out


def price_portfolio(policies: pd.DataFrame, table: RateTable) -> pd.Series:
    """Price every policy with the vectorised engine and return just the premiums."""
    return price_breakdown(policies, table)["premium"]


def price_one(policy: dict[str, Any], table: RateTable) -> float:
    """Price a single policy with plain Python. This is the reference engine.

    Written to read like the formula: start from the base premium and
    multiply by the matching band's factor for each rating factor in turn.
    """
    premium = table.base_premium
    for name in RATING_FACTORS:
        value = policy[name]
        for band in table.factors[name]:
            # NaN fails every comparison, so it never matches a band.
            if band.lower <= value < band.upper:
                premium = premium * band.factor
                break
        else:
            raise PricingError(
                f"{name}: value {value!r} outside every band "
                f"(policy_id={policy.get('policy_id')!r})"
            )
    return round(premium, 2)


def price_row_by_row(policies: pd.DataFrame, table: RateTable) -> pd.Series:
    """Price every policy one at a time with price_one.

    Slow on purpose. It is the baseline the vectorised engine is tested and
    benchmarked against, so keep it simple rather than fast.
    """
    _require_columns(policies)
    premiums = [price_one(policy, table) for policy in policies.to_dict("records")]
    logger.debug("Priced %d policies on '%s' rates (row by row)", len(premiums), table.version)
    return pd.Series(premiums, index=policies.index, name="premium", dtype=float)


def _require_columns(policies: pd.DataFrame) -> None:
    """Raise PricingError if policy_id or any rating factor column is missing."""
    missing = [col for col in ["policy_id", *RATING_FACTORS] if col not in policies.columns]
    if missing:
        raise PricingError(f"policies are missing columns {missing}")
