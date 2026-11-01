"""Tests for the Streamlit app, run headless with AppTest.

Why this file exists: it proves the page loads, the Run evaluation button
works, bad uploads give a plain message instead of a crash, and the policy
lookup shows the factor breakdown, all without opening a browser. It is
skipped if streamlit is not installed (pip install -e ".[ui]").
All rates are illustrative assumptions, not real Hastings Direct pricing.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402  (only importable once streamlit is known to be there)

from tests.conftest import CONFIG_DIR  # noqa: E402

APP = Path(__file__).resolve().parent.parent / "streamlit_app.py"
BREAKDOWN_STEPS = [
    "base premium", "driver_age", "vehicle_age", "annual_mileage",
    "driving_experience", "claims_last_5y", "premium",
]


def _app() -> AppTest:
    return AppTest.from_file(str(APP), default_timeout=60).run()


def _run_synthetic(at: AppTest) -> None:
    at.sidebar.toggle[0].set_value(True)
    at.sidebar.button[0].click().run()


def _breakdown(at: AppTest) -> pd.DataFrame:
    """The lookup table is the only one with a 'step' column."""
    return next(df.value for df in at.dataframe if "step" in df.value.columns)


def test_app_runs_and_shows_metrics() -> None:
    at = _app()
    assert not at.exception
    assert not at.metric, "nothing should run before the button is clicked"

    _run_synthetic(at)

    assert not at.exception
    labels = [m.label for m in at.metric]
    assert labels == [
        "Policies priced", "Rejected", "Affected", "Total premium change", "Total premium change %",
        "Largest rise", "Largest fall",
    ]
    assert at.metric[1].value == "7"
    assert not at.error


def test_upload_missing_column_shows_friendly_error(policies: pd.DataFrame) -> None:
    at = _app()
    at.sidebar.radio(key="source").set_value("Upload policies CSV").run()
    csv = policies.drop(columns="vehicle_age").to_csv(index=False).encode()
    at.sidebar.file_uploader(key="policies_file").upload("policies.csv", csv, "text/csv").run()
    at.sidebar.button[0].click().run()

    assert not at.exception
    assert len(at.error) == 1
    assert "missing 1 required column(s): vehicle_age." in at.error[0].value
    assert not at.metric


def test_broken_rate_yaml_shows_check_failure() -> None:
    text = (CONFIG_DIR / "rates_proposed.yaml").read_text(encoding="utf-8")
    gappy = text.replace("{lower: 3, upper: 8,", "{lower: 4, upper: 8,")  # vehicle_age now skips 3 to 4

    at = _app()
    at.sidebar.radio(key="rates_source").set_value("Upload YAML").run()
    at.sidebar.file_uploader(key="rates_file").upload("proposed.yaml", gappy.encode(), "application/x-yaml").run()

    assert not at.exception
    assert len(at.error) == 1
    assert "failed its checks" in at.error[0].value
    assert "vehicle_age: gap between 3.0 and 4.0" in at.error[0].value
    assert at.sidebar.button[0].disabled, "Run evaluation must stay off until the rates pass"


def test_policy_lookup_shows_breakdown() -> None:
    at = _app()
    _run_synthetic(at)
    at.button(key="lookup_rise").click().run()

    assert not at.exception
    rise_id = at.text_input(key="lookup_id").value
    assert rise_id.startswith("POL")
    breakdown = _breakdown(at)
    assert breakdown["step"].tolist() == BREAKDOWN_STEPS
    # The largest rise must come from factors that changed, so its premium row changes too.
    assert breakdown.set_index("step").loc["premium", "changed"]

    at.text_input(key="lookup_id").set_value("POL0000001").run()
    assert not at.exception
    assert _breakdown(at)["step"].tolist() == BREAKDOWN_STEPS
