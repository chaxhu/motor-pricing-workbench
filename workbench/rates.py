"""Load and check the rating tables that turn a policy into a premium.

Why this module exists: the rates live in YAML files so a pricing analyst can
change them without touching code. That makes the YAML as important as the
code. A typo in a band edge or a missing factor would quietly misprice
policies, so every table is checked here before anything uses it.

All rates and factors in this project are illustrative assumptions. They are
not real Hastings Direct pricing.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

# The rating factors, in the order they are multiplied in the premium formula.
RATING_FACTORS: list[str] = [
    "driver_age",
    "vehicle_age",
    "annual_mileage",
    "driving_experience",
    "claims_last_5y",
]


class RateTableError(ValueError):
    """Raised when a rating table is missing fields or does not make sense."""


@dataclass(frozen=True)
class Band:
    """One row of a factor table. A value v is in this band if lower <= v < upper."""

    lower: float
    upper: float
    factor: float


@dataclass(frozen=True)
class RateTable:
    """A full set of rates: a base premium and the bands for each factor."""

    version: str
    base_premium: float
    factors: dict[str, tuple[Band, ...]]

    def edges(self, name: str) -> list[float]:
        """Return the band edges for a factor: every lower, then the last upper.

        This is the shape numpy.searchsorted and pandas.cut expect.
        """
        bands = self.factors[name]
        return [band.lower for band in bands] + [bands[-1].upper]

    def values(self, name: str) -> list[float]:
        """Return the factor value of each band, in band order."""
        return [band.factor for band in self.factors[name]]


def load_rate_table(path: str | Path) -> RateTable:
    """Read a rating table from a YAML file, build a RateTable, and check it."""
    path = Path(path)
    table = parse_rate_table(path.read_text(encoding="utf-8"), source=path)
    logger.info("Loaded rate table '%s' from %s", table.version, path)
    return table


def parse_rate_table(text: str, source: str | Path = "<text>") -> RateTable:
    """Build a RateTable from YAML text and check it. source names the text in error messages."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RateTableError(f"{source}: not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise RateTableError(f"{source}: expected a mapping at the top level")

    table = RateTable(
        version=_require_str(raw, "version", source),
        base_premium=_require_number(raw, "base_premium", source),
        factors=_parse_factors(raw.get("factors"), source),
    )
    check_rate_table(table)
    return table


def check_rate_table(table: RateTable) -> None:
    """Raise RateTableError if the table could misprice a policy.

    Checks that the base premium is positive, every factor is present with at
    least one band, no unknown factors are present, each band has
    lower < upper and a positive factor, and the bands join up with no gaps
    or overlaps.
    """
    if not table.base_premium > 0:
        raise RateTableError(f"{table.version}: base_premium must be > 0, got {table.base_premium}")

    missing = [name for name in RATING_FACTORS if name not in table.factors]
    if missing:
        raise RateTableError(f"{table.version}: missing factors {missing}")

    unknown = [name for name in table.factors if name not in RATING_FACTORS]
    if unknown:
        # An unknown factor would be ignored by pricing, so treat it as an error.
        raise RateTableError(f"{table.version}: unknown factors {unknown}")

    for name in RATING_FACTORS:
        _check_bands(table.version, name, table.factors[name])


def rate_changes(current: RateTable, proposed: RateTable) -> pd.DataFrame:
    """Line up two rate tables band by band, so the changes can be read side by side.

    One row for the base premium and one for each band. Bands are matched on
    factor and band edges, so a band that exists in only one table shows a
    blank on the other side. Returns factor, band, current, proposed and changed.
    """
    merged = _band_rows(current).merge(
        _band_rows(proposed), on=["order", "factor", "lower", "upper"], how="outer", suffixes=("_cur", "_prop")
    )
    merged = merged.sort_values(["order", "lower", "upper"], ignore_index=True)
    return pd.DataFrame(
        {
            "factor": merged["factor"],
            "band": [_band_label(f, lo, up) for f, lo, up in zip(merged["factor"], merged["lower"], merged["upper"])],
            "current": merged["value_cur"],
            "proposed": merged["value_prop"],
            # A blank on either side compares as not equal, so a missing band counts as changed.
            "changed": merged["value_cur"] != merged["value_prop"],
        }
    )


def _band_rows(table: RateTable) -> pd.DataFrame:
    """One row per band, plus the base premium first, with a sort order that follows the formula."""
    rows = [(0, "base_premium", 0.0, 0.0, table.base_premium)]
    for order, name in enumerate(RATING_FACTORS, start=1):
        rows += [(order, name, band.lower, band.upper, band.factor) for band in table.factors[name]]
    return pd.DataFrame(rows, columns=["order", "factor", "lower", "upper", "value"])


def _band_label(factor: str, lower: float, upper: float) -> str:
    """Describe a band in words, for example '15,000 to under 200,000'."""
    return "all policies" if factor == "base_premium" else f"{lower:,g} to under {upper:,g}"


def _check_bands(version: str, name: str, bands: tuple[Band, ...]) -> None:
    """Check one factor's bands are well formed and join up exactly."""
    where = f"{version}.{name}"
    if not bands:
        raise RateTableError(f"{where}: has no bands")

    for i, band in enumerate(bands):
        if not band.lower < band.upper:
            raise RateTableError(f"{where} band {i}: lower {band.lower} must be < upper {band.upper}")
        if not band.factor > 0:
            raise RateTableError(f"{where} band {i}: factor must be > 0, got {band.factor}")

    for i, (prev, nxt) in enumerate(zip(bands, bands[1:]), start=1):
        if nxt.lower > prev.upper:
            raise RateTableError(f"{where}: gap between {prev.upper} and {nxt.lower} before band {i}")
        if nxt.lower < prev.upper:
            raise RateTableError(f"{where}: band {i} starts at {nxt.lower}, overlapping the previous band ending at {prev.upper}")


def _parse_factors(raw: Any, source: str | Path) -> dict[str, tuple[Band, ...]]:
    """Turn the 'factors' section of the YAML into Band tuples."""
    if not isinstance(raw, dict):
        raise RateTableError(f"{source}: 'factors' must be a mapping of factor name to bands")

    factors: dict[str, tuple[Band, ...]] = {}
    for name, raw_bands in raw.items():
        if raw_bands is None:
            raw_bands = []
        if not isinstance(raw_bands, list):
            raise RateTableError(f"{source}: factor '{name}' must be a list of bands")
        factors[str(name)] = tuple(
            _parse_band(raw_band, f"{source}: {name} band {i}") for i, raw_band in enumerate(raw_bands)
        )
    return factors


def _parse_band(raw: Any, where: str) -> Band:
    """Turn one {lower, upper, factor} mapping into a Band."""
    if not isinstance(raw, dict):
        raise RateTableError(f"{where}: expected a mapping with lower, upper and factor")
    return Band(
        lower=_require_number(raw, "lower", where),
        upper=_require_number(raw, "upper", where),
        factor=_require_number(raw, "factor", where),
    )


def _require_str(raw: dict, key: str, where: Any) -> str:
    """Return raw[key] as a non-empty string, or raise RateTableError."""
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RateTableError(f"{where}: '{key}' must be a non-empty string")
    return value


def _require_number(raw: dict, key: str, where: Any) -> float:
    """Return raw[key] as a finite float, or raise RateTableError.

    Booleans are rejected on purpose: YAML reads 'yes' as True, and Python
    would otherwise treat True as the number 1.
    """
    if key not in raw:
        raise RateTableError(f"{where}: missing '{key}'")
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RateTableError(f"{where}: '{key}' must be a number, got {value!r}")
    if not math.isfinite(value):
        raise RateTableError(f"{where}: '{key}' must be finite, got {value!r}")
    return float(value)
