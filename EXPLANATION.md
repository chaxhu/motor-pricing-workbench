# How the Motor Pricing Workbench works

This page explains, in plain words, what problem this project solves for an insurer and how each part of the code does its job. You do not need to read code to follow it.

> Everything here uses **made up data and made up rates**. The customers are randomly generated and the rating factors are invented for illustration. None of it is any real insurer's pricing.

---

## 1. The business problem

A car insurer sets each customer's price using a **rating table**: a list of factors such as the driver's age, the car's age and how many miles they drive. Every so often the pricing team wants to change some of those factors. In this project, claims costs are rising, so the team proposes to:

- charge more for drivers doing **15,000 miles or more** a year (factor 1.25 becomes 1.35)
- charge more for drivers with **exactly one claim** in the last five years (1.25 becomes 1.32)
- give a small discount on cars aged **8 to 14 years** (0.95 becomes 0.92)

Before a change like this goes live, the business needs clear answers to some simple questions:

1. **Who is affected?** How many customers pay more, how many pay less, and how many see no change?
2. **By how much?** What does it do to the total premium, and what is the biggest rise any one customer would see?
3. **Where?** Which groups of customers (young drivers, high mileage drivers, and so on) take most of the change?
4. **Can we trust the answer?** Were any customer records broken or missing? Do all the totals add up, however you count them?

Getting this wrong is costly. If customers are charged the wrong amount, the insurer can lose money, upset customers and get into trouble with the regulator. So the answer must be **correct**, **repeatable** (the same inputs always give the same answer) and **quick enough** to try out many proposals.

This project answers all four questions, either with one command or from a small web page.

---

## 2. The big picture

Think of a restaurant kitchen.

- The **pricing team writes the recipe**: which factors matter and by how much. In this project the recipe is two simple text files, the current rates and the proposed rates.
- The **automation runs the kitchen**: it takes every order (every customer policy), checks the ingredients are good, follows the recipe exactly, and at the end of the night makes sure the money in the till matches what was served.

This project is the kitchen. Here is what happens when you run it:

```
Rating tables  ─┐
                ├─> 1. Check every policy ──> bad ones set aside, with reasons
Policies       ─┘          │
                           v
                 2. Price each policy on the current and proposed rates
                           │
                           v
                 3. Compare: what changes, for whom, and by how much
                           │
                           v
                 4. Reconcile: do all the totals agree?
                           │
                           v
                 5. Write a report a person can read
```

**One kitchen, two front doors.** Steps 1 to 4 live in one function, `run_evaluation`. Two things call it:

- the **command line** (`python -m workbench.pipeline`), which writes files to a folder, and
- the **web app** (`streamlit_app.py`), which shows the same results on a page.

Neither front door does any pricing itself. They only take the order and serve the result. So the command line and the app can never give different numbers, and a test proves it.

---

## 3. How each part works

The code lives in the `workbench/` folder. Each file has one job.

### The recipe: rating tables (`config/` and `workbench/rates.py`)

The rates are kept in two text files, `rates_current.yaml` and `rates_proposed.yaml`, so a pricing analyst can change a number without touching any code.

Each factor is split into **bands**. For example, driver age:

| Driver age | Factor |
| --- | ---: |
| 17 to 24 | 1.80 |
| 25 to 34 | 1.20 |
| 35 to 59 | 1.00 |
| 60 to 74 | 1.05 |
| 75 to 99 | 1.30 |

`rates.py` reads these files and **checks them before anything uses them**. It refuses a table if, for example, a band is missing, two bands overlap, there is a gap between bands (so some ages would have no price), or a factor is zero or negative. The same checks run on a rate file uploaded in the app.

It can also lay the two tables **side by side** and mark which bands changed (`rate_changes`). The app uses this to highlight the three changed bands.

*Why it matters:* a typo in the rate file would quietly misprice thousands of customers without anything crashing. Checking the recipe is as important as checking the cooking.

### The customers: synthetic policies (`workbench/data.py`)

We cannot use real customer data, so this file **creates a believable pretend portfolio**: mostly middle aged drivers, most cars a few years old, mileage centred around 8,000 a year with a few very high mileage drivers, and claims being fairly rare.

It uses a fixed **random seed**, which is like shuffling a deck of cards in exactly the same way every time. The same seed always gives exactly the same customers, so every run and every test can be repeated and checked.

It can also **plant six known mistakes** on purpose (a missing mileage, a negative mileage, an impossible amount of driving experience, a wrong premium, a 15 year old driver and a duplicated policy). This lets us prove the checks below really catch problems.

You can also bring your own policies as a CSV file, from the command line (`--input`) or by uploading it in the app.

### Step 1: checking every policy (`workbench/validate.py`)

Think of this as **passport control**. Every policy is checked before it may be priced. Anyone turned away is written down with the reason, **never silently thrown away**.

First it checks the file has every column it needs. If not, it stops and names the missing columns. The app shows that as a plain message, not a crash.

Then it checks each row for:

- missing values
- duplicate policy numbers (all copies are set aside, because we cannot tell which one is right)
- values outside sensible limits (for example a driver aged under 17, or negative mileage)
- impossible combinations (more years of driving than years since the age of 17)
- a premium of zero or less
- a stored premium that **does not match what the current rates say it should be**

That last check is important. If the price a customer actually pays does not match the rate table, something has gone wrong somewhere (a manual override, a missed update or a system fault), and the whole comparison would be built on the wrong starting point.

Rejected policies go into `rejected_policies.csv` with every reason they failed. If too many are rejected (more than 5% by default), the run stops, because the data is not fit to make decisions on.

### Step 2: pricing (`workbench/pricing.py`)

The premium formula is simple:

```
premium = base premium (£420)
          x driver age factor x vehicle age factor x annual mileage factor
          x driving experience factor x claims factor
```

**A worked example.** A 22 year old driver with 3 years' experience, a 10 year old car, 16,000 miles a year and one claim:

| | Current rates | Proposed rates |
| --- | ---: | ---: |
| Base premium | £420 | £420 |
| Driver age 22 | x 1.80 | x 1.80 |
| Car age 10 | x 0.95 | x **0.92** |
| 16,000 miles | x 1.25 | x **1.35** |
| 3 years' experience | x 1.10 | x 1.10 |
| One claim | x 1.25 | x **1.32** |
| **Premium** | **£1,234.41** | **£1,363.36** |

So this customer would pay £128.95 more, a rise of about 10.4%. These two numbers are worked out by hand and checked by the tests. The app's "Look up a policy" box shows exactly this kind of table for any policy.

There are **two pricing engines that must always agree**:

- The **fast engine** prices the whole portfolio at once using NumPy. It is quick but harder to check by eye.
- The **simple engine** prices one policy at a time, step by step, exactly like the worked example above. It is slow but easy to trust.

A test prices the same policies with both and requires them to match **to the penny**. If they ever disagree, one of them is wrong. This is like two cashiers counting the same till separately.

### Step 3: comparing (`workbench/compare.py` and `sql/impact_by_segment.sql`)

For every valid policy, the tool works out the proposed premium, the change in pounds and in percent, and whether it goes **up, down or stays the same**. It then puts each policy into simple groups:

- **age band** (17-24, 25-34, 35-59, 60-74, 75+)
- **mileage band** (under 5k, 5k-10k, 10k-15k, 15k+)
- **size of change** (down more than 5%, down 0-5%, no change, up 0-5%, up 5-10%, up more than 10%)

From that it produces:

- a **portfolio summary**: how many affected, total before and after, average and largest changes
- a **distribution**: how many policies fall in each size-of-change group
- a **segment table**, written in **SQL**, showing the impact for each age and mileage group

SQL is the language most insurers use for their data, so writing the segment analysis in SQL shows the same logic would run on a real company database.

Two smaller helpers here feed the app:

- `largest_changes` finds the single biggest rise and biggest fall **in pounds**. (In percent, thousands of policies tie, because only a few factor changes exist.)
- `explain_policy` breaks one policy's premium down factor by factor on both rate tables, so you can see exactly which factor moved the price.

### Step 4: reconciling (also in `workbench/compare.py`)

Comparing answers *"what changes?"*. Reconciling answers *"can we trust the answer?"*. It is like **balancing a till at the end of the day**: the same money is counted in different ways, and every count must agree.

Six checks are run:

1. **No policies lost:** the number of policies going in equals the number coming out.
2. **The pennies add up:** adding up every policy's change gives the same total as the portfolio change.
3. **SQL agrees with pandas:** the totals from the SQL segment table match the totals worked out in Python.
4. **Every policy is counted once:** the segment counts add up to the total.
5. **Every policy has a direction:** increases + decreases + unchanged = total.
6. **No broken prices:** no proposed premium is missing, zero or negative.

If any check fails, the report says **FAILED** in its title, the app shows a red error, and the command line ends with an error code, so nobody uses the numbers by accident.

### Step 5: the report (`workbench/report.py`)

This writes everything a reviewer needs onto one page, `report.md`:

- which rate tables were compared, and a reminder that the data is synthetic
- reconciliation: PASSED or FAILED, with each check
- how many policies were read, priced and rejected, and why
- the portfolio impact in pounds and percent
- the size-of-change table and a chart
- the segment table from SQL
- how long each step took

It also writes `summary.json` (the same headline numbers, for other programs to read) and CSV files with every policy's result. The report is built as text first (`render_report`), so the app can offer it as a download without saving anything to disk.

### Running it all: the pipeline (`workbench/pipeline.py`)

This holds `run_evaluation`, the shared core from section 2. It takes the policies and the two rate tables, runs steps 1 to 4, and hands back **one result object** with everything in it. The command line wraps it in **one command**:

```
python -m workbench.pipeline --inject-errors
```

Like a production line, each station does one job and times itself. At the end it returns a simple **exit code** that tells a scheduler or another system what happened:

| Code | Meaning |
| ---: | --- |
| 0 | Everything worked and the numbers reconcile |
| 1 | Too many bad policies, so the run stopped |
| 2 | The numbers did not reconcile, so do not use them |
| 3 | A bad input or setting, such as a broken rate file or a missing column |

### The web app (`streamlit_app.py`)

A small page for running the tool without the command line, built with **Streamlit** (a Python library that turns a script into a web page).

```
pip install -e ".[dev,ui]"
streamlit run streamlit_app.py      # then open http://localhost:8501
```

On the left you choose:

- **Policies:** generate a synthetic portfolio (1,000 to 100,000, with or without the six planted mistakes) or upload a CSV.
- **Proposed rates:** use the config file or upload your own YAML. A broken upload is caught by the rate checks and the Run button stays off.

After you press **Run evaluation**, the page shows, top to bottom:

1. the headline numbers
2. the six reconciliation checks, each pass or fail
3. the rate changes side by side, with changed bands highlighted
4. a chart of how much prices change
5. the SQL segment table
6. the largest rise and fall, each with a button to look it up
7. a **Look up a policy** box with the factor by factor breakdown
8. every rejected row with its reasons
9. download buttons for the CSVs, the report and the chart

The app holds **no pricing rules of its own**. It is a waiter, not a chef: it takes your choices to `run_evaluation` and shows what comes back. The downloaded files are byte for byte the same as the ones the command line writes.

It also **remembers recent runs** (Streamlit's cache), so pressing Run again with the same settings is instant.

### Measuring speed (`benchmark.py`)

The rule is **measure, don't guess**. This script times both pricing engines and the checking step on 1,000 up to 1,000,000 policies. The fast engine is about **40 times quicker** at large sizes (around 12 seconds against 0.3 seconds for a million policies on the last saved run). It also showed that pricing is no longer the slow part: moving data around (loading into SQL and writing large files) now takes the most time.

---

## 4. What a run tells the business

From the sample run in [docs/sample_report.md](docs/sample_report.md) (10,000 pretend policies with six planted mistakes):

| Business question | Answer |
| --- | --- |
| Was the data fit to use? | 10,001 records read, 7 set aside with reasons (0.07%), 9,994 priced |
| Who is affected? | 4,569 of 9,994 policies: 2,251 go up, 2,318 go down |
| By how much in total? | £5,260,523.38 today, £5,311,386.75 proposed: **+£50,863.37 (+0.97%)** |
| What is the worst case for a customer? | The largest rise is +14.05%, the largest fall is -3.16% |
| Who is that customer? | POL0002079: +£248.08 (£1,765.97 to £2,014.05), because of high mileage plus one claim |
| Where does the change land? | Mostly on drivers doing 15,000+ miles: every one of them pays more |
| Can we trust it? | All six reconciliation checks passed |

Each of the six planted mistakes was caught and listed with its reason, and both copies of the duplicated policy were set aside.

---

## 5. How we know it is right

| Safety net | What it protects against |
| --- | --- |
| **Rate table checks** | Gaps, overlaps or nonsense numbers in the recipe, including uploaded ones |
| **Validation** | Broken, impossible or duplicated customer records, or missing columns |
| **Two engines that must agree** | A hidden mistake in the fast pricing code |
| **Reconciliation** | Lost policies, double counting, or SQL and Python disagreeing |
| **App and command line must agree** | The web page showing different numbers from the report |
| **39 automated tests** | Any of the above breaking after a future change |
| **Automatic checks on GitHub** | Someone changing the code without running the tests |

The app has its own tests too. They run the page without a browser and check that it loads, that a CSV with a missing column gives a friendly message, that a broken rate file is caught, and that the policy lookup shows the breakdown.

**A real example.** While building this, the two engines disagreed by **1p on 21 of 2,000 policies**. The cause was rounding: a premium like £460.845 sits exactly on a half penny, and the two engines broke that tie differently. The fix was one clear rule, used everywhere: **half pennies always round up**. The test that caught it still runs, with no wiggle room, so the problem cannot quietly come back.

---

## 6. Interview cheat sheet

### The 30 second version

"Before a pricing change goes live, you need to know who it affects, by how much, and whether you can trust the numbers. This tool takes the current and proposed rate tables and a portfolio of policies. It checks every policy and sets bad ones aside with reasons. It prices every policy both ways, compares them, and then reconciles the totals six different ways. It finishes with a one page report, and there is a small web app on top of the same engine."

### A 5 minute demo plan

1. **Open the app** and point at the banner: synthetic data, illustrative rates.
2. **Turn on the six planted mistakes and press Run.** Point at "Rejected: 7" (six mistakes, but the duplicate removes both copies).
3. **Reconciliation:** six green ticks. "This is the till being balanced."
4. **Rate changes:** three highlighted bands. "This is the whole proposal."
5. **Largest rise:** click Look up. Show which factors changed and how the premium moved.
6. **Rejected rows:** every row is still there, with its reason. "Nothing is thrown away."
7. **Upload a broken rate file** (one band edge changed so there is a gap). The app names the gap and will not run.
8. **Close with the command line:** same numbers, plus an exit code for a scheduler.

### Questions you may be asked

**Why two pricing engines?**
The fast one is what you use. The simple one is easy to check by eye. If they ever disagree by even a penny, a test fails. It is the cheapest way to trust fast code.

**Why does a premium mismatch reject the policy?**
If the price on file is not what the current rates say, something outside the model has changed it. Comparing against a wrong starting point would give a wrong answer, so it is flagged instead.

**Why stop at 5% bad rows?**
A few bad rows are normal and are listed. Lots of bad rows means the data itself is broken, and any answer from it would be misleading.

**Why SQL as well as Python?**
Real insurer data usually sits in a database, so the segment logic is written the way it would be run there. Counting the totals in both SQL and Python is also one of the reconciliation checks.

**Why is the largest rise measured in pounds, not percent?**
With only a few factors changing, thousands of policies share the top percent change. In pounds there is one clear answer, and its percent is shown next to it.

**Why does the app have no pricing logic?**
So there is only one place the rules live. If the app did its own sums, one day it would disagree with the report. A test runs both and checks they match.

**How fast is it?**
About 0.3 seconds to price a million policies with the fast engine, around 40 times quicker than one by one. Pricing is now not the slow part; moving data into SQL and writing files is.

**What would you do next?**
Model customers leaving after a price rise, add fairness checks across customer groups, let the analyst pick the current rate table in the app, and keep a log of every run.

---

## 7. Words used on this page

| Word | Meaning |
| --- | --- |
| **Premium** | The price a customer pays for their insurance |
| **Rating factor** | Something about the customer or car that changes the price, such as driver age |
| **Band** | A range of values that share one factor, such as ages 25 to 34 |
| **Base premium** | The starting price before any factors are applied (£420 here) |
| **Portfolio** | All the policies the insurer holds |
| **Segment** | A group of customers, such as drivers aged 17 to 24 doing 15,000+ miles |
| **Validation** | Checking each record is complete and makes sense before using it |
| **Reconciliation** | Checking that totals counted in different ways all agree |
| **Vectorised** | Working on a whole column of numbers at once, instead of one row at a time, which is much faster |
| **Random seed** | A starting number that makes "random" data come out the same every time |
| **Exit code** | A number a program returns when it finishes, telling other systems if it worked |
| **Front end** | The part a person sees and clicks on, here the web app |
| **Streamlit** | A Python library that turns a script into a simple web page |
| **Cache** | Remembering a result so the same request does not have to be worked out again |

---

## 8. What this project does not do

- It uses **pretend data**, so the results show how the tool works, not the effect on a real book of business.
- It assumes **every customer stays**. In real life some customers would leave after a price rise.
- It does **not check regulatory rules**, such as fair value or fair treatment of different customer groups.
- It runs on **one computer**, which is fine for a million policies but not for a whole market.
- The app lets you upload **proposed** rates only. The current rates always come from the config file.

See the [README](README.md) for how to install and run it, and how it could grow into a team tool.
