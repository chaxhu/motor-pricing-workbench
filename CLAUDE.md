# Motor Pricing Workbench

A small Python project that checks what a proposed motor insurance pricing change would do to a synthetic portfolio. It loads rating tables, loads or generates policies, validates them, prices every policy on the current and proposed rates, compares the two, reconciles the totals, and writes a report.

This is a portfolio project. Clarity and correctness matter more than features.

## Setup

```bash
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Project rules

### Code
- Python 3.10+, pandas and NumPy.
- Use type hints on all functions.
- Keep functions small, each doing one job.
- Every module starts with a docstring that explains, in plain English, why the module exists.
- Use `logging`, not `print`, in library code (anything inside `workbench/`).

### Data and pricing
- All rates and factors are illustrative assumptions. They are never real Hastings Direct pricing. Say so in comments and docs wherever rates appear.
- Never silently drop data. Bad rows are kept and flagged with a reason.
- Prefer vectorised pandas/NumPy for the main pricing path, but keep a simple row-by-row reference implementation so tests can check the two agree.

### Writing style (docs and comments)
- Simple, plain English.
- No em dashes.
- No buzzwords such as leverage, seamless, robust, delve.

### Working with Claude
- After each change, explain what was done in plain language, with an analogy, and say how to check it.
