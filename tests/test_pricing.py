"""Tests for the pricing engines and the rate table checks.

Why this file exists: it pins the premium formula to answers worked out by
hand, checks the band edges, and proves the fast and slow engines agree.
All rates are illustrative assumptions, not real Hastings Direct pricing.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from workbench.pricing import PricingError, price_one, price_portfolio, price_row_by_row, round_pence
from workbench.rates import Band, RateTable, RateTableError, check_rate_table, parse_rate_table, rate_changes
from tests.conftest import CONFIG_DIR

# A policy where every factor except driver_age is 1.00 on the current rates,
# so the premium is exactly 420 x the driver_age factor.
NEUTRAL_POLICY: dict[str, Any] = {
    "policy_id": "POL0000002",
    "driver_age": 40,
    "driving_experience": 10,
    "vehicle_age": 5,
    "annual_mileage": 8000,
    "claims_last_5y": 0,
}


def test_one_policy_current_premium(one_policy: dict[str, Any], current_rates: RateTable) -> None:
    # 420 x 1.80 x 0.95 x 1.25 x 1.10 x 1.25 = 1234.40625
    assert price_one(one_policy, current_rates) == 1234.41
    assert price_portfolio(pd.DataFrame([one_policy]), current_rates).iloc[0] == 1234.41


def test_one_policy_proposed_premium(one_policy: dict[str, Any], proposed_rates: RateTable) -> None:
    # 420 x 1.80 x 0.92 x 1.35 x 1.10 x 1.32 = 1363.358304
    assert price_one(one_policy, proposed_rates) == 1363.36
    assert price_portfolio(pd.DataFrame([one_policy]), proposed_rates).iloc[0] == 1363.36


def test_round_pence_rounds_halves_up() -> None:
    # Python's round(2.675, 2) gives 2.67, because 2.675 is stored as 2.67499999...
    assert round_pence(2.675) == 2.68
    assert round_pence(2.674) == 2.67


@pytest.mark.parametrize("rates_fixture", ["current_rates", "proposed_rates"])
def test_engines_agree(policies: pd.DataFrame, rates_fixture: str, request: pytest.FixtureRequest) -> None:
    table = request.getfixturevalue(rates_fixture)
    fast = price_portfolio(policies, table).to_numpy()
    slow = price_row_by_row(policies, table).to_numpy()

    differ = np.abs(fast - slow) > 1e-9
    examples = [
        f"{pid}: vectorised {f!r}, row-by-row {s!r}"
        for pid, f, s in zip(policies["policy_id"][differ], fast[differ], slow[differ])
    ][:5]
    assert not differ.any(), (
        f"{int(differ.sum())} of {len(policies)} policies differ on '{table.version}' rates, "
        f"e.g. {examples}"
    )


@pytest.mark.parametrize(
    ("age", "factor"),
    [(17, 1.80), (24, 1.80), (25, 1.20), (59, 1.00), (60, 1.05)],
)
def test_driver_age_band_edges(age: int, factor: float, current_rates: RateTable) -> None:
    baseline = price_one(NEUTRAL_POLICY, current_rates)
    assert baseline == 420.00  # the 40 year old: every factor is 1.00

    policy = {**NEUTRAL_POLICY, "driver_age": age}
    expected = round(baseline * factor, 2)
    assert price_one(policy, current_rates) == expected
    assert price_portfolio(pd.DataFrame([policy]), current_rates).iloc[0] == expected


def test_age_outside_every_band_raises(one_policy: dict[str, Any], current_rates: RateTable) -> None:
    policy = {**one_policy, "driver_age": 150}
    with pytest.raises(PricingError, match="driver_age"):
        price_one(policy, current_rates)
    with pytest.raises(PricingError, match="driver_age"):
        price_portfolio(pd.DataFrame([policy]), current_rates)


def test_gap_between_bands_raises(current_rates: RateTable) -> None:
    gappy = (Band(0, 3, 1.15), Band(4, 60, 1.00))  # nothing covers 3 to 4
    table = replace(current_rates, factors={**current_rates.factors, "vehicle_age": gappy})
    with pytest.raises(RateTableError, match="gap"):
        check_rate_table(table)


def test_negative_factor_raises(current_rates: RateTable) -> None:
    bands = list(current_rates.factors["claims_last_5y"])
    bands[1] = replace(bands[1], factor=-1.25)
    table = replace(current_rates, factors={**current_rates.factors, "claims_last_5y": tuple(bands)})
    with pytest.raises(RateTableError, match="factor must be > 0"):
        check_rate_table(table)


def test_parse_rate_table_matches_file_and_runs_the_checks(proposed_rates: RateTable) -> None:
    text = (CONFIG_DIR / "rates_proposed.yaml").read_text(encoding="utf-8")
    assert parse_rate_table(text) == proposed_rates

    gappy = text.replace("{lower: 3, upper: 8,", "{lower: 4, upper: 8,")  # vehicle_age now skips 3 to 4
    with pytest.raises(RateTableError, match="gap between 3.0 and 4.0"):
        parse_rate_table(gappy, source="upload.yaml")


def test_rate_changes_finds_exactly_the_changed_bands(current_rates: RateTable, proposed_rates: RateTable) -> None:
    table = rate_changes(current_rates, proposed_rates)
    changed = table[table["changed"]]

    assert list(zip(changed["factor"], changed["current"], changed["proposed"])) == [
        ("vehicle_age", 0.95, 0.92),
        ("annual_mileage", 1.25, 1.35),
        ("claims_last_5y", 1.25, 1.32),
    ]
    assert not rate_changes(current_rates, current_rates)["changed"].any()


def test_rate_changes_shows_a_split_band_on_both_sides(current_rates: RateTable) -> None:
    split = (Band(0, 3, 1.15), Band(3, 5, 1.00), Band(5, 8, 1.00), *current_rates.factors["vehicle_age"][2:])
    proposed = replace(current_rates, factors={**current_rates.factors, "vehicle_age": split})
    table = rate_changes(current_rates, proposed)
    changed = table[table["changed"]]

    assert list(changed["band"]) == ["3 to under 5", "3 to under 8", "5 to under 8"]
    assert changed["current"].isna().sum() == 2 and changed["proposed"].isna().sum() == 1
