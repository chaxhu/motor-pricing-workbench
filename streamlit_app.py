"""A click-to-run web page for the workbench: streamlit run streamlit_app.py

Why this file exists: it lets someone see a pricing run without using the
command line. It is only a front end. Every number on the page comes from
workbench.pipeline.run_evaluation, the same function the command line run
uses, so the page and the CLI always agree. There is no pricing, validation
or reconciliation logic in this file.

The data is synthetic unless a CSV is uploaded, and the rates are
illustrative assumptions, not real Hastings Direct pricing.
"""

from __future__ import annotations

import io
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from workbench.compare import Check, explain_policy, largest_changes
from workbench.data import N_INJECTED_ROWS, POLICY_COLUMNS, generate_policies, inject_errors
from workbench.pipeline import (
    BAD_INPUT_ERRORS,
    DEFAULT_CURRENT_RATES,
    DEFAULT_PROPOSED_RATES,
    EvaluationResult,
    read_policies,
    run_evaluation,
)
from workbench.rates import RateTable, RateTableError, load_rate_table, parse_rate_table, rate_changes
from workbench.report import distribution_figure, format_pounds
from workbench.validate import SchemaError, ValidationResult

SEED = 42  # the CLI default, so the page matches python -m workbench.pipeline
BANNER = "Synthetic data and illustrative rates. Not real pricing."
SYNTHETIC = "Synthetic"
UPLOAD_CSV = "Upload policies CSV"
CONFIG_RATES = "Use config/rates_proposed.yaml"
UPLOAD_YAML = "Upload YAML"
CHANGED_STYLE = "background-color: rgba(255, 193, 7, 0.35)"  # amber, readable on light and dark themes


@st.cache_data(max_entries=20)
def load_rates(proposed_yaml: str | None, proposed_name: str) -> tuple[RateTable, RateTable]:
    """Load the current rates and the proposed rates (the config file, or uploaded YAML text).

    Raises RateTableError if a table fails the existing checks.
    """
    current = load_rate_table(DEFAULT_CURRENT_RATES)
    if proposed_yaml is None:
        return current, load_rate_table(DEFAULT_PROPOSED_RATES)
    return current, parse_rate_table(proposed_yaml, source=proposed_name)


@st.cache_data(max_entries=20, show_spinner="Running evaluation...")
def evaluate(
    n_policies: int, plant_errors: bool, policies_csv: bytes | None, proposed_yaml: str | None, proposed_name: str
) -> EvaluationResult:
    """Load or generate the policies and run the evaluation.

    Cached on the inputs, so a rerun with the same inputs is instant.
    policies_csv is the uploaded file's bytes, or None for synthetic policies.
    """
    current, proposed = load_rates(proposed_yaml, proposed_name)
    if policies_csv is None:
        policies = generate_policies(n_policies, current, seed=SEED)
        if plant_errors:
            policies = inject_errors(policies)
    else:
        policies = read_policies(io.BytesIO(policies_csv))
    return run_evaluation(policies, current, proposed)


def sidebar() -> str | None:
    """Draw the inputs. Clicking the button saves them, so later reruns use the same run.

    Returns the problem with an uploaded rate table, or None if there is none.
    """
    st.sidebar.header("Policies")
    source = st.sidebar.radio("Data source", [SYNTHETIC, UPLOAD_CSV], key="source")
    run: dict[str, Any] = {
        "n_policies": 0, "plant_errors": False, "policies_csv": None, "label": "",
        "proposed_yaml": None, "proposed_name": str(DEFAULT_PROPOSED_RATES.name),
    }
    if source == SYNTHETIC:
        run["n_policies"] = st.sidebar.slider("Number of policies", 1_000, 100_000, 10_000, step=1_000)
        run["plant_errors"] = st.sidebar.toggle(f"Plant {N_INJECTED_ROWS} known data errors")
        run["label"] = f"{run['n_policies']:,} generated policies, seed {SEED}, " + (
            f"{N_INJECTED_ROWS} planted errors." if run["plant_errors"] else "no planted errors."
        )
    else:
        upload = st.sidebar.file_uploader("Policies CSV", type="csv", key="policies_file")
        if upload is not None:
            run["policies_csv"], run["label"] = upload.getvalue(), f"Policies from {upload.name}."

    st.sidebar.header("Proposed rates")
    rates_source = st.sidebar.radio("Proposed rates", [CONFIG_RATES, UPLOAD_YAML], key="rates_source")
    problem = None
    if rates_source == UPLOAD_YAML:
        upload = st.sidebar.file_uploader("Rate table YAML", type=["yaml", "yml"], key="rates_file")
        if upload is not None:
            run["proposed_name"] = upload.name
            problem = check_uploaded_rates(upload.getvalue(), upload.name)
            if problem is None:
                run["proposed_yaml"] = upload.getvalue().decode("utf-8")

    ready = (
        (source == SYNTHETIC or run["policies_csv"] is not None)
        and (rates_source == CONFIG_RATES or run["proposed_yaml"] is not None)
    )
    if st.sidebar.button("Run evaluation", type="primary", disabled=not ready):
        st.session_state["run"] = run
    return problem


def check_uploaded_rates(content: bytes, name: str) -> str | None:
    """Run the existing rate table checks on an upload. Returns the problem found, or None."""
    try:
        load_rates(content.decode("utf-8"), name)
    except UnicodeDecodeError:
        return f"{name} is not a text file."
    except RateTableError as exc:
        return str(exc)
    return None


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


def show_rate_changes(current: RateTable, proposed: RateTable) -> None:
    st.subheader("Rate changes")
    table = rate_changes(current, proposed)
    st.caption(
        f"Rate table '{current.version}' against '{proposed.version}'. "
        f"{int(table['changed'].sum())} of {len(table)} rows changed, highlighted below. "
        "Illustrative rates only."
    )
    styled = (
        table.style
        .apply(lambda row: [CHANGED_STYLE if row["changed"] else ""] * len(row), axis=1)
        .format({"current": "{:.2f}", "proposed": "{:.2f}"}, na_rep="(no band)")
        .format({"changed": lambda changed: "Yes" if changed else ""})
    )
    st.dataframe(styled, hide_index=True)


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


def show_largest_changes(results: pd.DataFrame) -> None:
    """Show the largest rise and fall in pounds, each with a button that looks the policy up."""
    st.subheader("Largest rise and fall")
    cols = st.columns(2)
    for col, (kind, label) in zip(cols, [("rise", "Largest rise"), ("fall", "Largest fall")]):
        row = largest_changes(results)[kind]
        if row is None:
            col.metric(label, "None")
            continue
        col.metric(label, format_pounds(row["premium_change"], sign=True))
        col.caption(f"{row['policy_id']}, {row['pct_change']:+.2f}%, {format_pounds(row['current_premium'])} now")
        col.button(
            f"Look up {row['policy_id']}", key=f"lookup_{kind}",
            on_click=set_lookup, args=(row["policy_id"],),
        )


def set_lookup(policy_id: str) -> None:
    """Button callback: put a policy ID in the lookup box before the page redraws."""
    st.session_state["lookup_id"] = policy_id


def show_lookup(evaluation: EvaluationResult, current: RateTable, proposed: RateTable) -> None:
    st.subheader("Look up a policy")
    policy_id = st.text_input("Policy ID", key="lookup_id", placeholder="for example POL0000001").strip()
    if not policy_id:
        return

    results, rejected = evaluation.results, evaluation.validation.rejected
    match = results[results["policy_id"] == policy_id]
    if match.empty:
        reasons = rejected.loc[rejected["policy_id"] == policy_id, "reasons"]
        if reasons.empty:
            st.info(f"No policy with ID {policy_id}.")
        else:
            st.warning(f"{policy_id} was rejected, so it was not priced. Reasons: {'; '.join(reasons)}.")
        return

    row = match.iloc[0]
    st.caption(
        f"{policy_id}: {format_pounds(row['current_premium'])} now, {format_pounds(row['proposed_premium'])} proposed, "
        f"a change of {format_pounds(row['premium_change'], sign=True)} ({row['pct_change']:+.2f}%). "
        "premium = base premium x each factor, rounded to the penny."
    )
    breakdown = explain_policy(match, current, proposed)
    styled = (
        breakdown.style
        .apply(lambda r: [CHANGED_STYLE if r["changed"] else ""] * len(r), axis=1)
        .format({"current": "{:,.2f}", "proposed": "{:,.2f}"})
        .format({"changed": lambda changed: "Yes" if changed else ""})
    )
    st.dataframe(styled, hide_index=True)


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


def show_schema_error(exc: SchemaError) -> None:
    st.error(
        f"The CSV is missing {len(exc.missing)} required column(s): {', '.join(exc.missing)}. "
        f"It needs all of these: {', '.join(POLICY_COLUMNS)}."
    )


st.set_page_config(page_title="Motor pricing workbench", layout="wide")
st.warning(BANNER)
st.title("Motor pricing workbench")
rates_problem = sidebar()

if rates_problem:
    st.error(
        f"The uploaded rate table failed its checks, so nothing will be priced with it. "
        f"Fix the file and upload it again.\n\n{rates_problem}"
    )

if "run" not in st.session_state:
    st.info("Choose the settings in the sidebar, then click Run evaluation.")
    st.stop()

run = st.session_state["run"]
st.caption(run["label"])

try:
    evaluation = evaluate(
        run["n_policies"], run["plant_errors"], run["policies_csv"], run["proposed_yaml"], run["proposed_name"]
    )
except SchemaError as exc:
    show_schema_error(exc)
    st.stop()
except (*BAD_INPUT_ERRORS, UnicodeDecodeError) as exc:
    st.error(f"Could not run with these inputs: {exc}")
    st.stop()

if evaluation.stopped:
    show_stopped(evaluation)
    st.stop()

if not evaluation.reconciliation_passed:
    st.error("Reconciliation failed. Do not use these numbers until the failed checks are explained.")

show_headlines(evaluation)
show_reconciliation(evaluation.checks)
current_rates, proposed_rates = load_rates(run["proposed_yaml"], run["proposed_name"])
show_rate_changes(current_rates, proposed_rates)
show_chart(evaluation)
show_segments(evaluation.segments)
show_largest_changes(evaluation.results)
show_lookup(evaluation, current_rates, proposed_rates)
show_rejected(evaluation.validation)
