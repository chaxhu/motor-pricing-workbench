"""Shared test fixtures: the two rate tables, a synthetic portfolio and one hand-checked policy.

Why this file exists: several test files need the same inputs. Building them
once here keeps the tests short and makes sure they all use the same data.
All rates are illustrative assumptions, not real Hastings Direct pricing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from workbench.data import generate_policies
from workbench.rates import RateTable, load_rate_table

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@pytest.fixture(scope="session")
def current_rates() -> RateTable:
    return load_rate_table(CONFIG_DIR / "rates_current.yaml")


@pytest.fixture(scope="session")
def proposed_rates() -> RateTable:
    return load_rate_table(CONFIG_DIR / "rates_proposed.yaml")


@pytest.fixture
def policies(current_rates: RateTable) -> pd.DataFrame:
    return generate_policies(2000, current_rates, seed=1)


@pytest.fixture
def one_policy() -> dict[str, Any]:
    """A young, high mileage driver with one claim, priced by hand in test_pricing.py."""
    return {
        "policy_id": "POL0000001",
        "driver_age": 22,
        "driving_experience": 3,
        "vehicle_age": 10,
        "annual_mileage": 16000,
        "claims_last_5y": 1,
    }
