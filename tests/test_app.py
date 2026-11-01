"""Smoke test for the Streamlit app.

Why this file exists: it proves the page loads, the Run evaluation button
works, and the headline tiles appear, without opening a browser. It is
skipped if streamlit is not installed (pip install -e ".[ui]").
All rates are illustrative assumptions, not real Hastings Direct pricing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402  (only importable once streamlit is known to be there)

APP = Path(__file__).resolve().parent.parent / "streamlit_app.py"


def test_app_runs_and_shows_metrics() -> None:
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception
    assert not at.metric, "nothing should run before the button is clicked"

    at.sidebar.toggle[0].set_value(True)
    at.sidebar.button[0].click().run()

    assert not at.exception
    labels = [m.label for m in at.metric]
    assert labels == [
        "Policies priced", "Rejected", "Affected", "Total premium change", "Total premium change %",
        "Largest rise", "Largest fall",
    ]
    assert at.metric[1].value == "7"
    assert not at.error
