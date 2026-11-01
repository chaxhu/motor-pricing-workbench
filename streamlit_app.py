"""A click-to-run web page for the workbench: streamlit run streamlit_app.py

Why this file exists: it lets someone see a pricing run without using the
command line. It is only a front end. Every number on the page comes from
workbench.pipeline.run_evaluation, the same function the command line run
uses, so the page and the CLI always agree. There is no pricing, validation
or reconciliation logic in this file.

The data is synthetic and the rates are illustrative assumptions, not real
Hastings Direct pricing.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from workbench.compare import Check
from workbench.data import N_INJECTED_ROWS, generate_policies, inject_errors
from workbench.pipeline import (
    BAD_INPUT_ERRORS,
    DEFAULT_CURRENT_RATES,
    DEFAULT_PROPOSED_RATES,
    EvaluationResult,
    run_evaluation,
)
from workbench.rates import load_rate_table
from workbench.report import distribution_figure, format_pounds
from workbench.validate import ValidationResult

SEED = 42  # the CLI default, so the page matches python -m workbench.pipeline
BANNER = "Synthetic data and illustrative rates. Not real pricing."


@st.cache_data(max_entries=20, show_spinner="Running evaluation...")
def evaluate(n_policies: int, plant_errors: bool) -> EvaluationResult:
    """Generate the policies and run the evaluation. Cached, so a rerun with the same inputs is instant."""
    current = load_rate_table(DEFAULT_CURRENT_RATES)
    proposed = load_rate_table(DEFAULT_PROPOSED_RATES)
    policies = generate_policies(n_policies, current, seed=SEED)
    if plant_errors:
        policies = inject_errors(policies)
    return run_evaluation(policies, current, proposed)


def sidebar() -> None:
    """Draw the inputs. Clicking the button saves them, so later reruns use the same run."""
    st.sidebar.header("Run settings")
    n_policies = st.sidebar.slider("Number of policies", 1_000, 100_000, 10_000, step=1_000)
    plant_errors = st.sidebar.toggle(f"Plant {N_INJECTED_ROWS} known data errors")
    if st.sidebar.button("Run evaluation", type="primary"):
        st.session_state["run"] = (n_policies, plant_errors)


def show_headlines(evaluation: EvaluationResult) -> None:
    summary = evaluation.summary
    cols = st.columns(5)
    cols[0].metric("Policies priced", f"{summary['policies']:,}")
    cols[1].metric("Rejected", f"{len(evaluation.validation.rejected):,}")
    cols[2].metric("Affected", f"{summary['policies_affected']:,}")
    cols[2].caption(f"{summary['increases']:,} up, {summary['decreases']:,} down")
    cols[3].metric("Total premium change", format_pounds(summary["total_change"], sign=True))
    cols[4].metric("Total premium change %", f"{summary['total_change_pct']:+.2f}%")


def show_reconciliation(checks: list[Check]) -> None:
    st.subheader("Reconciliation")
    table = pd.DataFrame(
        {
            "Result": ["✅ Pass" if c.passed else "❌ Fail" for c in checks],
            "Check": [c.name for c in checks],
            "Detail": [c.detail for c in checks],
        }
    )
    st.dataframe(table, hide_index=True)


def show_chart(evaluation: EvaluationResult) -> None:
    st.subheader("How premiums change")
    fig = distribution_figure(evaluation.results)
    st.pyplot(fig)
    plt.close(fig)


def show_segments(segments: pd.DataFrame) -> None:
    st.subheader("Impact by segment")
    st.caption("From sql/impact_by_segment.sql. Totals in pounds, biggest total change first.")
    money = st.column_config.NumberColumn(format="£%.2f")
    pct = st.column_config.NumberColumn(format="%.2f%%")
    st.dataframe(
        segments,
        hide_index=True,
        column_config={
            "current_total": money, "proposed_total": money, "total_change": money,
            "avg_pct_change": pct, "max_pct_change": pct,
        },
    )


def show_rejected(validation: ValidationResult) -> None:
    st.subheader("Rejected policies")
    if validation.rejected.empty:
        st.success("No policies were rejected.")
        return
    st.caption(f"{len(validation.rejected):,} rows ({validation.reject_rate:.2%}). A row can have more than one reason.")
    st.dataframe(validation.reason_counts().rename_axis("reason").reset_index(), hide_index=True)
    st.dataframe(validation.rejected, hide_index=True)


def show_stopped(evaluation: EvaluationResult) -> None:
    validation = evaluation.validation
    st.error(
        f"Run stopped: too many bad rows. {validation.reject_rate:.2%} of policies were rejected, "
        f"above the {evaluation.max_reject_rate:.0%} limit. Nothing was priced."
    )
    show_rejected(validation)


st.set_page_config(page_title="Motor pricing workbench", layout="wide")
st.warning(BANNER)
st.title("Motor pricing workbench")
sidebar()

if "run" not in st.session_state:
    st.info("Choose the settings in the sidebar, then click Run evaluation.")
    st.stop()

n_policies, plant_errors = st.session_state["run"]
st.caption(
    f"{n_policies:,} generated policies, seed {SEED}, "
    + (f"{N_INJECTED_ROWS} planted errors." if plant_errors else "no planted errors.")
)

try:
    evaluation = evaluate(n_policies, plant_errors)
except BAD_INPUT_ERRORS as exc:
    st.error(f"Bad input or config: {exc}")
    st.stop()

if evaluation.stopped:
    show_stopped(evaluation)
    st.stop()

if not evaluation.reconciliation_passed:
    st.error("Reconciliation failed. Do not use these numbers until the failed checks are explained.")

show_headlines(evaluation)
show_reconciliation(evaluation.checks)
show_chart(evaluation)
show_segments(evaluation.segments)
show_rejected(evaluation.validation)
