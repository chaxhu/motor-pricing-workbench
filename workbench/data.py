"""Make a synthetic motor portfolio, and plant known errors in it for testing.

Why this module exists: we cannot use real customer data, so we generate a
portfolio that looks believable (young drivers are rarer than middle aged
ones, most cars are a few years old, mileage is skewed with a long tail).
A fixed random seed means the same call always gives the same portfolio,
so pricing runs and tests can be repeated exactly.

inject_errors then plants six specific problems, so we can prove the
validation step finds every one of them and drops nothing silently.

The policies and their premiums are synthetic. The rates used are
illustrative assumptions, not real Hastings Direct pricing.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from workbench.pricing import price_portfolio
from workbench.rates import RateTable

logger = logging.getLogger(__name__)

POLICY_COLUMNS: list[str] = [
    "policy_id",
    "driver_age",
    "driving_experience",
    "vehicle_age",
    "annual_mileage",
    "claims_last_5y",
    "current_premium",
]

MIN_DRIVING_AGE = 17
N_INJECTED_ROWS = 6


def generate_policies(n: int, current_rates: RateTable, seed: int = 42) -> pd.DataFrame:
    """Return n synthetic policies priced on the current rates.

    The same n and seed always give exactly the same data. The draws happen
    in a fixed order, so changing one column's recipe changes the columns
    drawn after it.
    """
    if n <= 0:
        raise ValueError(f"n must be > 0, got {n}")
    rng = np.random.default_rng(seed)

    driver_age = _driver_age(rng, n)
    driving_experience = _driving_experience(rng, driver_age)
    policies = pd.DataFrame(
        {
            "policy_id": [f"POL{i:07d}" for i in range(1, n + 1)],
            "driver_age": driver_age,
            "driving_experience": driving_experience,
            "vehicle_age": _vehicle_age(rng, n),
            "annual_mileage": _annual_mileage(rng, n),
            "claims_last_5y": _claims_last_5y(rng, driving_experience),
        }
    )
    policies["current_premium"] = price_portfolio(policies, current_rates)
    logger.info("Generated %d policies with seed %d", n, seed)
    return policies[POLICY_COLUMNS]


def _driver_age(rng: np.random.Generator, n: int) -> np.ndarray:
    """Ages centred on mid 40s, kept between 17 and 90."""
    return np.clip(np.round(rng.normal(44, 14, n)), MIN_DRIVING_AGE, 90).astype(int)


def _driving_experience(rng: np.random.Generator, driver_age: np.ndarray) -> np.ndarray:
    """Years licensed: a random share of the years since 17, never more than possible."""
    share = rng.uniform(0.3, 1.0, len(driver_age))
    years = np.floor(share * (driver_age - MIN_DRIVING_AGE + 1)).astype(int)
    return np.minimum(years, driver_age - MIN_DRIVING_AGE)


def _vehicle_age(rng: np.random.Generator, n: int) -> np.ndarray:
    """Car age in years: mostly a few years old, with a tail of older cars."""
    return np.clip(np.round(rng.gamma(2.2, 3.2, n)), 0, 30).astype(int)


def _annual_mileage(rng: np.random.Generator, n: int) -> np.ndarray:
    """Miles a year: centred near 8,000, skewed, rounded to the nearest 100."""
    miles = np.clip(rng.lognormal(np.log(8000), 0.45, n), 1000, 40000)
    return (np.round(miles / 100) * 100).astype(int)


def _claims_last_5y(rng: np.random.Generator, driving_experience: np.ndarray) -> np.ndarray:
    """Claim counts: rare overall, more likely for drivers with under 3 years' experience."""
    rate = 0.18 + 0.25 * (driving_experience < 3)
    return rng.poisson(rate)


def inject_errors(df: pd.DataFrame, seed: int = 7) -> pd.DataFrame:
    """Return a copy of df with exactly six known problems planted in it.

    Each problem goes in a different, randomly chosen row:
      1. annual_mileage missing (NaN)
      2. annual_mileage of -5000
      3. driving_experience equal to driver_age (impossible)
      4. current_premium x 1.5 (no longer matches the rates)
      5. driver_age of 15, with driving_experience set to 0 so the age is
         the only thing wrong in that row
      6. one row duplicated, so its policy_id appears twice

    The input is not changed. The duplicate is added at the end and the
    index is reset, so the result has len(df) + 1 rows.
    """
    if len(df) < N_INJECTED_ROWS:
        raise ValueError(f"need at least {N_INJECTED_ROWS} rows to inject errors, got {len(df)}")
    rng = np.random.default_rng(seed)
    out = df.copy()
    out["annual_mileage"] = out["annual_mileage"].astype(float)
    out["current_premium"] = out["current_premium"].astype(float)

    rows = rng.choice(out.index.to_numpy(), size=N_INJECTED_ROWS, replace=False)
    missing_mileage, negative_mileage, impossible_exp, wrong_premium, underage, duplicate = rows

    out.loc[missing_mileage, "annual_mileage"] = np.nan
    out.loc[negative_mileage, "annual_mileage"] = -5000.0
    out.loc[impossible_exp, "driving_experience"] = out.loc[impossible_exp, "driver_age"]
    out.loc[wrong_premium, "current_premium"] = out.loc[wrong_premium, "current_premium"] * 1.5
    out.loc[underage, ["driver_age", "driving_experience"]] = [15, 0]
    out = pd.concat([out, out.loc[[duplicate]]], ignore_index=True)

    logger.info(
        "Injected errors: missing mileage %s, negative mileage %s, impossible experience %s, "
        "wrong premium %s, underage %s, duplicate %s",
        *(df.loc[row, "policy_id"] for row in rows),
    )
    return out
